"""Focus sprints (Pomodoro): "start a 25 minute focus sprint on the thesis", "how long is left",
"stop the sprint", "focus history". The sprint is a due-time row the background loop (reminders'
start loop already runs every 20 s) checks; when it ends, ISHTI announces it and logs the minutes
focused for the day. Paused sprints keep their remaining seconds and resume where they stopped.
Everything is local SQLite (ishti.db, focus table) - no account, nothing leaves the PC."""
import datetime as dt
import json
import re
import threading
import time

from ishti.facts import get_facts
from ishti.skills import trace

I = re.I
UNIT = {"s": 1, "sec": 1, "second": 1, "m": 60, "min": 60, "minute": 60, "h": 3600, "hour": 3600}
START = re.compile(r"^(?:please\s+)?(?:start|begin|run)?\s*(?:a\s+|an\s+)?(?P<n>\d{1,3})\s*(?P<u>minute|min|hour|hr)s?\s+"
                   r"(?:focus\s+)?(?:sprint|pomodoro|focus\s+session|deep\s+work)\b(?:\s+(?:on|for)\s+(?P<what>.+?))?\s*$", I)
START2 = re.compile(r"^(?:please\s+)?focus\s+(?:sprint\s+)?(?:on\s+)?(?P<what>.+?)\s+(?:for\s+)?(?P<n>\d{1,3})\s*"
                    r"(?P<u>minutes?|mins?|hours?|hrs?)\s*$", I)
STOP = re.compile(r"^(?:please\s+)?(?:stop|cancel|end|abort|drop)\s+(?:the\s+|my\s+)?(?:focus\s+)?(?:sprint|pomodoro|session)\b", I)
PAUSE = re.compile(r"^(?:please\s+)?pause\s+(?:the\s+|my\s+)?(?:focus\s+)?(?:sprint|pomodoro|session)\b", I)
RESUME = re.compile(r"^(?:please\s+)?resume\s+(?:the\s+|my\s+)?(?:focus\s+)?(?:sprint|pomodoro|session)\b", I)
LEFT = re.compile(r"^(?:how\s+much\s+(?:time\s+)?is\s+left|how\s+long\s+is\s+left|time\s+left|how\s+much\s+longer)"
                  r"(?:\s+on\s+(?:the\s+|my\s+)?(?:focus\s+)?(?:sprint|pomodoro|session))?\s*[?.!]?$", I)
HISTORY = re.compile(r"\b(?:focus|pomodoro)\s+(?:history|stats?|log|report)\b|\bhow\s+(?:much\s+)?(?:have\s+i\s+)"
                     r"?focused\b|\bfocus\s+(?:this|per)\s+(?:week|day)\b", I)
DONE_DEFAULT = 25  # "start a focus sprint" with no length


def match(text):
    return bool(START.match(text.strip()) or START2.match(text.strip()) or STOP.match(text.strip())
                or PAUSE.match(text.strip()) or RESUME.match(text.strip()) or LEFT.match(text.strip())
                or HISTORY.search(text))


def _db():
    f = get_facts()
    with f.lock, f.db:
        f.db.execute("CREATE TABLE IF NOT EXISTS focus(id INTEGER PRIMARY KEY, task TEXT, started REAL, "
                     "due REAL, secs INTEGER, state TEXT DEFAULT 'running', left_s INTEGER, ended REAL, "
                     "completed INTEGER DEFAULT 0)")
    return f


def active(f=None):
    """The running or paused sprint, or None."""
    f = f or _db()
    with f.lock:
        row = f.db.execute("SELECT id, task, started, due, secs, state, left_s FROM focus "
                           "WHERE state IN ('running','paused') ORDER BY id DESC LIMIT 1").fetchone()
    return dict(zip(("id", "task", "started", "due", "secs", "state", "left_s"), row)) if row else None


def _fmt(secs):
    m, s = divmod(max(0, int(secs)), 60)
    return f"{m}:{s:02d}" if m < 60 else f"{m // 60}h {m % 60:02d}m"


def _start(f, n, u, what):
    if cur := active(f):
        return (f"A sprint is already running on '{cur['task'] or 'focus'}' with {_fmt(cur['due'] - time.time())} left - "
                "stop it first or ask how long is left.")
    secs = int(n * UNIT[u[:3] if u[:3] in UNIT else (u[0] if u[0] in UNIT else "m")])
    if secs < 60:
        return "That's under a minute - give the sprint at least 1 minute."
    task = (what or "").strip().strip("'\"") or ""
    with f.lock, f.db:
        f.db.execute("INSERT INTO focus(task, started, due, secs, state) VALUES(?,?,?,?, 'running')",
                     (task, time.time(), time.time() + secs, secs))
    trace({}, "focus", f"sprint started: {_fmt(secs)}" + (f" on {task}" if task else ""))
    return (f"Focus sprint started: {_fmt(secs)}" + (f" on {task}" if task else "")
            + ". I'll tell you when it's done. (\"pause the sprint\", \"stop the sprint\", \"how much time is left?\")")


def start_sprint(text):
    m = START.match(text.strip()) or START2.match(text.strip())
    return _start(_db(), int(m["n"]), m["u"], m["what"])


def op_stop(text, session=None):
    if cur := active():
        f = _db()
        state = "paused" if cur["state"] == "paused" else "running"
        done = int((time.time() - cur["started"]) if state == "running" else (cur["secs"] - cur["left_s"]))
        with f.lock, f.db:
            f.db.execute("UPDATE focus SET state='stopped', ended=?, completed=?, left_s=NULL WHERE id=?",
                         (time.time(), 1 if done >= cur["secs"] * 0.8 else 0, cur["id"]))
        mins = max(1, round(done / 60))
        return f"Sprint stopped - {mins} minute(s) focused, logged to your focus history."
    return "No focus sprint is running."


