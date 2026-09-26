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


def _db():
    f = get_facts()
    with f.lock, f.db:
        f.db.execute("CREATE TABLE IF NOT EXISTS reminders(id INTEGER PRIMARY KEY, due REAL, text TEXT, "
                     "created REAL, done INTEGER DEFAULT 0)")
    return f


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
    return bool(SET.match(t) or LIST.match(t) or CANCEL.match(t))


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


def fire_due(announce, now=None):
    """Announce and close every reminder that is due. Returns how many fired."""
    f, now = _db(), now or time.time()
    with f.lock:
        rows = f.db.execute("SELECT id, due, text FROM reminders WHERE done=0 AND due<=?", (now,)).fetchall()
    for rid, due, text in rows:
        late = now - due > 120
        announce(f"Reminder{' (missed while I was off, due ' + dt.datetime.fromtimestamp(due).strftime('%H:%M') + ')' if late else ''}: {text}")
        with f.lock, f.db:
            f.db.execute("UPDATE reminders SET done=1 WHERE id=?", (rid,))
    return len(rows)


def start(announce):
    """Background loop (1 s tick) the server starts once."""
    def loop():
        while True:
            try:
                fire_due(announce)
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
    print("[self-check] reminders OK - parse in/at/tomorrow, set, list, cancel, fire")
