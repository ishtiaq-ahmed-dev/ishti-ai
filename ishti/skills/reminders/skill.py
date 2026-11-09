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
MIN_TASK_EVERY_S = 900  # scheduled tasks (not plain reminders) repeat at most every 15 min
UNIT = {"s": 1, "sec": 1, "second": 1, "m": 60, "min": 60, "minute": 60, "h": 3600, "hr": 3600, "hour": 3600, "day": 86400}
# A secret ASSIGNED a value ("my password is Hunter2", "api key: sk-...", "the 2FA code is 918273") must
# not be stored: reminder text is plain text in the task list and is shown on screen when it fires (same
# policy as the chat honesty guard and the clipboard ring, and what the reviewer secret-bait pass checks).
# Deterministic, so a busy or agreeable model can never make the skill store a secret.
_SECRET_ASSIGN = re.compile(
    r"\b(?:password|passwd|passphrase|api[\s_-]?key|secret[\s_-]?key|access[\s_-]?token|"
    r"(?:seed|recovery)[\s_-]*(?:phrase|words)|(?:2fa|otp)(?:\s+code)?|one[-\s]time\s*code|"
    r"credit\s*card(?:\s+number)?|cvv|cvc|pin)\b\s*(?:is|are|was|were|:|=)\s*\S", I)
# "in 2 minutes remind me to drink water" / "tomorrow at 9 remind me ...": the time may come first
SET = re.compile(r"^(?:please\s+)?(?:(?:in|after)\s+[\w .]{1,20}?\s+|(?:tomorrow|today|tonight)\s+(?:at\s+[\w:.]+\s*(?:am|pm)?\s+)?|at\s+[\w:.]+\s*(?:am|pm)?\s+)?(?:remind\s+me\b(?!\s+(?:what|who|where|which|when\s+is|how))|(?:set|add|create|make|put)\s+(?:a\s+|an\s+|the\s+|one\s+)?(?:reminder|timer|alarm)\b|(?:start\s+)?a?\s*\d+\s*"
                 r"(?:second|sec|minute|min|hour|hr)s?\s+timer\b)", I)
# "I have to submit the assignment as well" / "don't let me forget to pay the bill" / "add a task: call the bank": a to-do the owner
# states in passing. It goes to Tasks with a sensible default reminder (owner's chat: nothing was added and ISHTI asked for a file path).
TODO = re.compile(r"^\s*(?:and\s+|also\s+|oh\s+|ok(?:ay)?\s+)?(?:d?i\s+(?:also\s+|still\s+)?(?:have|need|got|gotta)\s+to|i\s+must|"
                  r"don'?t\s+let\s+me\s+forget(?:\s+to)?|add\s+(?:a\s+|this\s+)?(?:task|to-?do)\s*(?:to\s+|:|-)?|to-?do\s*:?)\s+(?P<what>.{3,200}?)\s*[.!]?$", I)
NOT_TODO = re.compile(r"^(?:say|admit|agree|tell\s+you|ask\s+you|know|see|check|send|open|search|play|find|go\s+now|leave\s+now|be\s+honest)\b", I)
LIST = re.compile(r"^(?:list|show|what\s+are)\s+(?:all\s+)?(?:my\s+)?(?:reminders|timers|tasks)|^(?:do\s+i\s+have\s+)?any\s+(?:reminders|tasks)"
                  r"|^what'?s?\s+(?:is\s+|are\s+)?(?:the\s+|my\s+)?(?:rem[aeio]nd[aeo]rs?|tasks?|to-?dos?)\b"  # "whats the remonder" (typed fast)
                  r"|^what\s+(?:do\s+i\s+have\s+to\s+do|is\s+on\s+my\s+list)\b|^what'?s\s+on\s+my\s+(?:list|plate|agenda|schedule|to-?do)", I)
