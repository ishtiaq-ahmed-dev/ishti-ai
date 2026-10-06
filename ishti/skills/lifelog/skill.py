"""Personal logs (tracking only - never medical advice): water, sleep, exercise, habits, and screen time.
"I drank 3 glasses of water", "slept 7 hours", "ran 5 km" / "worked out 45 minutes", "did my reading habit",
"my habits this week", "how much did I sleep this week", "screen time today". Screen time comes from a tiny
background sampler (the foreground app once a minute) that runs only inside ISHTI's server. Local SQLite."""
import datetime as dt
import os
import re
import sqlite3
import threading
import time

from ishti import ROOT
from ishti.skills import trace
from ishti.test_mode import scratch_path

I = re.I
DB = scratch_path(os.path.join(ROOT, "data", "lifelog.db"))
SAMPLE_S = 60
LOG = [
    ("water", re.compile(r"\b(?:drank|had|drink)\s+(?P<n>\d+(?:\.\d+)?)\s*(?P<u>glass(?:es)?|cups?|liters?|litres?|l|ml|bottles?)\s+(?:of\s+)?water\b", I)),
    ("sleep", re.compile(r"\b(?:slept|sleep\s+was|got)\s+(?P<n>\d+(?:\.\d+)?)\s*(?P<u>hours?|hrs?|h)\b", I)),
    ("exercise", re.compile(r"\b(?:ran|walked|jogged|cycled|swam|worked\s+out|exercised|did\s+(?:yoga|gym|cardio))\b(?:\s+for)?\s*(?P<n>\d+(?:\.\d+)?)?\s*(?P<u>km|kilometers?|minutes?|mins?|hours?|steps)?", I)),
]
# learning: "I studied machine learning for 2 hours", "revised calculus for 45 minutes", "did 1.5h of DSA"
STUDY = re.compile(r"\b(?:i\s+)?(?:studied|learned|learnt|revised|practi[cs]ed|read\s+up\s+on|worked\s+on)\s+(?P<topic>[\w .+#&-]{2,40}?)"
                   r"\s+(?:for\s+)?(?P<n>\d+(?:\.\d+)?)\s*(?P<u>hours?|hrs?|h|minutes?|mins?|m)\b|"
                   r"\b(?:did|spent)\s+(?P<n2>\d+(?:\.\d+)?)\s*(?P<u2>hours?|hrs?|h|minutes?|mins?)\s+(?:of|on|studying)\s+(?P<topic2>[\w .+#&-]{2,40})", I)
PROGRESS = re.compile(r"\b(?:my\s+)?(?:learning|study|studying)\s+(?:progress|report|log|stats)\b|\bwhat\s+(?:have\s+i|did\s+i)\s+stud(?:y|ied)\b|"
                      r"\bhow\s+much\s+(?:have\s+i|did\s+i)\s+stud(?:y|ied)\b", I)
HABIT_DONE = re.compile(r"\b(?:did|done|completed|finished)\s+(?:my\s+)?(?P<h>[\w ]{2,30}?)\s+habit\b|\bhabit\s*[:,-]\s*(?P<h2>[\w ]{2,30})\s+done\b", I)
REPORT = re.compile(r"\b(?:my\s+habits|habit\s+(?:streak|report)|how\s+much\s+(?:did\s+i\s+)?(?:sleep|slept|water|exercise)|"
                    r"(?:sleep|water|exercise|workout)\s+(?:this|last|report|log|today)|screen\s*time|which\s+apps\s+did\s+i\s+use)", I)


def match(text):
    return bool(PROGRESS.search(text) or STUDY.search(text) or REPORT.search(text) or HABIT_DONE.search(text) or any(rx.search(text) for _, rx in LOG))


def study_streak(now=None):
    """Consecutive days (ending today or yesterday) with any study logged."""
    import datetime as _dt
    with db() as c:
        days = {_dt.date.fromtimestamp(t) for (t,) in c.execute("SELECT ts FROM logs WHERE kind='study'")}
    day, n = _dt.date.fromtimestamp(now or time.time()), 0
    if day not in days:
        day -= _dt.timedelta(days=1)
    while day in days:
        n, day = n + 1, day - _dt.timedelta(days=1)
    return n


