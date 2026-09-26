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

I = re.I
STATUS = {"finished": "finished", "completed": "finished", "complete": "finished", "done": "finished",
          "started": "in progress", "in progress": "in progress", "paused": "paused", "on hold": "paused",
          "abandoned": "abandoned", "shipped": "deployed", "deployed": "deployed", "live": "deployed",
          "working": "working", "broken": "broken"}
_SW = "|".join(sorted(STATUS, key=len, reverse=True))
DRIVE = r"(?=[A-Za-z]:)"
RX = {
    "move": re.compile(rf"\bmoved\s+(?P<e>.+?)\s+(?:to|into)\s+{DRIVE}", I),
    "moved_passive": re.compile(rf"^(?P<e>.+?)\s+(?:has\s+been|was|got)\s+moved\s+(?:to|into)\s+{DRIVE}", I),
    "at": re.compile(rf"^(?:remember(?:\s+that)?\s+)?(?P<e>.+?)\s+(?:is|are|lives|sits)\s+(?:now\s+)?"
                     rf"(?:located\s+|stored\s+|saved\s+|kept\s+)?(?:on|at|in)\s+{DRIVE}", I),
    "status_i": re.compile(r"^i\s+(?:(?:just|have|'ve|already)\s+)*(?P<s>finished|completed|started|paused|"
                           r"abandoned|shipped|deployed)\s+(?:working\s+on\s+)?(?P<e>.+?)[.!]?$", I),
    # requires "now": keeps "my code is broken, help me" out of the store
    "status_is": re.compile(rf"^(?P<e>.+?)\s+is\s+now\s+(?P<s>{_SW})[.!]?$", I),
    "where": re.compile(r"^(?:where(?:'s|\s+is|\s+are)|where\s+did\s+i\s+(?:put|save|keep|store)|"
                        r"what(?:'s|\s+is)\s+the\s+(?:path|location)\s+(?:of|for|to))\s+(?P<e>.+?)\s*\??$", I),
    "status_q": re.compile(r"^(?:what(?:'s|\s+is)\s+the\s+)?status\s+(?:of|on)\s+(?P<e>.+?)\s*\??$", I),
    "forget": re.compile(r"^forget\s+(?:about\s+)?(?P<e>.+?)[.!]?$", I),
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
    if loc:
        session["last_entity"] = facts["entity"]
        if os.path.exists(loc):
            return R(f"{facts['entity']}: {loc}")
        note = f"Saved path {loc} no longer exists. "
    elif not PERSONAL.search(text):
        return None  # general question ("where is Paris?") - not the owner's files
    found = locate(e)
    if not found:
        return R(note + f"{e} not found on D:, Desktop or Downloads. Tell me the path and I'll save it.")
    session["pending"] = {"skill": "memory", "entity": facts.get("entity", e), "candidates": found}
    listing = "\n".join(f"{i}. {p}" for i, p in enumerate(found, 1))
    return R(f"{note}Found {e}:\n{listing}\nWhich one? (number, 'yes' = 1, 'no')")


def _pending(t, p, session):
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
    if m := RX["forget"].match(t):
        e = _resolve(m["e"], session)
        return R(f"Forgot {e}." if facts.forget(e) else f"Nothing saved for {e}.")
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
    print("[self-check] memory skill OK - store, move-update, status, pronoun, search+confirm, forget")
