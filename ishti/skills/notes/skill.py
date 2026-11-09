"""Quick personal notes (local SQLite, data/notes.db).

"note: the meeting moved to 5", "make a note that Ahmed owes 500", "show my notes",
"find the note about the projector", "delete the note about the projector".
Notes are private and local; no LLM call is needed for any of it.
"""
import os
import re
import sqlite3
import threading
import time

from ishti import CONFIG

DB = os.path.join(os.path.dirname(CONFIG["memory"]["db_path"]), "notes.db")
_lock = threading.Lock()

I = re.I
# "note that ..." is a note like any other: the (?!that) guard here sent it to owner/docs instead (their matchers
# caught it / stored it elsewhere). Quick-capture phrasings ("take a note", "jot down") stay with docs, which the
# labelled routing set pins (ishti/check.py), so they are deliberately not listed here.
ADD = re.compile(r"^(?:please\s+)?(?:make\s+a\s+note|new\s+note|save\s+a\s+note)\s*(?:that\s+)?[:,]?\s*(?P<body>.+)$|"
                 r"^note\s*:\s*(?P<body2>.+)$|^note(?:\s+that)?\s+(?P<body3>.+)$", I)
LIST = re.compile(r"^(?:show|list|what\s+are)\s+(?:my\s+|the\s+)?notes\b|^(?:do\s+i\s+have\s+)?any\s+notes\??$", I)
FIND = re.compile(r"^(?:find|search|look\s+(?:up|for)|what\s+(?:was|is)\s+the\s+note\s+(?:about|for))\s+(?:the\s+)?(?:my\s+)?note\s*(?:about|for|on)?\s*(?P<q>.+)$|^(?:find|search)\s+(?:my\s+)?notes?\s+(?:for\s+)?(?P<q2>.+)$", I)
DELETE = re.compile(r"^(?:delete|remove)\s+(?:the\s+|my\s+)?note\s*(?:about|for|on)?\s*(?P<q>.+)$", I)


def _db():
    d = os.path.dirname(DB)
    if d:
        os.makedirs(d, exist_ok=True)
    c = sqlite3.connect(DB, timeout=10)
    c.execute("CREATE TABLE IF NOT EXISTS notes(id INTEGER PRIMARY KEY, ts REAL, body TEXT UNIQUE)")
    return c


def _add(body: str) -> str:
    body = body.strip().rstrip(".")
    if not body:
        return "Nothing to note."
    with _lock, _db() as c:
        try:
            c.execute("INSERT INTO notes(ts, body) VALUES(?, ?)", (time.time(), body))
            n = c.execute("SELECT COUNT(*) FROM notes").fetchone()[0]
            return f"Noted: \"{body}\" ({n} note{'s' if n != 1 else ''} saved)."
        except sqlite3.IntegrityError:
            return "That's already in your notes."


def _list() -> str:
    with _lock, _db() as c:
        rows = c.execute("SELECT ts, body FROM notes ORDER BY ts DESC LIMIT 20").fetchall()
    if not rows:
        return "You have no notes yet. Say 'note: ...' and I'll remember it."
    lines = [f"- {b} ({time.strftime('%d %b', time.localtime(t))})" for t, b in rows]
    more = f"\n… and {len(rows)} shown of all." if len(rows) == 20 else ""
    return "Your notes:\n" + "\n".join(lines) + more


def _find(q: str) -> str:
    words = [w for w in re.findall(r"\w{3,}", q.lower())]
    if not words:
        return _list()
    with _lock, _db() as c:
        rows = c.execute("SELECT ts, body FROM notes ORDER BY ts DESC").fetchall()
    hits = [b for t, b in rows if any(w in b.lower() for w in words)]
    if not hits:
        return f"No note matches \"{q.strip()}\". Say 'show my notes' to see them all."
    return "Found:\n" + "\n".join(f"- {h}" for h in hits[:10])


_STOP = {"the", "about", "note", "notes", "that", "this", "with", "from", "and", "for", "my", "one"}


def _delete(q: str, session=None):
    words = [w for w in re.findall(r"\w{3,}", q.lower()) if w not in _STOP]
    with _lock, _db() as c:
        rows = c.execute("SELECT id, body FROM notes ORDER BY ts DESC").fetchall()
    victims = [(i, b) for i, b in rows if words and all(re.search(rf"\b{re.escape(w)}\b", b.lower()) for w in words)]
    if not victims:
        return f"No note matches \"{q.strip()}\" - nothing deleted."
    if len(victims) > 1:
        return ("Several notes match - which one?\n"
                + "\n".join(f"- {b}" for _i, b in victims[:8])
                + "\nSay 'delete the note <exact words>' to pick one.")
    def remove():
        with _lock, _db() as c:
            c.execute("DELETE FROM notes WHERE id=?", (victims[0][0],))
        return f"Deleted the note: \"{victims[0][1]}\"."
    if session is None:
        return remove()
    from ishti.skills import ask_first
    return ask_first(session, "notes", f"Delete the note \"{victims[0][1][:80]}\"", remove)


def match(text):
    return bool(ADD.match(text.strip()) or LIST.match(text.strip()) or FIND.match(text.strip())
                or DELETE.match(text.strip()))


def run(text, session):
    t = text.strip()
    m = ADD.match(t)
    if m:
        body = m.group("body") or m.group("body2") or m.group("body3") or ""
        return {"reply": _add(body), "sources": []}
    if LIST.match(t):
        return {"reply": _list(), "sources": []}
    m = FIND.match(t)
    if m:
        return {"reply": _find(m.group("q") or m.group("q2") or ""), "sources": []}
    m = DELETE.match(t)
    if m:
        out = _delete(m.group("q"), session)
        return out if isinstance(out, dict) else {"reply": out, "sources": []}
    return None


if __name__ == "__main__":
    import tempfile
    globals()["DB"] = os.path.join(tempfile.mkdtemp(), "notes.db")  # self-check is hermetic: temp store
    assert match("note that the meeting moved to 5")
    assert match("make a note Ahmed owes 500")
    assert match("show my notes")
    assert match("find the note about projector")
    assert match("delete the note about projector")
    assert not match("remember that my name is Ishtiaq")  # memory skill's job
    assert not match("what's the weather")
    r = run("make a note that the projector cable is broken", {})
    assert "Noted" in r["reply"], r
    r_dup = run("note: the projector cable is broken", {})
    assert "already" in r_dup["reply"], r_dup  # same body via another phrasing
    r2 = run("show my notes", {})
    assert "projector" in r2["reply"], r2
    r3 = run("find the note about projector", {})
    assert "projector" in r3["reply"], r3
    sess = {}
    r4 = run("delete the note about projector", sess)
    assert "(yes/no)" in r4["reply"], r4  # a delete is confirmed first
    assert "Deleted" in sess["pending"]["action"](), sess
    assert "nothing deleted" in run("delete the note about the car", {})["reply"]  # "car" is not "card"
    r5 = run("note the projector cable is broken", {})  # r4 deleted it: adding again must work
    assert "Noted" in r5["reply"], r5
    r6 = run("note the projector cable is broken", {})
    assert "already" in r6["reply"], r6  # duplicate guard across all phrasings
    print("[self-check] notes OK - add/list/find/delete, duplicate guard, all local SQLite")
