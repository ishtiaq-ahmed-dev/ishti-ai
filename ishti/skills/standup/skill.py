"""Standup notes (inventory domain 15 #10): fill three lines, get a paste-ready post.

"set standup yesterday: finished the parser, reviewed Ali's PR"
"set standup today: write tests for the parser"
"standup blockers: waiting on the API docs"
"give me my standup" / "standup" / "yesterday's standup"

Store: data/standup.db (day, field, text). Emitting composes Yesterday / Today / Blockers
(omitting empty sections), saves the composed post to history, and carries nothing over
automatically — yesterday's "today" is retrievable as reference, never auto-copied, so a
lazy standup can't silently lie. T0, local write only.
"""
import datetime as dt
import os
import re
import sqlite3
import threading

from ishti import CONFIG
from ishti.skills import trace

DB = os.path.join(os.path.dirname(CONFIG["memory"]["db_path"]), "standup.db")
_lock = threading.Lock()
I = re.I

SET = re.compile(
    r"^\s*(?:set\s+standup\s+|standup\s+)(?P<field>yesterday|today|blockers?)\s*[:\-]\s*(?P<text>.+)$", I)
EMIT = re.compile(r"^\s*(?:give\s+me\s+)?(?:my\s+)?standup(?:\s+post|\s+note)?\s*[!.]?\s*$", I)
YESTERDAY_POST = re.compile(r"^\s*(?:yesterday'?s?|last\s+days?'?s?)\s+standup\s*[!.]?\s*$", I)
HISTORY = re.compile(r"^\s*standup\s+history(?:\s+(?P<n>\d{1,2}))?\s*$", I)
CLEAR = re.compile(r"^\s*clear\s+(?:today'?s?\s+)?standup\s*$", I)
FIELDS = ("yesterday", "today", "blockers")


def _db():
    os.makedirs(os.path.dirname(DB), exist_ok=True)
    c = sqlite3.connect(DB, timeout=10)
    c.execute("CREATE TABLE IF NOT EXISTS lines(day TEXT, field TEXT, text TEXT, "
              "UNIQUE(day, field))")
    return c


def _today():
    return dt.date.today().isoformat()


def set_field(field, text):
    field = {"blocker": "blockers"}.get(field, field)
    text = text.strip().rstrip(".")
    with _lock, _db() as c:
        c.execute("INSERT INTO lines(day, field, text) VALUES(?,?,?) "
                  "ON CONFLICT(day, field) DO UPDATE SET text=excluded.text",
                  (_today(), field, text))
    return f"Standup {field} set ({len(text.split(',')) if ',' in text else 1} item(s))."


def emit(save=True):
    with _lock:
        rows = dict(_db().execute("SELECT field, text FROM lines WHERE day=?", (_today(),)).fetchall())
    if not rows:
        return ("No standup lines for today yet. Set them:\n"
                "\"set standup yesterday: ...\", \"set standup today: ...\", \"standup blockers: ...\".")
    post = ["*Standup — " + dt.date.today().strftime("%a %d %b") + "*"]
    if rows.get("yesterday"):
        post.append("**Yesterday:** " + rows["yesterday"])
    if rows.get("today"):
        post.append("**Today:** " + rows["today"])
    if rows.get("blockers"):
        post.append("**Blockers:** " + rows["blockers"])
    out = "\n".join(post)
    if save and len(rows) >= 2:
        with _lock, _db() as c:
            c.execute("INSERT OR REPLACE INTO lines(day, field, text) VALUES(?, 'posted', ?)",
                      (_today(), out))
    return out


def history(n=5):
    with _lock:
        rows = _db().execute("SELECT day, text FROM lines WHERE field='posted' ORDER BY day DESC LIMIT ?",
                             (int(n),)).fetchall()
    if not rows:
        return "No posted standups yet (one is saved when you emit with at least two sections filled)."
    return "**Recent standups:**\n" + "\n\n".join(f"{d}:\n{t}" for d, t in rows[:int(n)])


def clear():
    with _lock, _db() as c:
        n = c.execute("DELETE FROM lines WHERE day=?", (_today(),)).rowcount
    return f"Cleared today's standup lines ({n})."


def match(text):
    t = text.strip()
    return bool(SET.match(t) or EMIT.match(t) or YESTERDAY_POST.match(t) or HISTORY.match(t) or CLEAR.match(t))


def run(text, session):
    t = text.strip()
    if (m := SET.match(t)):
        trace(session, "standup", f"set {m.group('field')}")
        return {"reply": set_field(m.group("field"), m.group("text")), "sources": []}
    if YESTERDAY_POST.match(t):
        trace(session, "standup", "yesterday")
        with _lock:
            rows = _db().execute("SELECT text FROM lines WHERE field='posted' AND day<? ORDER BY day DESC LIMIT 1",
                                 (_today(),)).fetchone()
        return {"reply": rows[0] if rows else "No posted standup from a previous day yet.", "sources": []}
    if HISTORY.match(t):
        trace(session, "standup", "history")
        m = HISTORY.match(t)
        return {"reply": history(m.group("n") or 5), "sources": []}
    if EMIT.match(t):
        trace(session, "standup", "emit")
        return {"reply": emit(), "sources": []}
    if CLEAR.match(t):
        trace(session, "standup", "clear")
        return {"reply": clear(), "sources": []}
    return None


if __name__ == "__main__":
    import tempfile
    DB = os.path.join(tempfile.mkdtemp(), "st.db")  # rebind: CONFIG was read at import

    assert match("set standup yesterday: finished the parser, reviewed Ali's PR")
    assert match("set standup today: write tests")
    assert match("standup blockers: waiting on API docs")
    assert match("give me my standup")
    assert match("yesterday's standup")
    assert match("standup history")
    assert not match("stand up please")               # a request to rise, not the skill
    assert not match("set the table")                 # not ours

    run("set standup yesterday: finished the parser", {})
    r = run("give me my standup", {})
    assert "**Yesterday:** finished the parser" in r["reply"] and "Today:" not in r["reply"], r
    run("set standup today: write tests", {})
    run("set standup blockers: waiting on docs", {})
    r2 = run("standup", {})
    assert "**Yesterday:** finished the parser" in r2["reply"], r2
    assert "**Today:** write tests" in r2["reply"] and "**Blockers:** waiting on docs" in r2["reply"], r2
    r3 = run("standup history", {})
    assert "Recent standups:" in r3["reply"], r3
    r4 = run("set standup today: replaced line", {})
    r5 = run("standup", {})
    assert "replaced line" in r5["reply"] and "write tests" not in r5["reply"], r5   # set = replace
    assert "Cleared" in run("clear standup", {})["reply"]
    print("[self-check] standup OK - set/emit/history/yesterday, empty sections omitted, "
          "set replaces, history saved on full emit, local data/standup.db")