def op_pause(text, session=None):
    if (cur := active()) and cur["state"] == "running":
        f = _db()
        with f.lock, f.db:
            f.db.execute("UPDATE focus SET state='paused', left_s=? WHERE id=?", (int(cur["due"] - time.time()), cur["id"]))
        return f"Sprint paused with {_fmt(cur['due'] - time.time())} left - \"resume the sprint\" to continue."
    if cur := active():
        return f"The sprint is already paused ({_fmt(cur['left_s'])} left)."
    return "No focus sprint is running."


def op_resume(text, session=None):
    if (cur := active()) and cur["state"] == "paused":
        f = _db()
        with f.lock, f.db:
            f.db.execute("UPDATE focus SET state='running', due=? WHERE id=?", (time.time() + cur["left_s"], cur["id"]))
        return f"Sprint resumed - {_fmt(cur['left_s'])} left."
    if (cur := active()) and cur["state"] == "running":
        return f"The sprint is already running ({_fmt(cur['due'] - time.time())} left)."
    return "There's no paused sprint to resume."


def op_left(text, session=None):
    if not (cur := active()):
        return "No focus sprint is running - \"start a 25 minute focus sprint\" to begin one."
    if cur["state"] == "paused":
        return f"The sprint on {cur['task'] or 'focus'} is paused with {_fmt(cur['left_s'])} left."
    return f"{_fmt(cur['due'] - time.time())} left" + (f" on {cur['task']}" if cur["task"] else "") + "."


def op_history(text, session=None):
    f = _db()
    today = dt.date.today()
    with f.lock:
        rows = f.db.execute("SELECT started, secs, completed, state FROM focus WHERE state IN ('stopped','done') "
                            "AND started >= ?", ((time.time() - 7 * 86400),)).fetchall()
    if not rows:
        return "No finished sprints in the last 7 days yet."
    per_day = {}
    for started, secs, completed, state in rows:
        d = dt.datetime.fromtimestamp(started).date()
        mins = secs // 60 if completed else max(1, secs // 120)  # a stopped sprint counts roughly half
        per_day[d] = per_day.get(d, 0) + mins
    today_m = per_day.get(today, 0)
    week_m = sum(per_day.values())
    lines = [f"Focused {today_m} min today, {week_m} min in the last 7 days:", ""]
    for d in sorted(per_day, reverse=True)[:7]:
        lines.append(f"- {d.strftime('%a %d %b')}: {per_day[d]} min")
    return "\n".join(lines)


def fire_due(announce, run_task=None):
    """End-of-sprint announcement; called by the reminders loop tick like fire_due there."""
    f = _db()
    for cur in [active(f)]:
        if not cur or cur["state"] != "running" or time.time() < cur["due"]:
            continue
        with f.lock, f.db:
            f.db.execute("UPDATE focus SET state='done', ended=?, completed=1 WHERE id=?", (time.time(), cur["id"]))
        task = f" on {cur['task']}" if cur["task"] else ""
        announce(f"Focus sprint{task} is done - {cur['secs'] // 60} minutes focused. Take a 5 minute break.")


def start(announce, run_task=None):
    """Called once by the server's startup loop (like reminders/automation) to fire due sprints."""
    def loop():
        while True:
            try:
                fire_due(announce, run_task)
            except Exception as exc:
                print(f"[focus] {exc}")
            time.sleep(10)
    threading.Thread(target=loop, daemon=True, name="focus").start()


OPS = ((STOP, op_stop), (PAUSE, op_pause), (RESUME, op_resume), (LEFT, op_left))


def run(text, session):
    text = text.strip()
    if HISTORY.search(text):
        return {"reply": op_history(text), "sources": []}
    for rx, op in OPS:
        if rx.match(text):
            return {"reply": op(text, session), "sources": []}
    if START.match(text) or START2.match(text):
        return {"reply": start_sprint(text), "sources": []}
    return None


if __name__ == "__main__":
    import os
    import tempfile
    import ishti.facts as F
    F._facts = F.Facts(os.path.join(tempfile.mkdtemp(), "t.db"))
    assert match("start a 25 minute focus sprint on the thesis")
    assert match("focus on math for 50 minutes")
    assert match("stop the sprint") and match("pause the sprint") and match("resume the sprint")
    assert match("how much time is left?") and match("time left")
    assert match("focus history") and match("how much have I focused this week")
    assert not match("remind me in 20 minutes to call Ali") and not match("set a timer for 10 minutes")
    r = run("start a 25 minute focus sprint on the thesis", {})
    assert r["reply"].startswith("Focus sprint started: 25:00 on the thesis"), r
    r = run("start a 10 minute focus sprint", {})
    assert "already running" in r["reply"], r
    r = run("how much time is left?", {})
    assert "24:" in r["reply"] or "left on the thesis" in r["reply"], r
    assert "paused" in run("pause the sprint", {})["reply"]
    assert "resumed" in run("resume the sprint", {})["reply"]
    heard = []
    f = _db()
    cur = active(f)
    with f.lock, f.db:
        f.db.execute("UPDATE focus SET due=? WHERE id=?", (time.time() - 1, cur["id"]))
    fire_due(heard.append)
    assert heard and "is done" in heard[0], heard
    assert "Focused 25 min today" in run("focus history", {})["reply"]
    assert "No focus sprint" in run("stop the sprint", {})["reply"]  # the fired sprint is already done
    assert "Focus sprint started" in run("start a 15 minute focus sprint on code", {})["reply"]
    assert "Sprint stopped" in run("stop the sprint", {})["reply"] and "min today" in run("focus history", {})["reply"]
    print("[self-check] focus OK - start/pause/resume/left/stop, due announcement, history")
