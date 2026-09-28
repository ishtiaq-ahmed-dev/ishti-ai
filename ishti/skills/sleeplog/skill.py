"""Sleep log (local SQLite, data/sleeplog.db) — deterministic.

"log sleep 7.5 hours", "slept 6h20m last night", "my sleep this week",
"my sleep debt", "monthly sleep average".
Averages and debt are computed exactly in code; the goal is 8 h/night.
No diagnosis — informational only.
"""
import datetime as dt
import os
import re
import sqlite3
import threading

from ishti import CONFIG

DB = os.path.join(os.path.dirname(CONFIG["memory"]["db_path"]), "sleeplog.db")
_lock = threading.Lock()
GOAL_H = 8.0  # hours per night (default; change here if the owner sets another goal)

I = re.I

LOG = re.compile(
    r"^\s*(?:log\s+(?:my\s+)?sleep|slept|i\s+slept)\s+"
    r"(?:(?P<h>\d{1,2}(?:\.\d+)?)\s*(?:h|hr|hrs|hours?)\s*)?(?:\s*(?P<m>\d{1,2})\s*m(?:in)?\b)?"
    r"(?:\s*(?P<h2>\d{1,2}(?:\.\d+)?)\s*(?:h|hr|hrs|hours?))?"
    r"(?:\s*(?:last\s+night|tonight|yesterday)?)"
    r"(?:\s*[-—]?\s*(?P<note>.+))?$", I)
AVG = re.compile(r"^\s*(?:my\s+)?sleep\s+(?:average|stats|summary)\s*(?:this\s+week)?\s*$"
                 r"|^\s*(?:my\s+)?(?P<span>weekly|monthly)\s+sleep(?:\s+average)?\s*$", I)
WEEK = re.compile(r"^\s*(?:my\s+)?sleep\s+(?:this|last)\s+week\s*$", I)
DEBT = re.compile(r"^\s*(?:my\s+)?sleep\s+debt\s*$|^\s*how\s+much\s+sleep\s+am\s+i\s+behind\s*\??$", I)


def _db():
    os.makedirs(os.path.dirname(DB), exist_ok=True)
    c = sqlite3.connect(DB, timeout=10)
    c.execute("""CREATE TABLE IF NOT EXISTS nights(
        night TEXT PRIMARY KEY, hours REAL NOT NULL, note TEXT,
        logged TEXT DEFAULT (datetime('now')))""")
    return c


def _fmt(x, unit="h"):
    x = float(x)
    s = f"{x:,.1f}" if x != int(x) else f"{x:,.0f}"
    return f"{s} {unit}".strip()


def _hours_from_groups(h, m, h2):
    hours = 0.0
    if h:
        hours += float(h)
    if m:
        hours += float(m) / 60.0
    if h2:  # second "8 h" group — a re-statement, not additive in practice; keep max-safe
        hours = max(hours, float(h2))
    return round(hours, 2)


def _log(hours, note):
    if not 0.5 <= hours <= 16:
        return ("That doesn't look like a night of sleep — give it as hours (and minutes), e.g. "
                "\"log sleep 7.5 hours\" or \"slept 6h20m\".")
    night = dt.date.today().isoformat()
    with _lock, _db() as c:
        row = c.execute("SELECT hours FROM nights WHERE night=?", (night,)).fetchone()
        c.execute("INSERT INTO nights(night, hours, note) VALUES(?,?,?) "
                  "ON CONFLICT(night) DO UPDATE SET hours=excluded.hours, note=excluded.note",
                  (night, hours, note))
    return (f"Logged **{_fmt(hours)}** for last night." + (f" (was {_fmt(row[0])})" if row else "") +
            " Say \"my sleep this week\" for the average.")


def _span(days, label, show=7):
    since = (dt.date.today() - dt.timedelta(days=days)).isoformat()
    with _lock, _db() as c:
        rows = c.execute("SELECT night, hours FROM nights WHERE night >= ? ORDER BY night", (since,)).fetchall()
    if not rows:
        return f"No sleep logged {label}. Try: \"log sleep 7.5 hours\"."
    vals = [h for _n, h in rows]
    avg = sum(vals) / len(vals)
    best, worst = max(vals), min(vals)
    recent = rows[-show:]
    return ("Sleep " + label + f" ({len(vals)} night(s)):\n" +
            "\n".join(f"- {n}: {_fmt(h)}" for n, h in reversed(recent)) +
            f"\nAverage **{_fmt(avg)}** (goal {_fmt(GOAL_H)}) · best {_fmt(best)} · worst {_fmt(worst)}")


def _debt():
    since = (dt.date.today() - dt.timedelta(days=6)).isoformat()
    with _lock, _db() as c:
        rows = c.execute("SELECT hours FROM nights WHERE night >= ?", (since,)).fetchall()
    if not rows:
        return "No sleep logged in the last 7 days — debt starts counting once you log nights."
    vals = [h for (h,) in rows]
    debt = sum(max(0.0, GOAL_H - h) for h in vals)
    if debt == 0:
        return "No sleep debt over the last 7 days — you hit the 8-hour goal every logged night. 🎉"
    return (f"Sleep debt (last 7 days vs 8 h/night): **{_fmt(debt)}** short. "
            "One early night helps; consistent 7.5–8 h beats weekend catch-up.")


def match(text):
    t = text.strip()
    return bool(LOG.match(t) or AVG.match(t) or WEEK.match(t) or DEBT.match(t))


def run(text, session):
    t = text.strip()
    m = LOG.match(t)
    if m:
        hours = _hours_from_groups(m.group("h"), m.group("m"), m.group("h2"))
        return {"reply": _log(hours, (m.group("note") or "").strip()), "sources": []}
    m = AVG.match(t)
    if m:
        span = (m.group("span") or "").lower()
        if span == "monthly":
            return {"reply": _span(30, "this month"), "sources": []}
        return {"reply": _span(7, "this week"), "sources": []}
    if WEEK.match(t):
        return {"reply": _span(7, "this week"), "sources": []}
    if DEBT.match(t):
        return {"reply": _debt(), "sources": []}
    return None


if __name__ == "__main__":
    import tempfile
    DB = os.path.join(tempfile.mkdtemp(), "sl.db")
    assert match("log sleep 7.5 hours")
    assert match("slept 6h20m last night")
    assert match("i slept 8 hours")
    assert match("my sleep this week")
    assert match("my sleep average")
    assert match("monthly sleep average")
    assert match("my sleep debt")
    assert not match("wake up if i go to bed now")   # quickcalc
    r = run("log sleep 7.5 hours", {})
    assert "Logged" in r["reply"] and "7.5 h" in r["reply"], r
    r1 = run("slept 6h20m last night", {})
    assert "6.3 h" in r1["reply"], r1  # 6 h 20 m = 6.33 h, shown to 1 decimal
    with _lock, _db() as c:
        c.execute("UPDATE nights SET hours=6.0 WHERE night=?", (dt.date.today().isoformat(),))
    r2 = run("log sleep 8 hours", {})
    assert "was 6 h" in r2["reply"], r2
    r3 = run("my sleep this week", {})
    assert "Average" in r3["reply"] and "8 h" in r3["reply"], r3
    r4 = run("my sleep debt", {})
    assert "debt" in r4["reply"].lower(), r4
    r5 = run("log sleep 99 hours", {})
    assert "doesn't look like" in r5["reply"], r5
    print("[self-check] sleeplog OK - log/avg/debt exact, one row per night, all local SQLite")
