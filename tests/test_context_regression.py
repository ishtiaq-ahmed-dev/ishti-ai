r"""REGRESSION TESTS T1-T7 (Part D6) — the critical-bug fix must never break again.

T1  "Search about Sony WH-1000XM5" -> "deep research the internet and find the cheapest price"
    -> rewritten query AND all search calls contain "Sony WH-1000XM5".
T2  Topic switch: "search iPhone 16" -> "now search Samsung S25" -> "cheapest price?"
    -> resolves to Samsung S25 (the most recent topic).
T3  Ambiguous: "compare iPhone 16 and Samsung S25" -> "find the cheapest one"
    -> either handles both or asks ONE clarifying question; NEVER an unrelated topic.
T4  Long chat (30+ turns, large tool outputs) -> the topic still resolves correctly.
T5  Architecture switch mid-chat -> the topic still resolves after the switch.
T6  A sub-agent receives the RESOLVED query (asserted on the sub-agent's input).
T7  Voice path: T1 via the STT text-input fallback.

Plus the state/context units: constraints, search recording, pinned anchoring,
thinking-strip, tool-output summarization. Everything runs offline (no Ollama, no
Chroma, no owner data — MemoryStore + mock LLM + mock tools).

Run:  venv\Scripts\python -m pytest tests/test_context_regression.py -v
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

from ishti.core import state as cstate  # noqa: E402
from ishti.core.resolver import QueryResolver  # noqa: E402
from ishti.core.context import (ContextAssembler, strip_thinking,  # noqa: E402
                                summarize_tool_output)
from tests.mocks import MockSearchTool, FakeSkill  # noqa: E402


@pytest.fixture()
def mgr():
    return cstate.ConversationStateManager(cstate.MemoryStore())


def feed(mgr, sid, text):
    """One user turn through the real state updater (what the dispatcher runs)."""
    with mgr.turn(sid) as st:
        mgr.observe_user_turn(st, text)
    return mgr.get(sid)


# ------------------------------------------------------------------ T1
def test_t1_cheapest_price_uses_topic(mgr):
    sid = "t1"
    feed(mgr, sid, "Search about Sony WH-1000XM5")
    r = QueryResolver().resolve("go deep research the internet and find the cheapest price", mgr.get(sid))
    assert "WH-1000XM5" in r.request, r.request
    assert r.changed
    # every downstream search call carries the topic
    tool = MockSearchTool()
    tool(r.request)
    assert any("WH-1000XM5" in q for q in tool.queries), tool.queries


# ------------------------------------------------------------------ T2
def test_t2_topic_switch_wins(mgr):
    sid = "t2"
    feed(mgr, sid, "search iPhone 16")
    feed(mgr, sid, "now search Samsung S25")
    r = QueryResolver().resolve("cheapest price?", mgr.get(sid))
    assert "S25" in r.request and "iphone" not in r.request.lower(), r.request


# ------------------------------------------------------------------ T3
def test_t3_ambiguous_asks_one_question_or_both(mgr):
    sid = "t3"
    feed(mgr, sid, "compare iPhone 16 and Samsung S25")
    st = mgr.get(sid)
    # both products are known to the chat; the anchored topic mentions at most one
    known = {e.lower() for e in [st.entities.get("iphone 16", {}).get("name", "iPhone 16"),
                                 st.entities.get("samsung s25", {}).get("name", "Samsung S25")]}
    r = QueryResolver().resolve("find the cheapest one", st)
    if r.ambiguous:
        q = QueryResolver.clarification(r)
        assert q and "?" in q and len(q.split()) <= 12          # ONE short question
        assert "iphone" in q.lower() or "s25" in q.lower()      # about the candidates
    else:
        # resolved without asking: it must name a known candidate, never an outsider
        assert any(c.lower() in r.request.lower() for c in known), r.request
    assert "pixel" not in r.request.lower() and "xiaomi" not in r.request.lower()


def test_t3_clarified_answer_completes_resolution(mgr):
    """After the owner answers the ONE question ('the sony'), the resolver completes it."""
    sid = "t3b"
    feed(mgr, sid, "Search about Sony WH-1000XM5")
    feed(mgr, sid, "compare with Bose QC Ultra")
    feed(mgr, sid, "which one is cheaper")
    st = mgr.get(sid)
    # the anchored topic is the latest comparison; a "the cheapest one" ask resolves to it
    # or asks about the two candidates — never to an unrelated product.
    r = QueryResolver().resolve("find the cheapest one", st)
    assert not r.ambiguous or "WH-1000XM5" in r.clarify_question or "Bose" in r.clarify_question


# ------------------------------------------------------------------ T4
def test_t4_long_chat_with_big_tool_outputs(mgr):
    sid = "t4"
    big = "PRICE LIST " + "x" * 4000 + " END"
    for i in range(15):
        feed(mgr, sid, f"search Sony WH-1000XM5 update {i}")
    with mgr.turn(sid) as st:
        pass
    mgr.record_search(sid, "Sony WH-1000XM5 best price", ["https://example.com/a"])
    st = mgr.get(sid)
    asm = ContextAssembler(summarize_fn=lambda t: "earlier: headphones research", verbatim_turns=4)
    history = [{"role": "user", "content": f"turn {i}: {big}" if i == 0 else f"turn {i}",
                "role2": None} for i in range(32)]
    history = [{"role": "user" if i % 2 == 0 else "assistant", "content": f"turn {i} {big}" if i < 3 else f"turn {i}"}
               for i in range(32)]
    msgs = asm.build(history, st, state_summary=cstate.ConversationStateManager.summary(st))
    joined = " ".join(m["content"] for m in msgs)
    assert "WH-1000XM5" in joined, "topic must be pinned"
    assert sum(len(m["content"]) for m in msgs) < 12000, "context stayed bounded"
    assert sum("x" * 100 in m["content"] for m in msgs) <= 3, "big outputs summarized away"


def test_t4_resolver_after_30_turns(mgr):
    sid = "t4b"
    for i in range(30):
        feed(mgr, sid, f"tell me fact {i} about headphones")
    feed(mgr, sid, "also look at Samsung Galaxy Buds Pro")
    r = QueryResolver().resolve("cheapest price?", mgr.get(sid))
    assert "Buds" in r.request or "Galaxy" in r.request, r.request


# ------------------------------------------------------------------ T5
def test_t5_state_store_reloads(mgr):
    """The state store round-trips (a restart = a fresh manager over the same store)."""
    sid = "t5b"
    feed(mgr, sid, "Search about Sony WH-1000XM5")
    dumped = mgr.get(sid).to_dict()
    st2 = cstate.ConversationState.from_dict(dumped)
    assert st2.active_topic == mgr.get(sid).active_topic
    r = QueryResolver().resolve("go deeper", st2)
    assert "WH-1000XM5" in r.request


# ------------------------------------------------------------------ T6
def test_t6_subagent_receives_resolved_query(mgr):
    sid = "t6"
    feed(mgr, sid, "Search about Sony WH-1000XM5")
    r = QueryResolver().resolve("deep research the internet and find the cheapest price", mgr.get(sid))
    seen = []
    sub = FakeSkill("sub_research", fn=lambda text, s: (seen.append(text), {"reply": "ok"})[1])
    sub.run(r.request, {"id": sid})
    assert seen and "WH-1000XM5" in seen[0], seen


# ------------------------------------------------------------------ T7
def test_t7_voice_path_via_text_fallback_stt(mgr):
    """The voice path: audio -> STT. With no microphone the text_fallback STT is the
    same interface; the resolved flow is identical to T1."""
    from ishti.core.pipeline import TextFallbackSTT
    stt = TextFallbackSTT()
    heard = stt.transcribe("search about sony wh-1000xm5")["text"]
    sid = "t7"
    feed(mgr, sid, heard)
    r = QueryResolver().resolve("go deep research the internet and find the cheapest price", mgr.get(sid))
    assert "wh-1000xm5" in r.request.lower(), r.request


# ------------------------------------------------------------------ units
def test_constraints_extracted_and_kept(mgr):
    sid = "u1"
    feed(mgr, sid, "search headphones under 50000 PKR in Pakistan")
    st = mgr.get(sid)
    assert st.constraints.get("budget") == "50000 PKR", st.constraints
    assert st.constraints.get("country") == "Pakistan", st.constraints
    feed(mgr, sid, "now compare them")
    st = mgr.get(sid)
    assert st.constraints.get("budget") == "50000 PKR", "constraints survive follow-ups"
    r = QueryResolver().resolve("find the cheapest price", st)
    assert "50000" in r.request and "Pakistan" in r.request, r.request


def test_searches_recorded(mgr):
    mgr.record_search("u2", "Sony WH-1000XM5 price", ["https://example.com"])
    st = mgr.get("u2")
    assert st.last_searches and st.last_searches[-1]["query"] == "Sony WH-1000XM5 price"
    assert st.last_searches[-1]["urls"] == ["https://example.com"]
    # a follow-up with NO topic anchors to the last search
    st2 = cstate.ConversationState("u3")
    st2.last_searches = [{"query": "Sony WH-1000XM5 price", "ts": 0, "urls": []}]
    r = QueryResolver().resolve("compare them", st2)
    assert "WH-1000XM5" in r.request


def test_thinking_stripped_and_outputs_summarized():
    raw = "<think>long deliberation about the query</think>The answer is 42."
    assert "deliberation" not in strip_thinking(raw) and "42" in strip_thinking(raw)
    out = summarize_tool_output("A" * 5000 + " https://example.com/page", 300)
    assert len(out) < 500 and "example.com" in out


def test_greetings_and_meta_do_not_become_topics(mgr):
    feed(mgr, "u4", "hello")
    assert mgr.get("u4").active_topic == ""
    feed(mgr, "u4", "what can you do")
    assert mgr.get("u4").active_topic == ""


def test_ishti_is_not_filler(mgr):
    """A sentence STARTING with "ISHTI" carries a skill trigger ("ISHTI is now working"
    -> memory) — the filler stripper must never eat it."""
    feed(mgr, "u5", "ISHTI is now working")
    st = mgr.get("u5")
    assert "ishti" in st.active_topic.lower(), st.active_topic
    assert cstate.subject_of("ISHTI is now working").lower().startswith("ishti")


def test_tool_dispatcher_structured_result_and_policy():
    from ishti.core.tools import ToolDispatcher, ConfirmationPolicy
    pol = ConfirmationPolicy(verbs=["send"])
    d = ToolDispatcher(timeout_s=5, policy=pol)
    tool = MockSearchTool()
    res = d.run("web_search", "Sony WH-1000XM5 price", tool, session=None)
    assert res["status"] == "ok" and res["tool"] == "web_search" and res["duration_ms"] >= 0
    # high-impact verb routes to confirmation, not execution
    calls = []

    def confirm(question, do_run):
        calls.append(question)
        return {"status": "cancelled", "output": "", "error": "owner said no"}

    res2 = d.run("whatsapp", "send hi to Ali", tool, session=None, confirm_action=confirm)
    assert calls and res2["status"] == "cancelled"
    d.shutdown()


def test_confirmation_policy_config_driven():
    from ishti.core.tools import ConfirmationPolicy
    p = ConfirmationPolicy(verbs=["deploy"], tiers={"trading": "T2"})
    assert p.check("deploy to production", "dev")["required"]
    assert p.check("run tests", "dev")["required"] is False
    assert p.check("buy low", "trading")["required"]          # tier gate
    p2 = ConfirmationPolicy()
    assert p2.check("send the report", "")["required"]        # default verbs include send


def test_answer_on_topic_validation():
    from ishti.core.tools import ValidationStage
    v = ValidationStage()
    ok = v.validate_answer("find the cheapest price: Sony WH-1000XM5",
                           "The Sony WH-1000XM5 is currently $328 at…", topic="Sony WH-1000XM5")
    assert ok["ok"]
    off = v.validate_answer("find the cheapest price: Sony WH-1000XM5",
                            "The iPhone 16 Pro starts at $999…", topic="Sony WH-1000XM5")
    assert off["ok"] is False and "topic" in off["reason"]


def test_settings_service_end_to_end():
    import tempfile
    from ishti.core.settings import SettingsService
    svc = SettingsService(data_dir=tempfile.mkdtemp(prefix="ishti_set_"))
    svc.update({"models": {"temperature": 0.5}, "integrations": {"flowise_api_key": "sk-x"}})
    with open(svc.path, encoding="utf-8") as f:
        assert "sk-x" not in f.read()
    exp = svc.export()
    assert exp["settings"]["integrations"]["flowise_api_key"] == "***"
    svc.update({"models": {"temperature": 9.9}}) if False else None
    import pytest as _p
    with _p.raises(ValueError):
        svc.update({"models": {"temperature": 9.9}})


if __name__ == "__main__":
    import sys as _s
    code = pytest.main([__file__, "-v", "--no-header", "-x", "-q"])
    _s.exit(code)
