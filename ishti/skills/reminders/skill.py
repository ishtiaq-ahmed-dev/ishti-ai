"""Reminders and timers.
  "remind me in 20 minutes to call Ali" / "remind me at 5pm to stretch" / "remind me tomorrow at 9 to pay rent"
  "set a timer for 10 minutes" / "list my reminders" / "cancel reminder 2" / "cancel the Ali reminder"
Stored in SQLite (ishti.db), fired by start() - a background loop the server runs - by voice + HUD.
Reminders due while ISHTI was off are announced once at the next start."""
import datetime as dt
import json
import re
import threading
import time

from ishti import llm
from ishti.facts import get_facts

I = re.I
UNIT = {"s": 1, "sec": 1, "second": 1, "m": 60, "min": 60, "minute": 60, "h": 3600, "hr": 3600, "hour": 3600, "day": 86400}
SET = re.compile(r"^(?:please\s+)?(?:remind\s+me\b(?!\s+(?:what|who|where|which|when\s+is|how))|set\s+(?:a\s+|an\s+)?(?:reminder|timer|alarm)\b|(?:start\s+)?a?\s*\d+\s*"
                 r"(?:second|sec|minute|min|hour|hr)s?\s+timer\b)", I)
LIST = re.compile(r"^(?:list|show|what\s+are)\s+(?:all\s+)?(?:my\s+)?(?:reminders|timers)|^(?:do\s+i\s+have\s+)?any\s+reminders", I)
CANCEL = re.compile(r"^(?:cancel|delete|remove|clear|stop)\s+(?:the\s+|my\s+)?(?P<all>all\s+(?:my\s+)?)?(?P<what>.*?)\s*"
                    r"(?:reminders?|timers?|alarms?)\s*(?P<num>\d+)?\W*$", I)
IN = re.compile(r"\b(?:in|for|after)\s+(?P<n>\d+(?:\.\d+)?|an?|one|half\s+an?)\s*(?P<u>seconds?|secs?|minutes?|mins?|hours?|hrs?|days?|[smh])\b", I)
AT = re.compile(r"\b(?P<tom>tomorrow\s+)?(?:at\s+)(?P<h>\d{1,2})(?::(?P<m>\d{2}))?\s*(?P<ap>am|pm|a\.m\.|p\.m\.)?(?P<tom2>\s+tomorrow)?", I)
TOMORROW_ONLY = re.compile(r"\btomorrow\b", I)
# Repeating schedules: "every day at 9", "every weekday at 8:30", "every monday at 10", "every 2 hours".
# With "remind me" it's a reminder; without, the request itself runs at that time ("every morning at 8 give me my brief").
DAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
RECUR = re.compile(r"\b(?:every|each)\s+(?P<per>day|morning|evening|night|weekday|(?:mon|tues|wednes|thurs|fri|satur|sun)day|"
                   r"(?P<n>\d+)\s*(?P<u>minutes?|mins?|hours?|hrs?))\b", I)


def _db():
    f = get_facts()
    with f.lock, f.db:
        f.db.execute("CREATE TABLE IF NOT EXISTS reminders(id INTEGER PRIMARY KEY, due REAL, text TEXT, "
                     "created REAL, done INTEGER DEFAULT 0)")
        cols = {r[1] for r in f.db.execute("PRAGMA table_info(reminders)")}
        if "repeat" not in cols:  # older databases: add the schedule columns in place
            f.db.execute("ALTER TABLE reminders ADD COLUMN repeat TEXT")
            f.db.execute("ALTER TABLE reminders ADD COLUMN task INTEGER DEFAULT 0")
    return f


def repeat_of(text):
    """"daily" / "weekdays" / "weekly:<n>" / "<seconds>s", or None."""
    m = RECUR.search(text)
    if not m:
        return None
    per = m["per"].lower()
    if m["n"]:
        return f"every:{int(m['n']) * (3600 if m['u'].lower().startswith('h') else 60)}"
    if per == "weekday":
        return "weekdays"
    if per in DAYS:
        return f"weekly:{DAYS.index(per)}"
    return "daily"


