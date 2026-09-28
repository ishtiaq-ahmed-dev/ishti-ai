"""ARCH2 tests: everything that runs WITHOUT Ollama / Chroma / a GPU.

Covers the parts the user's architecture spec pins down exactly:
  - registry: one active architecture, hot switch, persistence to config.json, command parsing
  - memory: four layers, unified rows, no duplicate observations, supersede-not-overwrite,
    configurable thresholds, decay demotes never deletes, privacy export, entities/links
  - retrieval: relevant-only context, procedure matching, budget
  - pipeline (offline parts): tool-call JSON parsing, spec-24 confirmation trigger
  - consolidate (offline parts): memory commands (what do you remember / who is / forget /
    remember that), explicit-vs-observed kin rule

Needs a real model: pipeline passes 1/2, consolidation extraction, retrieval vector hits
(see MAC_CHANGES.md's PC checklist - run on the owner's machine with Ollama running).
"""
import json
import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from ishti.arch2 import registry  # noqa: E402

# ---------------------------------------------------------------- 0. scratch environment
TMP = tempfile.mkdtemp(prefix="ishti_arch2_test_")
CFG_PATH = os.path.join(ROOT, "ishti", "config.json")
with open(CFG_PATH, encoding="utf-8") as f:
    REAL_CFG = f.read()

import ishti.arch2.memory as memory  # noqa: E402
import ishti.arch2.retrieval as retrieval  # noqa: E402
import ishti.arch2.consolidate as consolidate  # noqa: E402
import ishti.arch2.pipeline as pipeline  # noqa: E402

memory.db_path = lambda: os.path.join(TMP, "arch2_memory.db")
memory._conn = None
retrieval.memory = memory
consolidate.memory = memory


def setup_module(module=None):
    pass


def teardown_module(module=None):
    with open(CFG_PATH, "w", encoding="utf-8") as f:  # restore the owner's config byte-for-byte
        f.write(REAL_CFG)


# ---------------------------------------------------------------- 1. registry
def test_registry_switch_and_persist():
    d = registry.set_active("arch2", by="test")
    assert d["active"] == "arch2"
    disk = json.load(open(CFG_PATH, encoding="utf-8"))
    assert disk["arch"]["active"] == "arch2", "the switch must survive a restart"
    assert registry.active() == "arch2"
    d = registry.set_active("1", by="test")  # alias
    assert d["active"] == "arch1"
    try:
        registry.set_active("arch9")
        assert False, "unknown arch must raise"
    except ValueError:
        pass


def test_registry_command():
    d = registry.set_active("arch1", by="test")
    assert registry.handle_command("use arch 2") is not None
    assert registry.active() == "arch2"
    registry.set_active("arch1", by="test")
    assert registry.handle_command("what's the weather") is None, "non-command must fall through"
    assert registry.handle_command("arch " * 5 + "long " * 10) is None, "long sentences aren't commands"
    # "architecture" contains "arch" as a substring but ARCH_WORD needs the word boundary
    assert registry.handle_command("what architecture do you use") is None


# ---------------------------------------------------------------- 2. memory layers
def test_four_layers_and_thresholds():
    m1 = memory.semantic("User prefers concise email replies.", source="explicit_user",
                         confidence=0.95, importance=0.8)
    assert m1["id"].startswith("mem_") and m1["type"] == "semantic"
    # re-stating the same memory strengthens it instead of duplicating
    memory.semantic("User prefers concise email replies.", source="explicit_user")
    rows = memory.search("concise email")
    assert len(rows) == 1 and rows[0]["times_observed"] == 2
    # explicit source wins over inference confidence
    assert rows[0]["confidence"] >= 0.95
    assert memory.episodic("User decided to use Qwen3 as the main model.", importance=0.7)["id"]
    p = memory.procedural("Email workflow",
                          steps=["Read email", "Draft response", "Show user", "Wait for approval", "Send"],
                          keywords=["email", "reply", "draft"])
    assert "3. Show user" in p["content"], "procedure content carries the numbered steps"


