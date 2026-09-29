"""Goal tracker (inventory domain 17 #6, 20 #26/#30/#476/#480): targets and progress, exact.

"new goal save 100000 for laptop by 31 Dec", "add 5000 to the laptop goal",
"progress on the laptop goal", "my goals", "cancel the laptop goal"

Store: data/goals.db (name, target, current, created, due). The progress view computes the
exact percent, remaining, and — when a due date exists — the required amount per remaining
day. Category words (donation, sadqa, savings) are just kept in the name; the math is the
same. T0, local write only.
"""
import datetime as dt
import os
import re
import sqlite3
import threading

from ishti import CONFIG
from ishti.skills import trace

DB = os.path.join(os.path.dirname(CONFIG["memory"]["db_path"]), "goals.db")
_lock = threading.Lock()
I = re.I

NEW = re.compile(
    r"^\s*(?:new\s+goal|create\s+(?:a\s+)?goal|set\s+(?:a\s+)?goal)\s+(?:to\s+)?(?P<rest>.+)$", I)
ADD = re.compile(
    r"^\s*(?:add|log|put)\s+(?:rs\.?\s*|rs\b)?(?P<amt>\d[\d,]*(?:\.\d+)?)\s*(?:k\b)?(?:\s*(?:rs|pkr|rupees))?\s+"
    r"(?:to|towards|in)\s+(?:the\s+)?(?P<name>.+?)\s+goal\s*$", I)
PROGRESS = re.compile(r"^\s*(?:progress|status)\s+(?:on|of|for)\s+(?:the\s+|my\s+)?(?P<name>.+?)\s+goal\s*$", I)
LIST = re.compile(r"^\s*(?:my\s+)?goals?\s*$|^\s*(?:list|show)\s+(?:my\s+)?goals?\s*$", I)
CANCEL = re.compile(r"^\s*(?:cancel|delete|remove|drop)\s+(?:the\s+|my\s+)?(?P<name>.+?)\s+goal\s*$", I)
_name_words = r"[A-Za-z][\w &'-]{1,40}"


def _db():
    os.makedirs(os.path.dirname(DB), exist_ok=True)
    c = sqlite3.connect(DB, timeout=10)
    c.execute("CREATE TABLE IF NOT EXISTS goals(id INTEGER PRIMARY KEY, name TEXT UNIQUE, target REAL, "
              "current REAL DEFAULT 0, created TEXT, due TEXT)")
    return c


def _norm(s):
    n = re.sub(r"\s+", " ", s.strip().lower()).strip(" .!")
    return re.sub(r"^(?:the|my|a)\s+", "", n)


def _num(s):
    s = s.lower().replace(",", "")
    if s.endswith("k"):
        return float(s[:-1]) * 1000
    return float(s)


def _parse_new(rest):
    """'save 100000 for laptop by 31 Dec' / 'read 30 books this year' -> (name, target, due)."""
    rest = rest.strip().rstrip(".!")
    due = None
    m = re.search(r"\bby\s+((?:\d{1,2}(?:st|nd|rd|th)?\s+)?(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*(?:\s+\d{4})?)", rest, I)
    if m:
        from dateutil import parser as dtp
        try:
            due = dtp.parse(m.group(1), dayfirst=True).date().isoformat()
            rest = rest[:m.start()].strip()
        except ValueError:
            pass
    m = re.search(r"(?:save|reach|raise|hit)?\s*(?:rs\.?\s*|rs\b)?(?P<t>\d[\d,]*k?)\s*(?:rs|pkr|rupees)?\s*(?:for|towards|to)\s+(?P<n>.+)", rest, I)
    if m:
        return _norm(m.group("n")), _num(m.group("t")), due
    m = re.search(r"(?P<t>\d[\d,]*k?)\s*(?P<n>[a-z][\w &'-]{2,40})", rest, I)
    if m:
        return _norm(m.group("n")), _num(m.group("t")), due
    return _norm(rest), None, due


def new_goal(rest):
    name, target, due = _parse_new(rest)
    if not name:
        return "What is the goal? Try \"new goal save 100000 for laptop\"."
    with _lock, _db() as c:
        try:
            c.execute("INSERT INTO goals(name, target, current, created, due) VALUES(?,?,0,?,?)",
                      (name, target or 0, dt.date.today().isoformat(), due))
        except sqlite3.IntegrityError:
            return f"Goal **{name}** already exists — see \"progress on the {name} goal\"."
    return (f"Goal **{name}** created" + (f", target {target:g}" if target else "") +
            (f", due {due}" if due else "") + ". Log progress with \"add 5000 to the "
            f"{name} goal\".")


