"""Memory skill: front-end to the exact fact store.

- "Project X is on D: at D:\\work\\x" / "I moved X to D:\\new"  -> upsert X.location
- "I finished X" / "X is now working" / "this is now broken"   -> upsert X.status
- "where is my X?"   -> direct lookup; if unknown, search D:, Desktop, Downloads, confirm, store
- "forget X", "I moved to Lahore", "remember <any fact>" (LLM extracts entity/attr/value)
"""
import json
import os
import re
import shutil
import subprocess
import time
from collections import deque

from ishti import CONFIG, llm
from ishti.facts import get_facts, norm
from ishti.skills import ask_first, trace

try:  # best-effort failure log (never breaks the module if logging is unavailable)
    from ishti.core.logutil import swallowed
except Exception:  # pragma: no cover
    def swallowed(*_a, **_k):
        return None

I = re.I
STATUS = {"finished": "finished", "completed": "finished", "complete": "finished", "done": "finished",
          "started": "in progress", "in progress": "in progress", "paused": "paused", "on hold": "paused",
          "abandoned": "abandoned", "shipped": "deployed", "deployed": "deployed", "live": "deployed",
          "working": "working", "broken": "broken"}
_SW = "|".join(sorted(STATUS, key=len, reverse=True))
DRIVE = r"(?=[A-Za-z]:)"
# relationship words, so "forget everything about my cousin Bilal" is a PERSON forget, not a fact forget
REL_WORDS = ("brother", "sister", "father", "mother", "son", "daughter", "cousin", "uncle", "aunt",
             "nephew", "niece", "grandfather", "grandmother", "friend", "colleague", "classmate",
             "manager", "boss", "supervisor", "client")
FORGET_PERSON = re.compile(r"^forget\s+(?:everything|all)?\s*(?:about|of)?\s*(?:my\s+)?"
                           r"(?:(?P<rel>" + "|".join(REL_WORDS) + r")(?:'s|s)?\s+)?"
                           r"(?P<name>[A-Za-z][\w'-]*)\s*[.!]?$", re.I)
# "Delete all data about my <role/relationship>, I want it gone completely" (checklist 237) - a FULL
# scoped deletion, not a hidden/soft forget. The target may be named by role ("my cardiologist
# collaborator"), so it is resolved against known people before anything is removed.
DELETE_ALL = re.compile(
    r"^\s*(?:please\s+)?(?:delete|remove|erase|wipe|purge|get\s+rid\s+of|destroy)\s+"
    r"(?:all\s+)?(?:the\s+|my\s+|any\s+)?(?:data|info|information|records?|memory|memories|documents?|details?|everything)"
    r"\s+(?:about|on|of|for|regarding|related\s+to)\s+(?P<target>.+?)"
    r"(?:\s*[,.;-]?\s*(?:i\s+(?:want|need)|please\s+make|make|get|have)\s+it\s+(?:gone|removed|deleted)\b.*)?\s*[.!]?$", I)