def test_observations_promote_by_configurable_threshold():
    for _ in range(3):
        memory.observe("User asks to make the email shorter.")
    r = memory.search("make the email shorter", mtype="observation")[0]
    assert r["times_observed"] == 3
    assert memory.strength(2) == "temporary" and memory.strength(4) == "weak"
    assert memory.strength(7) == "probable" and memory.strength(15) == "strong"
    # below threshold: still just an observation
    assert all("email shorter" not in x["content"] for x in memory.search(mtype="semantic"))
    # push past the threshold -> promotion
    memory.observe("User asks to make the email shorter.")
    promoted = memory.promote_observations()
    assert any("email shorter" in p["content"] for p in promoted)
    assert not [x for x in memory.search(mtype="observation") if x["status"] == "active"]


def test_supersede_and_forget():
    a = memory.semantic("User prefers Chrome.", source="explicit_user", confidence=1.0)
    b = memory.supersede(a["id"], "User switched to Firefox.")
    assert memory.get(a["id"])["status"] == "superseded", "old memory stays, marked superseded"
    assert memory.get(b["id"])["status"] == "active"
    assert memory.forget(b["id"]) == 1 and memory.get(b["id"])["status"] == "forgotten"
    assert memory.search("Firefox") == []


def test_decay_never_deletes_and_respects_importance_floor():
    low = memory.semantic("User tried a wallpaper app once.", importance=0.3, confidence=0.4)
    high = memory.semantic("User's brother is Ahmed.", source="explicit_user", importance=0.9, confidence=1.0)
    old = time.time() - 90 * 86400
    c = memory.conn()
    with c:
        c.execute("UPDATE memories SET last_confirmed_at=? WHERE id IN (?,?)", (old, low["id"], high["id"]))
    flagged = memory.decay(now=time.time())
    ids = {x["id"] for x in flagged}
    assert low["id"] in ids and high["id"] not in ids, "high-importance memories are never decayed"
    assert memory.get(low["id"])["status"] == "needs_confirmation"
    assert memory.get(low["id"])["content"], "row still exists - decay flags, never deletes"


def test_entities_and_links():
    e = memory.upsert_entity("Ahmed", relationship="friend", note="works on Project X", aliases=["Ahmad"])
    e2 = memory.upsert_entity("Ahmed", relationship="friend", note="frequently contacts the owner")
    assert e["id"] == e2["id"] and len(json.loads(e2["notes"])) == 2
    assert memory.find_entity("ahmad")["name"] == "ahmed", "aliases resolve"
    m = memory.semantic("Ahmed prefers cricket talk.", entity_ids=[e["id"]])
    memory.link(m["id"], e["id"])
    assert memory.entities_for(m["id"])[0]["name"] == "ahmed"
    card = memory.entity_card("Ahmed")
    assert "Ahmed" in card and "friend" in card


def test_privacy_export():
    memory.semantic("User had knee surgery in March.", source="conversation")
    memory.semantic("User prefers professional short emails.", source="explicit_user")
    client_view = memory.export("client").lower()
    owner_view = memory.export("owner").lower()
    assert "surgery" not in client_view, "health facts never reach client-facing context"
    assert "surgery" in owner_view
    assert "professional short emails" in client_view


# ---------------------------------------------------------------- 3. retrieval
def test_retrieval_relevant_only():
    memory.procedural("File organization",
                      steps=["When downloading project files, place them in the Projects directory"],
                      keywords=["download", "project", "files", "organize"])
    ctx = retrieval.assemble_context("draft an email reply")
    assert "Email workflow" in ctx, "procedure keyword-matches the request"
    assert "RELEVANT PROCEDURES" in ctx
    empty = retrieval.assemble_context("quantum chromodynamics lattice gauge theory constants")
    assert empty == "", f"an unrelated request must pull nothing, got: {empty[:120]}"
    ctx2 = retrieval.assemble_context("shorten this email for me")
    assert ctx2 and len(ctx2) <= memory.MEM().get("context_budget_chars", 2600) + 200