def learning_report(days=30):
    since = time.time() - days * 86400
    with db() as c:
        rows = c.execute("SELECT note, SUM(amount), COUNT(*), MAX(ts) FROM logs WHERE kind='study' AND ts>=? GROUP BY note "
                         "ORDER BY SUM(amount) DESC", (since,)).fetchall()
        today = c.execute("SELECT COALESCE(SUM(amount),0) FROM logs WHERE kind='study' AND ts>=?", (_day_start(),)).fetchone()[0]
    if not rows:
        return "No study logged yet - say e.g. \"I studied machine learning for 2 hours\"."
    total = sum(r[1] for r in rows)
    lines = [f"- {topic}: {mins / 60:.1f} h over {n} session(s)" for topic, mins, n, _ in rows[:12]]
    return (f"Learning, last {days} days: {total / 60:.1f} h across {len(rows)} topic(s); today {today:g} min; "
            f"streak {study_streak()} day(s).\n" + "\n".join(lines))


def db():
    os.makedirs(os.path.dirname(DB), exist_ok=True)
    c = sqlite3.connect(DB)
    c.executescript("""CREATE TABLE IF NOT EXISTS logs(ts REAL, kind TEXT, amount REAL, unit TEXT, note TEXT);
                       CREATE TABLE IF NOT EXISTS usage(ts REAL, app TEXT, title TEXT);""")
    return c


def run(text, session):
    if PROGRESS.search(text):
        trace(session, "lifelog", "learning progress")
        return {"reply": learning_report(), "sources": []}
    if m := STUDY.search(text):
        topic = (m["topic"] or m["topic2"]).strip(" .").lower()
        n, u = float(m["n"] or m["n2"]), (m["u"] or m["u2"]).lower()
        minutes = n * (60 if u.startswith("h") else 1)
        with db() as c:
            c.execute("INSERT INTO logs VALUES(?,?,?,?,?)", (time.time(), "study", minutes, "min", topic))
            total = c.execute("SELECT SUM(amount) FROM logs WHERE kind='study' AND note=?", (topic,)).fetchone()[0]
        return {"reply": f"Logged {minutes:g} min of {topic}. Total on {topic}: {total / 60:.1f} h; "
                         f"study streak {study_streak()} day(s).", "sources": []}
    if REPORT.search(text):
        trace(session, "lifelog", "report")
        return {"reply": report(text), "sources": []}
    if m := HABIT_DONE.search(text):
        habit = (m.group("h") or m.group("h2")).strip().lower()
        with db() as c:
            c.execute("INSERT INTO logs VALUES(?,?,?,?,?)", (time.time(), "habit", 1, "", habit))
        return {"reply": f"Habit '{habit}' done - {streak(habit)} day streak.", "sources": []}
    for kind, rx in LOG:
        if m := rx.search(text):
            n = float(m.group("n") or 1)
            unit = unit_of(m.group("u")) or ("session" if kind == "exercise" else "")
            if kind == "water" and unit in ("liter", "ml", "bottle", "cup"):  # all water in ~250 ml glasses
                n, unit = n * {"liter": 4, "ml": 1 / 250, "bottle": 2, "cup": 1}[unit], "glass"
            with db() as c:
                c.execute("INSERT INTO logs VALUES(?,?,?,?,?)", (time.time(), kind, n, unit, text[:120]))
                today = c.execute("SELECT SUM(amount) FROM logs WHERE kind=? AND unit=? AND ts>=?",
                                  (kind, unit, _day_start())).fetchone()[0] or n
            extra = {"water": f" Today: {today:g} glasses (8 is a common goal).", "sleep": "",
                     "exercise": f" Today: {today:g} {plural(today, unit)}."}[kind]
            return {"reply": f"Logged {kind}: {n:g} {plural(n, unit)}.{extra}", "sources": []}
    return None


SINGULAR = {"glasses": "glass", "hrs": "hour", "hr": "hour", "h": "hour", "mins": "minute", "min": "minute",
            "kilometers": "km", "kilometer": "km", "kms": "km", "litres": "liter", "litre": "liter", "liters": "liter", "l": "liter"}


def unit_of(u):
    u = (u or "").lower()
    return SINGULAR.get(u, u[:-1] if u.endswith("s") and u not in ("glass",) else u)


def plural(n, unit):
    return unit if n == 1 or not unit or unit == "km" else ("glasses" if unit == "glass" else unit + "s")


def _day_start(days_ago=0):
    d = dt.date.today() - dt.timedelta(days=days_ago)
    return time.mktime(d.timetuple())