RX = {
    "move": re.compile(rf"\bmoved\s+(?P<e>.+?)\s+(?:to|into)\s+{DRIVE}", I),
    "moved_passive": re.compile(rf"^(?P<e>.+?)\s+(?:has\s+been|was|got)\s+moved\s+(?:to|into)\s+{DRIVE}", I),
    "at": re.compile(rf"^(?:remember(?:\s+that)?\s+)?(?P<e>.+?)\s+(?:is|are|lives|sits)\s+(?:now\s+)?"
                     rf"(?:located\s+|stored\s+|saved\s+|kept\s+)?(?:on|at|in)\s+{DRIVE}", I),
    "status_i": re.compile(r"^i\s+(?:(?:just|have|'ve|already)\s+)*(?P<s>finished|completed|started|paused|"
                           r"abandoned|shipped|deployed)\s+(?:working\s+on\s+)?(?P<e>.+?)[.!]?$", I),
    # requires "now": keeps "my code is broken, help me" out of the store
    "status_is": re.compile(rf"^(?P<e>.+?)\s+is\s+now\s+(?P<s>{_SW})[.!]?$", I),
    # "where is my order/package" is order tracking (email), not something the owner stored
    "where": re.compile(r"^(?!where(?:'s|\s+is|\s+are)\s+(?:my\s+)?(?:online\s+)?(?:orders?|packages?|parcels?|deliver(?:y|ies)|shipments?)\b)"
                        r"(?:where(?:'s|\s+is|\s+are)|where\s+did\s+i\s+(?:put|save|keep|store)|"
                        r"what(?:'s|\s+is)\s+the\s+(?:path|location)\s+(?:of|for|to)|"
                        r"(?:which|what)\s+(?:folder|directory|drive)\s+(?:did\s+i\s+)?(?:put|save|keep|store|is|are)(?:\s+(?:in|on))?)"
                        r"\s+(?P<e>.+?)(?:\s+(?:in|on))?\s*\??$", I),
    "status_q": re.compile(r"^(?:what(?:'s|\s+is)\s+the\s+)?status\s+(?:of|on)\s+(?P<e>.+?)\s*\??$", I),
    # "Actually, forget Chrome - for work I now prefer Edge" is a memory update (forgetting a preference,
    # checklist #92). The leading discourse marker is optional, so plain "forget X" and "Forget that"
    # (checklist #91) still match.
    "forget": re.compile(r"^\s*(?:(?:actually|well|look|listen|ok|okay|so|right|hey),?\s+)?forget\s+(?:about\s+)?(?P<e>.+?)[.!]?$", I),
    "city": re.compile(r"^i\s+(?:now\s+)?(?:live|am\s+living|have\s+moved|moved)\s+(?:to|in)\s+"
                       r"(?P<c>[A-Za-z][A-Za-z .'-]{1,40}?)[.!]?$", I),
    "remember": re.compile(r"^remember\b", I),
    "open": re.compile(r"^(?:open|show)\s+(?:up\s+)?(?P<e>.+?)(?:\s+(?:in|with)\s+(?P<app>vs\s*code|code|"
                       r"(?:file\s+)?explorer|terminal))?[.!]?$", I),
}
# ponytail: word-list heuristic for "is this where-question about the owner's own stuff?".
# Upgrade path: ask Laya a noul question once the typed-decisions checkpoint is fine-tuned.
PERSONAL = re.compile(r"\b(my|our|project|folder|file|repo|directory|code|app|document|docs?|notes?|"
                      r"put|save|saved|keep|kept|store|stored)\b", I)
PRONOUNS = {"this", "it", "that", "this project", "that project", "the project"}
YES = {"yes", "y", "yeah", "yep", "correct", "right", "that one", "ok", "okay", "sure"}
NO = {"no", "n", "nope", "none", "neither", "wrong"}
SKIP = {"venv", ".venv", "node_modules", "__pycache__", "site-packages", "appdata", "windows",
        "program files", "program files (x86)", "programdata", "chrome_profile", "mcp_browser_profile"}


def R(reply):
    return {"reply": reply, "sources": []}


def squash(s):
    return re.sub(r"[^a-z0-9]", "", s.lower())


def extract_path(t):
    ms = list(re.finditer(r"[A-Za-z]:(?:[\\/]|(?=\s|$|[.,;!?]))", t))
    if not ms:
        return None
    s = t[ms[-1].start():]
    end = re.search(r",|;|\s+(?:and|but|so)\s|\.(?:\s|$)|[?!](?:\s|$)", s[2:])
    s = (s[:2 + end.start()] if end else s).strip().strip("\"'`").rstrip(".,;!?")
    return os.path.normpath(s + "\\" if re.fullmatch(r"[A-Za-z]:", s) else s)


def _saved_location(text):
    m = RX["open"].match(text.strip())
    return m and get_facts().get(m["e"]).get("location")


def _open(t, session):
    m = RX["open"].match(t)
    f = get_facts().get(m["e"])
    path, app = f["location"], (m["app"] or "").lower().replace(" ", "")
    if not os.path.exists(path):
        return _where(m["e"], "where is my " + m["e"], session)  # stale path: search, confirm, update
    session["last_entity"] = f["entity"]
    if app in ("vscode", "code") and shutil.which("code"):
        subprocess.Popen([shutil.which("code"), path])
        where = "VS Code"
    elif app == "terminal" and shutil.which("wt"):
        subprocess.Popen([shutil.which("wt"), "-d", path if os.path.isdir(path) else os.path.dirname(path)])
        where = "Terminal"
    else:
        os.startfile(path)  # folder -> Explorer, file -> its default app; no shell involved
        where = "Explorer" if os.path.isdir(path) else "its default app"
    return R(f"Opened {f['entity']} in {where}: {path}")


def match(text):
    t = text.strip()
    if DELETE_ALL.match(t):
        return True
    try:
        from ishti.skills.relationships import skill as rel_skill
        if rel_skill.match(t):
            return False
    except Exception as _exc:
        swallowed('ishti/skills/memory/skill.py:match', _exc)
    if _saved_location(t):
        return True
    if extract_path(t) and any(RX[k].search(t) for k in ("move", "moved_passive", "at")):
        return True
    return any(RX[k].match(t) for k in ("status_i", "status_is", "where", "status_q", "forget", "city", "remember"))