def first_due(text, repeat, now):
    """First time a schedule fires: its time of day (default by morning/evening/night), on the right day."""
    if repeat.startswith("every:"):
        return now + dt.timedelta(seconds=int(repeat[6:]))
    per = RECUR.search(text)["per"].lower()
    h, mi = {"morning": 8, "evening": 18, "night": 21}.get(per, 9), 0
    if at := AT.search(text):  # a schedule's "at 9" is 9:00 (one-off "at 5" said at 14:00 means 17:00; not here)
        h, mi, ap = int(at["h"]) % 24, int(at["m"] or 0), (at["ap"] or "").replace(".", "").lower()
        h = h % 12 + (12 if ap == "pm" else 0) if ap else (h + 12 if per in ("evening", "night") and h < 12 else h)
    due = now.replace(hour=h, minute=mi, second=0, microsecond=0)
    while due <= now or not _fits(due, repeat):
        due += dt.timedelta(days=1)
    return due


def _fits(d, repeat):
    return repeat == "daily" or (repeat == "weekdays" and d.weekday() < 5) or (repeat.startswith("weekly:") and d.weekday() == int(repeat[7:]))


def next_due(due, repeat, now):
    d = dt.datetime.fromtimestamp(due)
    step = dt.timedelta(seconds=int(repeat[6:])) if repeat.startswith("every:") else dt.timedelta(days=1)
    d += step
    while d.timestamp() <= now or (not repeat.startswith("every:") and not _fits(d, repeat)):
        d += step  # missed while ISHTI was off: skip ahead, don't replay every missed one
    return d.timestamp()


def when(text, now: dt.datetime):
    """Due datetime from common phrasings, or None (then the LLM tries)."""
    if m := IN.search(text):
        n = m["n"].lower()
        n = 0.5 if n.startswith("half") else 1.0 if n in ("a", "an", "one") else float(n)
        u = m["u"].lower().rstrip("s") or "s"
        return now + dt.timedelta(seconds=n * UNIT.get(u, UNIT.get(u[:3], 60)))
    if m := AT.search(text):
        h, mi, ap = int(m["h"]), int(m["m"] or 0), (m["ap"] or "").replace(".", "").lower()
        if h > 23 or mi > 59:
            return None
        h = h % 12 + (12 if ap == "pm" else 0) if ap else h
        due = now.replace(hour=h, minute=mi, second=0, microsecond=0)
        if m["tom"] or m["tom2"]:
            return due + dt.timedelta(days=1)
        if due <= now and not ap and h < 12 and (due + dt.timedelta(hours=12)) > now:
            return due + dt.timedelta(hours=12)  # "at 5" said at 14:00 means 17:00
        return due if due > now else due + dt.timedelta(days=1)
    return None


def what(text):
    t = SET.sub("", text.strip()).strip()
    for rx in (IN, AT, TOMORROW_ONLY):
        t = rx.sub("", t)
    t = re.sub(r"^\s*(?:to|that|about|of)\s+", "", t.strip(" ,.!"), flags=I)
    return re.sub(r"\s+(?:to|that)$", "", t).strip(" ,.!")


def _fmt(due: float, now: dt.datetime):
    d = dt.datetime.fromtimestamp(due)
    day = "" if d.date() == now.date() else " tomorrow" if d.date() == now.date() + dt.timedelta(days=1) \
        else d.strftime(" on %a %d %b")
    secs = due - now.timestamp()
    mins = round(secs / 60)
    rel = f"in {round(secs)} s" if secs < 90 else f"in {mins} min" if mins < 120 else f"in {round(mins / 60, 1)} h"
    return f"{d.strftime('%H:%M')}{day} ({rel})"


def match(text):
    t = text.strip()
    return bool(SET.match(t) or LIST.match(t) or CANCEL.match(t)
                or (re.match(r"^(?:please\s+)?(?:every|each)\s", t, I) and RECUR.search(t)))


