"""Habit tracker (local SQLite, data/habits.db).

"track water every day", "I drank water" / "mark gym done", "my habits today",
"habit streaks". Streaks are consecutive days done; the nudge loop (server start_sampler
style) is left to reminders so this stays a pure skill with no background thread of its own.
"""
import datetime as dt
import os
import re
import sqlite3
import threading
import time

from ishti import CONFIG

DB = os.path.join(os.path.dirname(CONFIG["memory"]["db_path"]), "habits.db")
_lock = threading.Lock()

I = re.I
ADD = re.compile(r"^(?:please\s+)?(?:track|add|start)\s+(?:a\s+|an\s+)?(?:new\s+)?habit\s*[:\-]?\s*(?P<body>.+)$", I)
TRACK_SHORT = re.compile(r"^track\s+(?!(?:my|the|a)\s+(?:orders?|packages?|parcels?|deliver\w*|shipments?|expenses?|spending|"
                         r"flights?|stocks?|prices?)\b)(?P<name>[A-Za-z][A-Za-z &'-]{1,29}?)(?:\s+every\s+day|\s+daily)?\s*[.!]?$", I)  # letters only: "track asset ... 250000" (an amount) is networth's, not a habit
DONE = re.compile(r"^(?:i\s+)?(?:did|done|completed|finished)\s+(?:my\s+)?(?P<name>water|gym|walk|run|reading|quran|exercise|workout|meditat\w*|stud\w*)\b[\w &'-]{0,20}(?:\s+(?:today|just\s+now))?\s*[.!]?$", I)
MARK_DONE = re.compile(r"^mark\s+(?:my\s+)?(?P<name>water|gym|walk|run|reading|quran|exercise|workout|meditat\w*|stud\w*)[\w &'-]{0,20}(?:\s+(?:as\s+)?done)?(?:\s+today)?\s*[.!]?$", I)
TODAY = re.compile(r"^(?:my\s+)?habits\s+(?:for\s+)?today|^(?:what\s+are\s+)?my\s+habits\b.*$", I)
STREAKS = re.compile(r"^habit\s+streaks?|^my\s+streaks", I)


def _db():
    os.makedirs(os.path.dirname(DB), exist_ok=True)
    c = sqlite3.connect(DB, timeout=10)
    c.execute("CREATE TABLE IF NOT EXISTS habits(id INTEGER PRIMARY KEY, name TEXT UNIQUE, created REAL, last_nudge REAL DEFAULT 0)")
    c.execute("CREATE TABLE IF NOT EXISTS done(habit_id INTEGER, day TEXT, PRIMARY KEY(habit_id, day))")
    return c


def _today():
    return dt.date.today().isoformat()


def _add(name: str) -> str:
    name = name.strip().rstrip(".")
    with _lock, _db() as c:
        try:
            c.execute("INSERT INTO habits(name, created) VALUES(?,?)", (name, time.time()))
            return f"Tracking \"{name}\" daily. Say \"I did {name}\" each day and I'll keep your streak."
        except sqlite3.IntegrityError:
            return f"Already tracking \"{name}\"."


def _mark(name: str) -> str:
    name = name.strip().rstrip(".")
    with _lock, _db() as c:
        row = c.execute("SELECT id FROM habits WHERE name LIKE ?", (f"%{name}%",)).fetchall()
        if not row:
            return f"You're not tracking \"{name}\" yet. Say \"track {name}\" first."
        if len(row) > 1:
            names = [r2[0] for r2 in c.execute("SELECT name FROM habits WHERE name LIKE ?", (f"%{name}%",))]
            return "Which habit? " + ", ".join(names)
        hid = row[0][0]
        c.execute("INSERT OR IGNORE INTO done(habit_id, day) VALUES(?,?)", (hid, _today()))
        streak = 0
        d = dt.date.today()
        while c.execute("SELECT 1 FROM done WHERE habit_id=? AND day=?", (hid, d.isoformat())).fetchone():
            streak += 1
            d -= dt.timedelta(days=1)
        return f"✅ {name} — done today. Streak: {streak} day{'s' if streak != 1 else ''}. Keep it going!"


def _today_list() -> str:
    with _lock, _db() as c:
        habits = c.execute("SELECT id, name FROM habits ORDER BY id").fetchall()
    if not habits:
        return "No habits tracked yet. Say \"track water every day\" to start."
    with _lock, _db() as c:
        done_ids = {r[0] for r in c.execute("SELECT habit_id FROM done WHERE day=?", (_today(),))}
    done = [n for hid, n in habits if hid in done_ids]
    todo = [n for hid, n in habits if hid not in done_ids]
    parts = []
    if done:
        parts.append("Done today:\n" + "\n".join(f"✅ {n}" for n in done))
    if todo:
        parts.append("Still open:\n" + "\n".join(f"⬜ {n}" for n in todo))
    return "\n\n".join(parts) if parts else "Nothing tracked yet."


def _streaks() -> str:
    with _lock, _db() as c:
        habits = c.execute("SELECT id, name FROM habits ORDER BY id").fetchall()
    if not habits:
        return "No habits tracked yet."
    out = []
    with _lock, _db() as c:
        for hid, name in habits:
            streak = 0
            d = dt.date.today()
            while c.execute("SELECT 1 FROM done WHERE habit_id=? AND day=?", (hid, d.isoformat())).fetchone():
                streak += 1
                d -= dt.timedelta(days=1)
            total = c.execute("SELECT COUNT(*) FROM done WHERE habit_id=?", (hid,)).fetchone()[0]
            out.append(f"- {name}: {streak}-day streak, {total} total")
    return "Habit streaks:\n" + "\n".join(out)


def match(text):
    t = text.strip()
    return bool(ADD.match(t) or TRACK_SHORT.match(t) or DONE.match(t) or MARK_DONE.match(t)
                or TODAY.match(t) or STREAKS.match(t))


def run(text, session):
    t = text.strip()
    if m := ADD.match(t):
        return {"reply": _add(m.group("body")), "sources": []}
    if m := TRACK_SHORT.match(t):
        return {"reply": _add(m.group("name")), "sources": []}
    if m := MARK_DONE.match(t):
        return {"reply": _mark(m.group("name")), "sources": []}
    if m := DONE.match(t):
        return {"reply": _mark(m.group("name")), "sources": []}
    if TODAY.match(t):
        return {"reply": _today_list(), "sources": []}
    if STREAKS.match(t):
        return {"reply": _streaks(), "sources": []}
    return None


if __name__ == "__main__":
    import tempfile
    DB = os.path.join(tempfile.mkdtemp(), "h.db")
    assert match("track water every day")
    assert match("track gym")
    assert not match("track asset bank account 250000")  # networth's (habit names are words, amounts have digits)
    assert not match("track subscription netflix 1500 monthly")  # subscriptions'
    assert match("I drank water") or match("did water")  # short forms route here via DONE
    assert match("mark gym done")
    assert not match("mark my last expense as tax deductible")  # ledger's labelled case
    assert match("my habits today")
    assert match("habit streaks")
    assert not match("remind me to drink water at 5")  # reminders' job
    run("track water every day", {})
    run("track gym", {})
    r = run("did water", {})
    assert "✅" in r["reply"] and "1 day" in r["reply"], r
    r2 = run("my habits today", {})
    assert "water" in r2["reply"] and "gym" in r2["reply"], r2
    r3 = run("habit streaks", {})
    assert "1-day streak" in r3["reply"], r3
    r4 = run("did gym", {})
    assert "2-day" not in r4["reply"], r4  # water and gym have independent streaks
    print("[self-check] habits OK - add/mark/today/streaks, independent streaks, all local SQLite")