def locate(name, roots=None, max_depth=None, budget=None):
    """Breadth-first name search, root by root in priority order; first root with hits wins."""
    cfg = CONFIG["memory"]
    target = squash(norm(name))
    if len(target) < 3:
        return []
    for root in roots or cfg["search_roots"]:
        root = os.path.expanduser(root)
        if not os.path.isdir(root):
            continue
        found, q = [], deque([(root, 0)])
        deadline = time.time() + (budget or cfg["search_budget_s"])
        while q and time.time() < deadline and len(found) < 8:
            d, depth = q.popleft()
            try:
                entries = list(os.scandir(d))
            except OSError:
                continue
            for e in entries:
                n = e.name.lower()
                if n in SKIP or n.startswith((".", "$")):
                    continue
                is_dir = e.is_dir(follow_symlinks=False)
                if target in squash(n if is_dir else os.path.splitext(n)[0]):
                    found.append((squash(os.path.splitext(n)[0]) != target, not is_dir, len(e.path), e.path))
                elif is_dir and depth < (max_depth or cfg["search_max_depth"]):
                    q.append((e.path, depth + 1))
        if found:
            return [p for *_, p in sorted(found)[:5]]
    return []


def _resolve(e, session):
    e = e.strip(" .!?'\"")
    return session.get("last_entity") or e if e.lower() in PRONOUNS else e


def _store(entity, attr, value, session):
    prev = get_facts().set(entity, attr, value)
    trace(session, "save", f"{'update' if prev else 'save'} {entity} {attr}")
    shown = get_facts().get(entity).get("entity", entity)
    session["last_entity"] = shown
    fact = {"location": f"{shown} is at {value}", "status": f"{shown} is {value}"}.get(attr, f"{shown} {attr}: {value}")
    if prev == value:
        return R(f"Already saved: {fact}")
    msg = f"{'Updated' if prev else 'Saved'}: {fact}" + (f" (was {prev})" if prev else "")
    if attr == "location" and not os.path.exists(value):
        msg += "\nNote: that path doesn't exist right now."
    return R(msg)


def _where(e, text, session):
    facts = get_facts().get(e)
    loc, note = facts.get("location"), ""
    trace(session, "lookup", f"look up '{e}' in saved facts", found=bool(loc))
    if loc:
        session["last_entity"] = facts["entity"]
        if os.path.exists(loc):
            return R(f"{facts['entity']}: {loc}")
        note = f"Saved path {loc} no longer exists. "
    elif not PERSONAL.search(text):
        return None  # general question ("where is Paris?") - not the owner's files
    trace(session, "search", f"search D:, Desktop, Downloads for '{e}'", status="start")
    found = locate(e)
    trace(session, "search", f"found {len(found)} match(es) for '{e}'", results=found)
    if not found:
        return R(note + f"{e} not found on D:, Desktop or Downloads. Tell me the path and I'll save it.")
    session["pending"] = {"skill": "memory", "entity": facts.get("entity", e), "candidates": found}
    listing = "\n".join(f"{i}. {p}" for i, p in enumerate(found, 1))
    return R(f"{note}Found {e}:\n{listing}\nWhich one? (number, 'yes' = 1, 'no')")


def _forget_person(t, session):
    """"Forget everything about my cousin Bilal" (checklist 138) - a forget aimed at a PERSON. It deletes
    only that person's links to the owner (and their own stored facts), never anyone else's, and it
    reports the real count. Returns None when the target is not a person we know, so the normal
    entity-level forget still owns projects and folders."""
    m = FORGET_PERSON.match(t)
    if not m or not m["rel"]:  # a bare "forget X" is ambiguous - only an explicit relationship is scoped
        return None
    from ishti.skills.relationships.skill import forget_person
    res = forget_person(m["name"], m["rel"], session)
    if not res:
        name = m["name"].strip().title()
        return R(f"I have nothing stored about {name} (your {m['rel']}), so there was nothing to delete - "
                 f"no other record was touched.")
    lines = "\n".join(f"- {l}" for l in res["edges"]) or "- (no link was stored)"
    extra = f" and {res['facts']} saved fact(s)" if res["facts"] else ""
    trace(session, "save", f"scoped forget: {res['name']}")
    return R(f"Forgot {len(res['edges'])} relationship record(s) about {res['name'].title()}{extra}:\n{lines}\n"
             f"No other person was touched.")