def streak(habit):
    with db() as c:
        days = {dt.date.fromtimestamp(t) for (t,) in c.execute("SELECT ts FROM logs WHERE kind='habit' AND note=?", (habit,))}
    n, d = 0, dt.date.today()
    while d in days:
        n, d = n + 1, d - dt.timedelta(days=1)
    return n


def report(text):
    week = bool(re.search(r"\bweek\b", text, I))
    since = _day_start(dt.date.today().weekday() if week else 0) if not re.search(r"\blast\s+week\b", text, I) else _day_start(7 + dt.date.today().weekday())
    label = "this week" if week else "today"
    if re.search(r"screen\s*time|apps\s+did\s+i\s+use", text, I):
        with db() as c:
            rows = c.execute("SELECT app, COUNT(*) FROM usage WHERE ts>=? GROUP BY app ORDER BY 2 DESC LIMIT 10", (since,)).fetchall()
        if not rows:
            return "No screen time recorded yet - it's sampled while ISHTI's server runs."
        total = sum(n for _, n in rows) * SAMPLE_S / 3600
        return f"Screen time {label}: **{total:.1f} h**\n" + "\n".join(f"- {app}: {n * SAMPLE_S / 60:.0f} min" for app, n in rows)
    with db() as c:
        rows = c.execute("SELECT kind, unit, SUM(amount), COUNT(*) FROM logs WHERE ts>=? AND kind!='habit' GROUP BY kind, unit", (since,)).fetchall()
        habits = c.execute("SELECT note, COUNT(DISTINCT date(ts, 'unixepoch', 'localtime')) FROM logs WHERE kind='habit' AND ts>=? GROUP BY note", (since,)).fetchall()
    if not rows and not habits:
        return f"Nothing logged {label}. Tell me like \"I drank 3 glasses of water\" or \"slept 7 hours\"."
    lines = [f"- {kind.title()}: {total:g} {plural(total, unit)}" + (f" (avg {total / n:.1f} per night)" if kind == "sleep" and n > 1 else "")
             for kind, unit, total, n in rows]
    lines += [f"- Habit '{h}': {d} day(s), streak {streak(h)}" for h, d in habits]
    return f"Your log {label}:\n" + "\n".join(lines)


_sampler = {"on": False}


def start_sampler():
    """Foreground app once a minute (process name + window title), local only."""
    if _sampler["on"]:
        return
    _sampler["on"] = True

    def loop():
        import psutil
        import win32gui
        import win32process
        while True:
            try:
                hwnd = win32gui.GetForegroundWindow()
                title = win32gui.GetWindowText(hwnd)
                _, pid = win32process.GetWindowThreadProcessId(hwnd)
                app = psutil.Process(pid).name().removesuffix(".exe") if pid > 0 else "idle"
                if title:
                    with db() as c:
                        c.execute("INSERT INTO usage VALUES(?,?,?)", (time.time(), app, title[:120]))
            except Exception:
                pass
            time.sleep(SAMPLE_S)
    threading.Thread(target=loop, daemon=True, name="screen-time").start()


if __name__ == "__main__":
    import tempfile
    DB = os.path.join(tempfile.mkdtemp(), "l.db")
    s = {}
    assert "Logged water: 3 glasses" in run("I drank 3 glasses of water", s)["reply"]
    assert "Today: 7 glasses" in run("had 1 liter of water", s)["reply"]
    assert "Logged sleep: 7 hours" in run("slept 7 hours", s)["reply"]
    assert "Logged exercise: 5 km." in run("ran 5 km", s)["reply"]
    assert "1 day streak" in run("did my reading habit", s)["reply"]
    rep = report("my habits today")
    assert "Water: 7 glasses" in rep and "Habit 'reading'" in rep, rep
    assert not match("what's the weather") and not match("run the tests")
    assert STUDY.search("I studied machine learning for 2 hours") and STUDY.search("did 45 minutes of calculus")
    r = run("I studied machine learning for 2 hours", {})["reply"]
    assert "120 min of machine learning" in r and "streak 1" in r, r
    run("revised calculus for 45 minutes", {})
    run("did 30 mins of machine learning", {})
    rep = run("show my learning progress", {})["reply"]
    assert rep.startswith("Learning, last 30 days: 3.2 h across 2 topic(s)") and "machine learning: 2.5 h over 2" in rep, rep
    print("[self-check] lifelog OK - water/sleep/exercise/habit logging, report, streak, learning log + progress")