def _pending(f):
    with f.lock:
        return f.db.execute("SELECT id, due, text FROM reminders WHERE done=0 ORDER BY due").fetchall()


def run(text, session, now=None):
    now, f, t = now or dt.datetime.now(), _db(), text.strip()
    if LIST.match(t):
        rows = _pending(f)
        return {"reply": "\n".join(f"{i}. {_fmt(d, now)}: {x}" for i, (_, d, x) in enumerate(rows, 1))
                         or "No reminders set.", "sources": []}
    if (m := CANCEL.match(t)) and not SET.match(t):
        rows = _pending(f)
        if m["all"]:
            hit = rows
        elif m["num"]:
            hit = rows[int(m["num"]) - 1: int(m["num"])]
        else:
            words = m["what"].lower().split()
            hit = [r for r in rows if words and all(w in r[2].lower() for w in words)] or rows[:1] * (not words)
        with f.lock, f.db:
            f.db.executemany("UPDATE reminders SET done=1 WHERE id=?", [(r[0],) for r in hit])
        return {"reply": ("Cancelled: " + "; ".join(r[2] for r in hit)) if hit else "No matching reminder.",
                "sources": []}
    if repeat := repeat_of(t):
        task = not re.search(r"\bremind\s+me\b", t, I)
        body = RECUR.sub("", AT.sub("", t)).strip(" ,.")
        body = re.sub(r"^(?:please\s+)?(?:remind\s+me\s+)?(?:to\s+)?", "", body, flags=I).strip(" ,.") or "Reminder"
        due = first_due(t, repeat, now)
        with f.lock, f.db:
            f.db.execute("INSERT INTO reminders(due, text, created, repeat, task) VALUES(?,?,?,?,?)",
                         (due.timestamp(), body, time.time(), repeat, int(task)))
        every = {"daily": "every day", "weekdays": "every weekday"}.get(repeat) or (
            f"every {DAYS[int(repeat[7:])].title()}" if repeat.startswith("weekly") else f"every {int(repeat[6:]) // 60} min")
        return {"reply": f"{'Scheduled' if task else 'Repeating reminder'} {every}"
                         + (f" at {due:%H:%M}" if not repeat.startswith("every:") else "")
                         + f": {body}. First: {_fmt(due.timestamp(), now)}." + (" I'll do it and tell you the result." if task else ""),
                "sources": []}
    due, label = when(t, now), what(t)
    if due is None:  # unusual phrasing: let the local LLM read it
        out = llm.ask(f"Now is {now:%Y-%m-%d %H:%M} ({now:%A}). From this request return JSON "
                      f'{{"due": "YYYY-MM-DD HH:MM", "what": "<what to remind>"}}.\nRequest: {t}',
                      system=None, temperature=0, max_tokens=80, response_format={"type": "json_object"})
        try:
            d = json.loads(out)
            due, label = dt.datetime.strptime(d["due"], "%Y-%m-%d %H:%M"), d.get("what") or label
        except (ValueError, KeyError, TypeError):
            return {"reply": "When? Say e.g. 'in 20 minutes' or 'at 5pm'.", "sources": []}
    if due <= now:
        return {"reply": f"That time ({due:%H:%M}) has already passed.", "sources": []}
    label = label or ("Timer done" if re.search(r"\btimer\b", t, I) else "Reminder")
    with f.lock, f.db:
        f.db.execute("INSERT INTO reminders(due, text, created) VALUES(?,?,?)", (due.timestamp(), label, time.time()))
    return {"reply": f"Reminder set for {_fmt(due.timestamp(), now)}: {label}.", "sources": []}