def _person_desc_matches(desc):
    """Resolve a person named by role/relationship ("my cardiologist collaborator") to the known
    people it could mean. Delegates to arch2.people.find_by_description (entity rows + relationship
    graph). Returns display names (never a guess when more than one matches - the caller asks)."""
    try:
        from ishti.arch2 import people as P
        return sorted({(e.get("display") or e["name"]) for e in P.find_by_description(desc)})
    except Exception as _exc:
        swallowed('ishti/skills/memory/skill.py:_person_desc_matches', _exc)
        return []


def _really_delete(name, session):
    """HARD deletion across every store: memory rows, entity rows, graph node+edges and the
    best-effort vector snippets (arch2.memory.forget_hard). Reports the real counts so the owner can
    see it was removed, not just hidden, and never claims more than actually went."""
    try:
        from ishti.arch2 import memory as M
        res = M.forget_hard(name)
    except Exception as exc:
        return R(f"I could not complete the deletion of {name.title()}: {type(exc).__name__}: {exc}. "
                 f"Nothing was removed - your data is unchanged.")
    m = res.get("memories", 0)
    e = res.get("entities", 0)
    ed = res.get("edges", 0)
    trace(session, "save", f"hard-deleted everything about {name}")
    return R(f"Deleted everything about {name.title()}: {m} memory record(s), {e} entity record(s), "
             f"{ed} relationship-graph link(s), plus any stored conversation snippet that mentioned them. "
             f"This is a real removal from all stores (memory, entity table and relationship graph), not just "
             f"hidden. The deletion event itself is logged - without the deleted content.")


def _gate_delete(session, name, action):
    return ask_first(session, "memory", f"Delete every saved memory and relationship about {name.title()}?", action)


def _delete_all(t, session):
    m = DELETE_ALL.match(t)
    cands = _person_desc_matches(m["target"])
    if not cands:
        return R(f"I don't have anyone stored that matches \"{m['target'].strip()}\", so there is nothing "
                 f"to delete. Tell me their exact name and I'll remove everything about them.")
    if len(cands) > 1:
        session["pending"] = {"skill": "memory", "delete_all": cands}
        listing = "\n".join(f"{i}. {n}" for i, n in enumerate(cands, 1))
        return R(f"That could be more than one person:\n{listing}\nWhich one? (number) - nothing has been "
                 f"deleted yet.")
    name = cands[0]
    return _gate_delete(session, name, lambda: _really_delete(name, session))


def _pending(t, p, session):
    if p.get("delete_all"):
        low, cd = t.lower().strip(" .!"), p["delete_all"]
        if low.isdigit() and 1 <= int(low) <= len(cd):
            name = cd[int(low) - 1]
            return _gate_delete(session, name, lambda: _really_delete(name, session))
        for n in cd:
            if low and (low in n.lower() or n.lower() in low):
                return _gate_delete(session, n, lambda: _really_delete(n, session))
        return None
    low, c = t.lower().strip(" .!"), p["candidates"]
    if low in YES:
        return _store(p["entity"], "location", c[0], session)
    if low.isdigit() and 1 <= int(low) <= len(c):
        return _store(p["entity"], "location", c[int(low) - 1], session)
    if low in NO:
        return R("OK, not saved. Tell me the path and I'll save it.")
    path = extract_path(t)
    if path:
        return _store(p["entity"], "location", path, session)
    return None  # unrelated message: drop the question, route normally


def _extract(t, session):
    out = llm.ask(
        'Extract one durable fact from the owner\'s message as JSON: {"entity": "<what it is about, as the '
        'owner names it; use \\"owner\\" for the owner themself>", "attr": "<location | status | a short '
        'lowercase attribute like birthday or email>", "value": "<exact value, paths verbatim>"}. '
        f"If there is no durable fact return {{}}.\nMessage: {t}",
        system=None, temperature=0, response_format={"type": "json_object"})
    try:
        d = json.loads(out)
    except ValueError:
        d = {}
    if not all(isinstance(d.get(k), str) and d.get(k).strip() for k in ("entity", "attr", "value")):
        return None
    value = extract_path(d["value"]) or d["value"] if d["attr"] == "location" else d["value"]
    return _store(d["entity"].strip(), d["attr"].strip().lower(), value.strip(), session)