def add_progress(name, amt):
    name = _norm(name)
    with _lock, _db() as c:
        row = c.execute("SELECT target, current, due FROM goals WHERE name=?", (name,)).fetchone()
        if not row:
            return f"No goal named **{name}**."
        target, cur, due = row
        new = cur + amt
        c.execute("UPDATE goals SET current=? WHERE name=?", (new, name))
    pct = (new / target * 100) if target else 0
    out = (f"**{name}**: {new:g} of {target:g} ({pct:.0f}%)"
           + (f" — {'done!' if pct >= 100 else f'{target - new:g} to go'}" if target else ""))
    if due and pct < 100:
        days = (dt.date.fromisoformat(due) - dt.date.today()).days
        if days > 0:
            out += f" {days} day(s) left: {max(0.0, (target - new) / days):g} per day to stay on track."
        else:
            out += " The due date has passed."
    return out


def _progress_row(row):
    name, target, cur, due = row
    pct = (cur / target * 100) if target else 0
    out = f"- **{name}**: {cur:g}/{target:g} ({pct:.0f}%)" if target else f"- **{name}**: {cur:g}"
    if due:
        days = (dt.date.fromisoformat(due) - dt.date.today()).days
        out += f", due {due} ({days}d left)"
    return out


def progress(name):
    name = _norm(name)
    with _lock:
        row = _db().execute("SELECT name, target, current, due FROM goals WHERE name=?", (name,)).fetchone()
    if not row:
        return f"No goal named **{name}**."
    return _progress_row(row)


def list_goals():
    with _lock:
        rows = _db().execute("SELECT name, target, current, due FROM goals ORDER BY created").fetchall()
    if not rows:
        return "No goals yet — \"new goal save 100000 for laptop\"."
    return "**Your goals:**\n" + "\n".join(_progress_row(r) for r in rows)


def cancel(name):
    name = _norm(name)
    with _lock, _db() as c:
        n = c.execute("DELETE FROM goals WHERE name=?", (name,)).rowcount
    return f"Cancelled the **{name}** goal." if n else f"No goal named **{name}**."


def match(text):
    t = text.strip()
    return bool(NEW.match(t) or ADD.match(t) or PROGRESS.match(t) or LIST.match(t) or CANCEL.match(t))


def run(text, session):
    t = text.strip()
    if (m := NEW.match(t)):
        trace(session, "goaltrack", "new")
        return {"reply": new_goal(m.group("rest")), "sources": []}
    if (m := ADD.match(t)):
        trace(session, "goaltrack", "add progress")
        return {"reply": add_progress(m.group("name"), _num(m.group("amt"))), "sources": []}
    if (m := PROGRESS.match(t)):
        trace(session, "goaltrack", "progress")
        return {"reply": progress(m.group("name")), "sources": []}
    if LIST.match(t):
        trace(session, "goaltrack", "list")
        return {"reply": list_goals(), "sources": []}
    if (m := CANCEL.match(t)):
        trace(session, "goaltrack", "cancel")
        return {"reply": cancel(m.group("name")), "sources": []}
    return None


if __name__ == "__main__":
    import tempfile
    DB = os.path.join(tempfile.mkdtemp(), "g.db")  # rebind: CONFIG was read at import

    assert match("new goal save 100000 for laptop by 31 Dec")
    assert match("add 5000 to the laptop goal")
    assert match("progress on the laptop goal")
    assert match("my goals")
    assert match("cancel the laptop goal")
    assert not match("goals of the match")            # football, not ours
    assert not match("add 5000 to cart")              # no 'goal' suffix

    r = run("new goal save 100k for laptop by 31 Dec", {})
    assert "Goal **laptop** created" in r["reply"] and "100000" in r["reply"] and "due" in r["reply"], r
    r2 = run("add 5000 to the laptop goal", {})
    assert "5,000" in r2["reply"] or "5000 of 100000 (5%)" in r2["reply"], r2
    assert "per day" in r2["reply"], r2               # on-track math with a due date
    r3 = run("progress on the laptop goal", {})
    assert "5000/100000 (5%)" in r3["reply"], r3
    r4 = run("my goals", {})
    assert "laptop" in r4["reply"], r4
    r5 = run("new goal save 100k for laptop", {})
    assert "already exists" in r5["reply"], r5
    r6 = run("cancel the laptop goal", {})
    assert "Cancelled" in r6["reply"], r6
    assert "No goals yet" in run("my goals", {})["reply"]
    print("[self-check] goaltrack OK - new/add/progress/list/cancel, exact percent + per-day math, "
          "duplicate refused politely, local data/goals.db")