# ---------------------------------------------------------------- 4. pipeline offline parts
def test_pipeline_tool_parsing_and_safety():
    call = pipeline._parse_tool_call('{"tool": "web_search", "input": "weather in Peshawar"}')
    assert call == {"tool": "web_search", "input": "weather in Peshawar"}
    assert pipeline._parse_tool_call("Sunny, 34 degrees.") is None
    assert pipeline._parse_tool_call('{"tool": "", "input": "x"}') is None
    assert pipeline._parse_tool_call('not json {') is None
    assert pipeline._needs_confirmation("send the report to Ahmed")
    assert pipeline._needs_confirmation("delete that file")
    assert not pipeline._needs_confirmation("what is the capital of Pakistan")
    assert not pipeline._needs_confirmation("summarize this document for me")


# ---------------------------------------------------------------- 5. memory commands
def test_memory_commands():
    memory.semantic("User is Ishtiaq.", source="explicit_user", confidence=1.0, importance=0.9)
    assert "Ishtiaq" in (consolidate.handle_command("what do you remember about me") or "")
    r = consolidate.handle_command("remember that I prefer standing desks")
    assert r and "standing desks" in r
    rows = memory.search("standing desks")
    assert rows and rows[0]["source"] == "explicit_user"
    # store the person this command's assertion needs (earlier tests may or may not have run)
    memory.upsert_entity("Ahmed", relationship="friend", note="works on Project X", aliases=["Ahmad"])
    memory.semantic("Ahmed prefers cricket talk.")
    r = consolidate.handle_command("who is Ahmed")
    assert r and "friend" in r, r
    memory.observe("User tends to ask for shorter replies.")  # an active pattern to be listed
    r = consolidate.handle_command("what have you learned from my behavior")
    assert r and "shorter replies" in r, r
    r = consolidate.handle_command("forget standing desks")
    assert r and not memory.search("standing desks")
    # the way people actually say it: a substring of the stored content
    r = consolidate.handle_command("forget concise email replies")
    assert r and not memory.search("concise email")
    assert consolidate.handle_command("tell me a joke") is None, "non-commands fall through"


def test_consolidate_apply_explicit_vs_observed():
    # explicit preference in the request
    counts = consolidate.apply({"semantic": ["User prefers dark mode."] * 1},
                               request="remember that I prefer dark mode")
    assert counts["semantic"] == 1
    row = memory.search("dark mode")[0]
    assert row["source"] == "explicit_user" and row["confidence"] >= 0.9
    # inference: same fact but the owner never said it -> low confidence semantic
    counts = consolidate.apply({"semantic": ["User enjoys cricket."]}, request="tell me sports news")
    row = memory.search("cricket")[0]
    assert row["source"] == "inference" and row["confidence"] < 0.9
    # kin from chatter must NOT become a relationship
    counts = consolidate.apply({"entities": [{"name": "Ali", "relationship": "brother", "kind": "person"}]},
                               request="Ali and I had lunch today")
    e = memory.find_entity("ali")
    assert e["relationship"] == "unknown", "kin is never inferred (spec 10)"
    # kin from an explicit statement is stored with high confidence
    consolidate.apply({"entities": [{"name": "Sara", "relationship": "sister", "kind": "person"}]},
                      request="Sara is my sister")
    assert memory.find_entity("sara")["relationship"] == "sister"
    # procedural proposal lands with its steps
    consolidate.apply({"procedural": [{"name": "Coding workflow",
                                       "steps": ["Open VS Code", "Inspect existing code", "Run tests"]}]},
                      request="how I like coding done")
    ctx = retrieval.assemble_context("open the project and run tests")
    assert "Coding workflow" in ctx


def test_unified_memory_object_shape():
    row = memory.search(limit=1)[0]
    for field in ("id", "type", "content", "importance", "confidence", "source", "created_at",
                  "updated_at", "last_accessed_at", "last_confirmed_at", "status"):
        assert field in row, f"unified memory object missing {field} (spec 21)"


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"  ok  {fn.__name__}")
        except AssertionError as exc:
            failed += 1
            print(f"FAIL  {fn.__name__}: {exc}")
    print(f"[test] arch2 memory: {len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