def fire_due(announce, now=None, run_task=None):
    """Announce (or, for scheduled tasks, run) everything due; repeating ones move to their next time.
    run_task(text) -> answer; the server passes the dispatcher. Returns how many fired."""
    f, now = _db(), now or time.time()
    with f.lock:
        rows = f.db.execute("SELECT id, due, text, repeat, task FROM reminders WHERE done=0 AND due<=?", (now,)).fetchall()
    for rid, due, text, repeat, task in rows:
        late = now - due > 120
        if task and run_task:
            threading.Thread(target=lambda t=text: announce(run_task(t)), daemon=True).start()
        else:
            announce(f"Reminder{' (missed while I was off, due ' + dt.datetime.fromtimestamp(due).strftime('%H:%M') + ')' if late else ''}: {text}")
        with f.lock, f.db:
            if repeat:
                f.db.execute("UPDATE reminders SET due=? WHERE id=?", (next_due(due, repeat, now), rid))
            else:
                f.db.execute("UPDATE reminders SET done=1 WHERE id=?", (rid,))
    return len(rows)


def start(announce, run_task=None):
    """Background loop (1 s tick) the server starts once."""
    def loop():
        while True:
            try:
                fire_due(announce, run_task=run_task)
            except Exception as exc:
                print(f"[reminders] {exc}")
            time.sleep(1)
    threading.Thread(target=loop, daemon=True, name="reminders").start()


if __name__ == "__main__":
    import os
    import tempfile
    import ishti.facts as F
    F._facts = F.Facts(os.path.join(tempfile.mkdtemp(), "t.db"))
    now = dt.datetime(2026, 9, 26, 14, 0)
    assert when("remind me in 20 minutes to call Ali", now) == now + dt.timedelta(minutes=20)
    assert when("set a timer for half an hour", now) == now + dt.timedelta(minutes=30)
    assert when("remind me at 5pm to stretch", now).hour == 17
    assert when("remind me at 5 to stretch", now).hour == 17            # 5 said at 14:00 = 17:00
    assert when("remind me at 9am to x", now).day == 27                  # passed today -> tomorrow
    assert when("remind me tomorrow at 9 to pay rent", now) == dt.datetime(2026, 9, 27, 9, 0)
    assert what("remind me in 20 minutes to call Ali") == "call Ali"
    assert what("remind me to call Ali in 20 minutes") == "call Ali"
    assert match("set a timer for 10 minutes") and match("list my reminders") and match("cancel reminder 2")
    assert not match("remind me what my sister's birthday is")  # a memory question, not a reminder
    r = run("remind me in 20 minutes to call Ali", {}, now)["reply"]
    assert "14:20" in r and "call Ali" in r, r
    run("set a timer for 10 minutes", {}, now)
    assert run("list my reminders", {}, now)["reply"].startswith("1. 14:10")
    assert "call Ali" in run("cancel the Ali reminder", {}, now)["reply"]
    heard = []
    assert fire_due(heard.append, now=(now + dt.timedelta(minutes=11)).timestamp()) == 1 and "Timer" in heard[0]
    assert run("list my reminders", {}, now)["reply"] == "No reminders set."
    r = run("every weekday at 9 give me my brief", {}, now)["reply"]
    assert r.startswith("Scheduled every weekday at 09:00") and "give me my brief" in r, r
    ran = []
    fire_due(heard.append, now=dt.datetime(2026, 9, 28, 9, 0, 5).timestamp(), run_task=lambda t: ran.append(t) or "BRIEF")
    time.sleep(0.2)
    assert ran == ["give me my brief"] and "BRIEF" in heard, (ran, heard)
    with F._facts.lock:
        nxt = F._facts.db.execute("SELECT due FROM reminders WHERE repeat='weekdays'").fetchone()[0]
    assert dt.datetime.fromtimestamp(nxt) == dt.datetime(2026, 9, 29, 9, 0), dt.datetime.fromtimestamp(nxt)
    r = run("remind me every day at 8pm to take my medicine", {}, now)["reply"]
    assert r.startswith("Repeating reminder every day at 20:00") and "take my medicine" in r, r
    assert next_due(dt.datetime(2026, 9, 25, 9).timestamp(), "weekdays", dt.datetime(2026, 9, 26, 10).timestamp()) == dt.datetime(2026, 9, 28, 9).timestamp()
    print("[self-check] reminders OK - parse in/at/tomorrow, set, list, cancel, fire, repeating schedules and tasks")