# "cancel the reminder to drink water" / "delete the task about the rent"
CANCEL_ABOUT = re.compile(r"^(?:cancel|delete|remove|clear|stop)\s+(?:the\s+|my\s+)?(?:reminder|timer|alarm|task)\s+(?:to|for|about|of)\s+(?P<what>.+?)\W*$", I)
# "Move my 3pm thing to tomorrow same time" / "reschedule the dentist reminder to Friday": a reminder
# move, not a file move (checklist #15). The thing being moved is a scheduled item and the destination
# is a time (tomorrow, Friday, 5pm, next week) - never a folder; file moves are fileops'.
RESCHEDULE = re.compile(r"^\s*(?:please\s+)?(?:move|reschedule|shift|push|change)\s+(?P<what>.+?)\s+to\s+"
                        r"(?P<when>(?:tomorrow|today|tonight|next\s+(?:week|month)|monday|tuesday|wednesday|thursday|"
                        r"friday|saturday|sunday|\d{1,2}(?::\d{2})?\s*(?:am|pm)?|the\s+same\s+time))\b", I)
# "mark drink water as done" / "I finished the assignment" / "done with the rent"
DONE = re.compile(r"^(?:mark|tick|check\s+off|set)\s+(?:the\s+)?(?P<what>.+?)\s+(?:as\s+)?(?:done|complete|completed|finished)\W*$"
                  r"|^(?:i\s+)?(?:have\s+)?(?:done|did|finished|completed)\s+(?:with\s+)?(?:the\s+)?(?P<what2>.+?)\W*$", I)
CANCEL = re.compile(r"^(?:cancel|delete|remove|clear|stop)\s+(?:the\s+|my\s+)?(?P<all>all\s+(?:my\s+)?)?(?P<what>.*?)\s*"
                    r"(?:reminders?|timers?|alarms?)\s*(?P<num>\d+)?\W*$", I)
IN = re.compile(r"\b(?:in|for|after)\s+(?P<n>\d+(?:\.\d+)?|an?|one|half\s+an?)\s*(?P<u>seconds?|secs?|minutes?|mins?|hours?|hrs?|days?|[smh])\b", I)
AT = re.compile(r"\b(?P<tom>tomorrow\s+)?(?:at\s+)(?P<h>\d{1,2})(?::(?P<m>\d{2}))?\s*(?P<ap>am|pm|a\.m\.|p\.m\.)?(?P<tom2>\s+tomorrow)?", I)
TOMORROW_ONLY = re.compile(r"\btomorrow\b", I)
# Repeating schedules: "every day at 9", "every weekday at 8:30", "every monday at 10", "every 2 hours".
# With "remind me" it's a reminder; without, the request itself runs at that time ("every morning at 8 give me my brief").
DAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
# ---- spoken durations (WP-JEV JEV-17) -------------------------------------------------
_NUMW = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
         "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
         "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17,
         "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40, "fourty": 40,
         "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90, "quarter": 15}
_DUR_UNIT = {"second": 1, "sec": 1, "s": 1, "minute": 60, "min": 60, "m": 60,
             "hour": 3600, "hr": 3600, "h": 3600, "day": 86400}
_DUR_CLAUSE = re.compile(r"\b(?:in|for|after)\s+(?P<clause>[^,]+?)(?=\s+(?:to|that|about|please|then|now)\b|[.!?]|$)", I)
_DUR_PAIR = re.compile(r"(\d+(?:\.\d+)?)\s*(seconds?|secs?|minutes?|mins?|hours?|hrs?|days?|[smh])\b", I)
RECUR = re.compile(r"\b(?:every|each)\s+(?P<per>day|morning|evening|night|weekday|month|(?:mon|tues|wednes|thurs|fri|satur|sun)day|"
                   r"(?P<n>\d+)\s*(?P<u>minutes?|mins?|hours?|hrs?))\b|\b(?P<monthly>monthly)\b", I)
DAY_OF_MONTH = re.compile(r"\b(?:on\s+)?(?:the\s+)?(?P<d>[12]?\d|3[01])(?:st|nd|rd|th)\b", I)
# a reminder whose trigger is a CONDITION, not a time ("only if I haven't left by 5pm")
_CONDITIONAL = re.compile(r"\b(?:only\s+(?:if|when)|but\s+only\s+if|unless|provided\s+that|"
                          r"if\s+i\s+(?:have(?:n'?t|\s+not)?|haven'?t|don'?t|do\s+not|am\s+not|'m\s+not))\b", I)


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
    per = (m["per"] or "month").lower()
    if per == "month":  # bills: "on the 12th of every month" (default: today's date)
        d = DAY_OF_MONTH.search(text)
        return f"monthly:{int(d['d']) if d else dt.date.today().day}"
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
    per = (RECUR.search(text)["per"] or "month").lower()
    h, mi = {"morning": 8, "evening": 18, "night": 21}.get(per, 9), 0
    if at := AT.search(text):  # a schedule's "at 9" is 9:00 (one-off "at 5" said at 14:00 means 17:00; not here)
        h, mi, ap = int(at["h"]) % 24, int(at["m"] or 0), (at["ap"] or "").replace(".", "").lower()
        h = h % 12 + (12 if ap == "pm" else 0) if ap else (h + 12 if per in ("evening", "night") and h < 12 else h)
    due = now.replace(hour=h, minute=mi, second=0, microsecond=0)
    while due <= now or not _fits(due, repeat):
        due += dt.timedelta(days=1)
    return due