def run(text, session):
    t, facts = text.strip(), get_facts()
    p = session.get("pending")
    if p and p.get("skill") == "memory":
        session["pending"] = None
        return _pending(t, p, session)
    if _saved_location(t):
        return _open(t, session)
    if DELETE_ALL.match(t):
        return _delete_all(t, session)
    if m := RX["forget"].match(t):
        if out := _forget_person(t, session):  # a relationship, deleted where it lives
            return out
        e = _resolve(m["e"], session)
        def forget():
            return R(f"Forgot {e}." if facts.forget(e) else f"Nothing saved for {e}.")
        return ask_first(session, "memory", f"Forget the saved fact about {e}?", forget)
    path = extract_path(t)
    if path:
        for k in ("move", "moved_passive", "at"):
            if m := RX[k].search(t):
                return _store(_resolve(m["e"], session), "location", path, session)
    elif m := RX["city"].match(t):
        prev = facts.set("owner", "city", m["c"].strip())
        return R(f"Updated: you live in {m['c'].strip()}" + (f" (was {prev})." if prev else "."))
    if m := RX["status_i"].match(t) or RX["status_is"].match(t):
        return _store(_resolve(m["e"], session), "status", STATUS[m["s"].lower()], session)
    if m := RX["where"].match(t):
        return _where(_resolve(m["e"], session), t, session)
    if m := RX["status_q"].match(t):
        f = facts.get(_resolve(m["e"], session))
        return R(f"{f['entity']}: {f['status']}" if f.get("status") else f"No status saved for {m['e']}.")
    if RX["remember"].match(t) or path:
        return _extract(t, session)
    return None


if __name__ == "__main__":
    import tempfile
    import ishti.facts as F
    tmp = tempfile.mkdtemp()
    F._facts = F.Facts(os.path.join(tmp, "t.db"))
    proj = os.path.join(tmp, "Work", "Falcon_App")
    os.makedirs(proj)
    s = {}
    assert extract_path(r"Project X is on D: at D:\Work\X.") == r"D:\Work\X"
    assert extract_path("it lives on D:") == "D:\\"
    assert match(r"Project Falcon is on D: at D:\Work\Falcon") and not match(r"summarize D:\notes.txt")
    assert not match("my code is broken, help me") and match("this is now working")

    assert "Saved" in run(rf"Project Falcon is on D: at {proj}", s)["reply"]
    assert run("where is falcon?", s)["reply"] == f"Project Falcon: {proj}"
    new = os.path.join(tmp, "Archive", "Falcon_App")
    os.makedirs(new)
    r = run(f"I moved falcon to {new}", s)["reply"]
    assert "Updated" in r and proj in r, r
    assert len(F.get_facts().keys()) == 1, "update must not append a second record"
    assert "working" in run("this is now working", s)["reply"]
    assert run("status of falcon", s)["reply"].endswith("working")
    assert "is finished" in run("I finished the falcon project", s)["reply"]

    # unknown location -> search the configured roots -> confirm -> stored
    CONFIG["memory"]["search_roots"] = [tmp]
    r = run("where is my eagle project?", s)["reply"]
    assert "not found" in r, r
    os.makedirs(os.path.join(tmp, "Stuff", "eagle-project"))
    r = run("where is my eagle project?", s)["reply"]
    assert "1." in r and "eagle-project" in r and s["pending"], r
    assert "Saved" in run("1", s)["reply"] and F.get_facts().get("eagle")["location"].endswith("eagle-project")
    assert run("where is Paris?", s) is None, "general questions fall through"
    assert match("open falcon") and not match("open notepad") and not match("open github.com")
    assert "Forgot" in run("forget eagle", s)["reply"] and not F.get_facts().get("eagle")

    # 138: "forget everything about my cousin Bilal" routes to THIS skill, so the scoped delete happens
    # here: only that person's link in the relationship graph (+ their own facts) goes, the real count is
    # reported, and every other person survives. A project forget keeps working as before.
    import ishti.rgraph as rg
    rg.DB_PATH = os.path.join(tmp, "g.db")
    rg._conn = None
    rg.relate("me", "cousin", "Bilal")
    rg.relate("me", "brother", "Ahmed")
    rg.relate("bilal", "works_on", "CardioSense", kind_dst="project")
    F.get_facts().set("Bilal", "note", "owes me 500")
    r = run("Forget everything about my cousin Bilal", s)["reply"]
    assert "Forgot 1 relationship record(s) about Bilal" in r and "1 saved fact" in r, r
    assert not rg.edges_of("me", rel="cousin", direction="out"), "the cousin link is gone"
    assert rg.edges_of("me", rel="brother", direction="out"), "Ahmed is untouched"
    assert rg.edges_of("bilal", rel="works_on", direction="out"), "only the named relationship is scoped"
    assert not F.get_facts().get("Bilal") and F.get_facts().get("falcon"), "the fact store lost only Bilal"
    assert "nothing stored about Nobody" in run("Forget everything about my cousin Nobody", s)["reply"]
    assert "falcon" in F.get_facts().keys(), "the project remember is still there"
    print("[self-check] memory skill OK - store, move-update, status, pronoun, search+confirm, forget, "
          "scoped relationship forget (138)")