def _fits(d, repeat):
    if repeat.startswith("monthly:"):  # the 31st in a 30-day month fires on the 30th
        import calendar
        return d.day == min(int(repeat[8:]), calendar.monthrange(d.year, d.month)[1])
    return repeat == "daily" or (repeat == "weekdays" and d.weekday() < 5) or (repeat.startswith("weekly:") and d.weekday() == int(repeat[7:]))


def next_due(due, repeat, now):
    d = dt.datetime.fromtimestamp(due)
    step = dt.timedelta(seconds=int(repeat[6:])) if repeat.startswith("every:") else dt.timedelta(days=1)
    d += step
    while d.timestamp() <= now or (not repeat.startswith("every:") and not _fits(d, repeat)):
        d += step  # missed while ISHTI was off: skip ahead, don't replay every missed one
    return d.timestamp()


def _duration_seconds(text: str):
    """Spoken-duration vocabulary (WP-JEV / matrix JEV-17; behaviour adopted from hey-jev's
    parse_duration, original implementation): 'twenty five minutes', 'a couple of', 'a few',
    'an hour and a half', 'an hour 30 minutes', 'three quarters of an hour'. Returns total seconds
    or None (then the caller falls back to the IN/AT/LLM paths exactly as before)."""
    m = _DUR_CLAUSE.search(text)
    if not m:
        return None
    c = " " + m.group("clause").lower() + " "
    c = re.sub(r"\b(?:three|3)\s+quarters?\s+of\s+(?:an?\s+)?hours?\b", "45 minutes", c)
    c = re.sub(r"\b(?:a\s+|one\s+)?quarters?\s+of\s+(?:an?\s+)?hours?\b", "15 minutes", c)
    # 'an hour and a half' / '2 hours and a half'  ->  '1.5 hour' / '2.5 hours'
    c = re.sub(r"\b(an?|one|\d+(?:\.\d+)?)\s+(seconds?|secs?|minutes?|mins?|hours?|hrs?)\s+and\s+a\s+half\b",
               lambda g: f"{_qty(g.group(1)) + 0.5} {g.group(2)}", c)
    # '1 and a half hours' -> '1.5 hours'
    c = re.sub(r"\b(an?|one|\d+(?:\.\d+)?)\s+and\s+a\s+half\s+(seconds?|secs?|minutes?|mins?|hours?|hrs?)\b",
               lambda g: f"{_qty(g.group(1)) + 0.5} {g.group(2)}", c)
    # 'half an hour' -> '0.5 hour'
    c = re.sub(r"\bhalf\s+an?\s+(seconds?|secs?|minutes?|mins?|hours?|hrs?)\b", r"0.5 \1", c)
    c = re.sub(r"\b(?:a\s+)?couple(?:\s+of)?\b", "2", c)
    c = re.sub(r"\ba\s+few\b", "3", c)
    words = c.replace("-", " ").split()
    out = []
    for w in words:
        if w in _NUMW:
            out.append(str(_NUMW[w]))
        else:
            out.append(w)
    c = " " + " ".join(out) + " "
    # spoken tens+ones landed as adjacent digits: '20 5 minutes' -> '25 minutes'
    c = re.sub(r"\b(\d{2})\s+(\d)\b(?=\s*(?:seconds?|secs?|minutes?|mins?|hours?|hrs?|days?|[smh])\b)",
               lambda g: str(int(g.group(1)) + int(g.group(2))), c)
    total = 0.0
    found = False
    for num, unit in _DUR_PAIR.findall(c):
        found = True
        total += float(num) * _DUR_UNIT[unit.rstrip("s") if len(unit) > 1 else unit]
    return int(total) if found and total > 0 else None


def _qty(tok: str) -> float:
    return float(_NUMW.get(tok, tok))


def when(text, now: dt.datetime):
    """Due datetime from common phrasings, or None (then the LLM tries)."""
    if secs := _duration_seconds(text):        # spoken numbers first (JEV-17), then the digit forms
        return now + dt.timedelta(seconds=secs)
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


def todo(text):
    m = TODO.match(text.strip())
    return m if m and not NOT_TODO.match(m["what"]) and not text.strip().endswith("?") else None


def default_due(now):
    """A to-do with no time: remind tonight at 20:00, or tomorrow 09:00 when it is already evening."""
    due = now.replace(hour=20, minute=0, second=0, microsecond=0)
    return due if now < due - dt.timedelta(hours=1) else (now + dt.timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)


def _done_rows(t):
    """The pending reminders/tasks a "mark X as done" names (every word of X in the task text), else []."""
    m = DONE.match(t.strip())
    if not m:
        return []
    words = [w for w in re.findall(r"[a-z0-9']+", (m["what"] or m["what2"] or "").lower()) if w not in ("the", "my", "a", "task", "reminder")]
    return [r for r in _pending(_db()) if words and all(w in r[2].lower() for w in words)]


WHEN_DUE = re.compile(r"^\s*when\s+is\s+(?:my\s+)?(?P<what>[\w\s.-]+?)\s+due\??\s*$", I)


def match(text):
    t = text.strip()
    return bool(SET.match(t) or LIST.match(t) or CANCEL.match(t) or CANCEL_ABOUT.match(t) or _done_rows(t) or todo(t)
                or RESCHEDULE.match(t) or WHEN_DUE.match(t)
                or (re.match(r"^(?:please\s+)?(?:every|each)\s", t, I) and RECUR.search(t)))


def _pending(f):
    with f.lock:
        return f.db.execute("SELECT id, due, text FROM reminders WHERE done=0 ORDER BY due").fetchall()


def run(text, session, now=None):
    now, f, t = now or dt.datetime.now(), _db(), text.strip()
    # a CONDITIONAL reminder ("remind me ... only if I haven't left by 5pm") cannot be honoured: the
    # engine fires at fixed times / simple repeats, not on an arbitrary condition (checklist 18). Say so
    # and offer the closest fixed-time alternative instead of silently setting an unconditional reminder.
    p = (session or {}).get("pending")
    if p and p.get("skill") == "reminders" and p.get("action"):
        if re.match(r"^\s*(?:yes|y|yeah|yep|confirm|sure|ok|okay|do\s+it)\b", t, I):
            session["pending"] = None
            return {"reply": p["action"](), "sources": []}
        if re.match(r"^\s*(?:no|n|nope|cancel|stop|don'?t|keep)\b", t, I):
            session["pending"] = None
            return {"reply": "OK - I kept your reminders.", "sources": []}
    if _SECRET_ASSIGN.search(t):
        return {"reply": "That one contains a password, key or code, so I won't put it in your reminders - "
                         "reminder text is kept as plain text in your task list and shown on screen when it "
                         "fires. Save it in the credential vault instead (\"save my login for <site>: user ... "
                         "password ...\"), or I can remind you about it WITHOUT the secret (\"remind me to "
                         "rotate that password at 5pm\").", "sources": []}
    if re.search(r"\b(?:yesterday|last\s+(?:night|week|month|monday|tuesday|wednesday|thursday|friday|saturday|sunday))\b", t, I) \
            and not re.search(r"\b(?:since|from|about|what\s+i\s+did)\b", t, I) and (SET.match(t) or re.search(r"\breminder\b", t, I)):
        # "set a reminder for yesterday at 5pm" was set for tomorrow, text "for yesterday"
        return {"reply": "That time has already passed - reminders only go forward. Did you mean today or tomorrow at "
                         "that time?", "sources": []}
    if SET.match(t) and re.search(r"\b(?:don'?t|do\s+not|never)\s+remind\b", t, I):
        # "remind me at 5pm and also don't remind me at 5pm" was set as a reminder with that text
        return {"reply": "You asked me both to remind you and not to - which one? Say \"remind me at ...\" to set it, "
                         "or nothing and I'll leave it.", "sources": []}
    if SET.match(t) and _CONDITIONAL.search(t):
        return {"reply": "I can't set a reminder that only fires on a condition - I do fixed times and "
                         "simple every-day repeats, not 'only if X'. Closest thing I can do: set a plain "
                         "reminder at 5pm and you cancel it if you've already left. Want that?", "sources": []}
    if LIST.match(t):
        rows = _pending(f)
        return {"reply": "\n".join(f"{i}. {_fmt(d, now)}: {x}" for i, (_, d, x) in enumerate(rows, 1))
                         or "No reminders set.", "sources": []}
    if m := WHEN_DUE.match(t):
        due_item = m.group("what").strip()
        words = [w for w in re.findall(r"[a-z0-9']+", due_item.lower()) if w not in ("the", "my", "a", "is", "assignment")]
        hit = [r for r in _pending(f) if words and any(w in r[2].lower() for w in words)]
        if hit:
            return {"reply": f"Your {due_item} is due {_fmt(hit[0][1], now)}: {hit[0][2]}.", "sources": []}
        return {"reply": f"No deadline on file for that {due_item}. Would you like me to set a reminder or schedule it?", "sources": []}
    if rows := _done_rows(t):
        with f.lock, f.db:
            f.db.executemany("UPDATE reminders SET done=1 WHERE id=?", [(r[0],) for r in rows])
        return {"reply": "Done: " + "; ".join(r[2] for r in rows) + ". It moved to Done in Tasks.", "sources": []}
    if m := CANCEL_ABOUT.match(t):
        words = [w for w in re.findall(r"[a-z0-9']+", m["what"].lower()) if w not in ("the", "my", "a")]
        hit = [r for r in _pending(f) if words and all(w in r[2].lower() for w in words)]
        with f.lock, f.db:
            f.db.executemany("UPDATE reminders SET done=1 WHERE id=?", [(r[0],) for r in hit])
        return {"reply": ("Cancelled: " + "; ".join(r[2] for r in hit)) if hit else f"I don't have a reminder about \"{m['what']}\".",
                "sources": []}
    if m := RESCHEDULE.match(t):
        # "Move my 3pm thing to tomorrow same time" (checklist #15): find the scheduled item the owner
        # named ("3pm thing"), ask for the exact one when ambiguous, and never silently create a new item.
        # NB: the target is called `item`, not `what` - a local named `what` shadows the module-level
        # what() helper that the SET path below calls (UnboundLocalError).
        item, when_txt = m["what"].strip(" .,!?\"'"), m["when"].strip()
        rows = _pending(f)
        words = [w for w in re.findall(r"[a-z0-9']+", item.lower()) if w not in ("the", "my", "a", "thing", "reminder", "task")]
        at = re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b", item, I)
        if at:  # "my 3pm thing": match by the hour it is due at
            hour = int(at.group(1)) % 12 + (12 if (at.group(3) or "").lower() == "pm" else 0)
            hit = [r for r in rows if dt.datetime.fromtimestamp(r[1]).hour == hour]
        else:
            hit = [r for r in rows if words and all(w in r[2].lower() for w in words)]
        if not hit:
            return {"reply": f"Nothing on your list matches \"{item}\" - I didn't create anything new. "
                             "Say 'list my reminders' to see what is set.", "sources": []}
        if len(hit) > 1:
            return {"reply": "More than one matches: " + "; ".join(r[2] for r in hit[:5])
                             + ". Which one should I move?", "sources": []}
        rid, due, body = hit[0]
        new_due = when(when_txt, now)
        if new_due is None and "same time" in when_txt.lower():
            new_due = (dt.datetime.fromtimestamp(due) + dt.timedelta(days=1)).replace(
                hour=dt.datetime.fromtimestamp(due).hour, minute=dt.datetime.fromtimestamp(due).minute)
        if new_due is None:
            return {"reply": f"When should I move \"{body}\" to? Give me a time (tomorrow 9am, Friday 5pm...).",
                    "sources": []}
        with f.lock, f.db:
            f.db.execute("UPDATE reminders SET due=? WHERE id=?", (new_due.timestamp(), rid))
        return {"reply": f"Moved \"{body}\" to {_fmt(new_due.timestamp(), now)}.", "sources": []}
    if (m := CANCEL.match(t)) and not SET.match(t):
        rows = _pending(f)
        # "delete ALL my reminders" is destructive and not undoable in one line - recap the count and
        # require an explicit yes first (checklist 17). A single named/numbered cancel still acts.
        if m["all"] and rows and (session or {}).get("pending") is None:
            ids = [r[0] for r in rows]

            def _wipe():
                with f.lock, f.db:
                    f.db.executemany("UPDATE reminders SET done=1 WHERE id=?", [(i,) for i in ids])
                return f"Deleted all {len(ids)} reminder(s)."

            session["pending"] = {"skill": "reminders", "action": _wipe, "at": time.time()}
            return {"reply": f"You have {len(rows)} reminder(s) set. Delete all of them? Say 'yes' to "
                             f"confirm.", "sources": []}
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
        if repeat.startswith("monthly:"):
            body = re.sub(r"\s*\b(?:on\s+)?(?:the\s+)?(?:[12]?\d|3[01])(?:st|nd|rd|th)(?:\s+of)?\b", "", body, flags=I).strip(" ,.")
        body = re.sub(r"^(?:please\s+)?(?:remind\s+me\s+)?(?:to\s+)?", "", body, flags=I).strip(" ,.") or "Reminder"
        if task and repeat.startswith("every:") and int(repeat[6:]) < MIN_TASK_EVERY_S:
            repeat = f"every:{MIN_TASK_EVERY_S}"  # a scheduled TASK runs a request each time: never more often than this
        due = first_due(t, repeat, now)
        every = {"daily": "every day", "weekdays": "every weekday"}.get(repeat) or (
            f"every {DAYS[int(repeat[7:])].title()}" if repeat.startswith("weekly") else
            f"on day {repeat[8:]} of every month" if repeat.startswith("monthly") else f"every {int(repeat[6:]) // 60} min")

        def schedule():
            with f.lock, f.db:
                f.db.execute("INSERT INTO reminders(due, text, created, repeat, task) VALUES(?,?,?,?,?)",
                             (due.timestamp(), body, time.time(), repeat, int(task)))
            return {"reply": f"{'Scheduled' if task else 'Repeating reminder'} {every}"
                             + (f" at {due:%H:%M}" if not repeat.startswith("every:") else "")
                             + f": {body}. First: {_fmt(due.timestamp(), now)}."
                             + (" I'll do it and tell you the result." if task else ""), "sources": []}
        if not task:
            return schedule()
        # A scheduled TASK runs a request later with nobody watching (OWASP ASI08/ASI10): the owner approves the exact
        # request once, now. Plain reminders only notify, so they need no approval.
        from ishti.skills import ask_first
        return ask_first(session, "reminders", f"Schedule this to run {every}"
                         + (f" at {due:%H:%M}" if not repeat.startswith("every:") else "")
                         + f" without asking again: \"{body}\"", schedule)
    if (m := todo(t)) and not SET.match(t):
        body = m["what"]
        due = when(body, now)
        body = re.sub(r"\s+(?:as\s+well|too|also)$", "", what(body) or body, flags=I).strip(" ,.") or body
        body = body[:1].upper() + body[1:]
        due = due or default_due(now)
        same = [r for r in _pending(f) if r[2].strip().lower() == body.strip().lower()]
        if same:  # said twice (or repeated in a voice retry): one task, not two
            return {"reply": f"That's already on your tasks: {body} ({_fmt(same[0][1], now)}).", "sources": []}
        with f.lock, f.db:
            f.db.execute("INSERT INTO reminders(due, text, created) VALUES(?,?,?)", (due.timestamp(), body, time.time()))
        return {"reply": f"Added to your tasks: {body}. I'll remind you at {_fmt(due.timestamp(), now)} - tell me another time to change it.",
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
            # past its grace window (half the period, 2 min to 2 h) a task still runs once, but says it is a catch-up:
            # an "08:00 brief" arriving at 18:00 must not read as fresh
            period = (next_due(due, repeat, due) - due) if repeat else 240
            note = (f"(Catch-up - this was due at {dt.datetime.fromtimestamp(due):%H:%M}.)\n"
                    if now - due > min(7200, max(120, period / 2)) else "")
            threading.Thread(target=lambda t=text, n=note: announce((lambda r: r and n + r)(run_task(t))),
                             daemon=True).start()
        else:
            announce(f"Reminder{' (missed while I was off, due ' + dt.datetime.fromtimestamp(due).strftime('%H:%M') + ')' if late else ''}: {text}")
        with f.lock, f.db:
            if repeat:
                f.db.execute("UPDATE reminders SET due=? WHERE id=?", (next_due(due, repeat, now), rid))
            else:
                f.db.execute("UPDATE reminders SET done=1 WHERE id=?", (rid,))
    return len(rows)


def start(announce, run_task=None):
    """Background loop the server starts once: it sleeps until the next due reminder (0.5-5 s), not a fixed 1 s tick."""
    def loop():
        while True:
            try:
                fire_due(announce, run_task=run_task)
                f = _db()
                with f.lock:
                    nxt = f.db.execute("SELECT MIN(due) FROM reminders WHERE done=0").fetchone()[0]
                wait = 5.0 if nxt is None else min(5.0, max(0.5, float(nxt) - time.time()))
            except Exception as exc:
                print(f"[reminders] {exc}")
                wait = 5.0
            time.sleep(wait)
    threading.Thread(target=loop, daemon=True, name="reminders").start()


if __name__ == "__main__":
    import os
    import tempfile
    import ishti.facts as F
    F._facts = F.Facts(os.path.join(tempfile.mkdtemp(), "t.db"))
    now = dt.datetime(2026, 9, 26, 14, 0)
    assert when("remind me in 20 minutes to call Ali", now) == now + dt.timedelta(minutes=20)
    assert when("set a timer for half an hour", now) == now + dt.timedelta(minutes=30)
    # WP-JEV spoken-duration vocabulary (JEV-17)
    assert when("set a timer for twenty five minutes", now) == now + dt.timedelta(minutes=25)
    assert when("remind me in a few minutes to check", now) == now + dt.timedelta(minutes=3)
    assert when("remind me in an hour and a half to leave", now) == now + dt.timedelta(minutes=90)
    assert when("set a timer for three quarters of an hour", now) == now + dt.timedelta(minutes=45)
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
    # secrets assigned a value are refused deterministically and never echoed or stored
    for bait in ("my password is Hunter2 remember it", "my API key is sk-batch2DEF789 store this",
                 "the 2FA code is 918273, save it", "my seed phrase is olive kite storm save this safely"):
        before = len(_pending(_db()))
        r = run(bait, {}, now)["reply"]
        assert "won't put it in your reminders" in r and "Hunter2" not in r, r
        assert len(_pending(_db())) == before, bait
    assert not _SECRET_ASSIGN.search("remind me to change my password at 9am")  # no value assigned
    run("set a timer for 10 minutes", {}, now)
    assert run("list my reminders", {}, now)["reply"].startswith("1. 14:10")
    assert "call Ali" in run("cancel the Ali reminder", {}, now)["reply"]
    heard = []
    assert fire_due(heard.append, now=(now + dt.timedelta(minutes=11)).timestamp()) == 1 and "Timer" in heard[0]
    assert run("list my reminders", {}, now)["reply"] == "No reminders set."
    assert repeat_of("remind me to pay the electricity bill on the 12th of every month") == "monthly:12"
    assert repeat_of("pay rent monthly on the 1st") == "monthly:1"
    first = first_due("remind me to pay rent on the 31st of every month at 10", "monthly:31", dt.datetime(2026, 9, 5, 12))
    assert first == dt.datetime(2026, 9, 30, 10), first  # September has 30 days: the last day
    assert dt.datetime.fromtimestamp(next_due(first.timestamp(), "monthly:31", first.timestamp() + 60)) == dt.datetime(2026, 10, 31, 10)
    r = run("remind me to pay the electricity bill on the 12th of every month", {}, now)["reply"]
    assert "on day 12 of every month" in r and "pay the electricity bill" in r and "12th" not in r, r
    sess = {}
    q = run("every weekday at 9 give me my brief", sess, now)["reply"]
    assert q.startswith("Schedule this to run every weekday at 09:00") and sess.get("pending"), q  # a task asks first
    r = sess.pop("pending")["action"]()["reply"]
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
