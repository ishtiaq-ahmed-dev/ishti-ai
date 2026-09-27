"""ISHTI's own calendar (no Outlook account is set up on this PC): add events with a clash warning, list a day or
week, find free slots, delete (asks first), export everything as .ics (import into Google Calendar or Outlook).
Each event also gets a 15-minute heads-up through the reminders skill. Local SQLite (data/calendar.db)."""
import datetime as dt
import json
import os
import re
import sqlite3
import uuid

from ishti import ROOT, llm
from ishti.skills import ask_first, trace

I = re.I
DB = os.path.join(ROOT, "data", "calendar.db")
OPS = [
    ("export", re.compile(r"\bexport\s+(?:my\s+)?calendar\b|\bcalendar\s+(?:to|as)\s+(?:ics|google|outlook)\b|\.ics\b", I)),
    ("free", re.compile(r"\b(?:when\s+am\s+i\s+free|free\s+(?:time|slots?)|am\s+i\s+(?:free|busy))\b", I)),
    ("delete", re.compile(r"\b(?:cancel|delete|remove)\s+(?:the\s+|my\s+)?(?P<what>.+?)\s+(?:meeting|event|appointment)\b", I)),
    ("list", re.compile(r"\b(?:what(?:'s| is)\s+on\s+my\s+(?:calendar|schedule|agenda)|my\s+(?:schedule|agenda|calendar|meetings|events)|"
                        r"what\s+(?:meetings|events)\s+do\s+i\s+have|what\s+do\s+i\s+have\s+(?:today|tomorrow|this\s+week|on))\b", I)),
    ("add", re.compile(r"\b(?:add|schedule|put|book|create|set\s+up)\s+(?:a\s+|an\s+)?(?:meeting|event|appointment|call|class|lecture|exam)\b|"
                       r"\b(?:add|put)\s+.+\s+(?:to|on|in)\s+(?:my\s+)?calendar\b|\bi\s+have\s+(?:a|an)\s+(?:meeting|appointment|exam|class)\b", I)),
]


def match(text):
    return any(rx.search(text) for _, rx in OPS)


def db():
    os.makedirs(os.path.dirname(DB), exist_ok=True)
    c = sqlite3.connect(DB)
    c.execute("CREATE TABLE IF NOT EXISTS events(id TEXT PRIMARY KEY, start REAL, end REAL, title TEXT, place TEXT, repeat TEXT)")
    return c


def run(text, session):
    for name, rx in OPS:
        if m := rx.search(text):
            trace(session, "calendar", name)
            out = globals()["op_" + name](m, text, session)
            return out if isinstance(out, dict) else {"reply": out, "sources": []}
    return None


def _parse(text):
    now = dt.datetime.now()
    raw = llm.ask(f"Now is {now:%Y-%m-%d %H:%M} ({now:%A}). Extract the calendar event from: {text}\nReturn JSON "
                  '{"title": "<short>", "start": "YYYY-MM-DD HH:MM", "minutes": <duration, default 60>, "place": "<or empty>", '
                  '"repeat": "none|weekly|daily"}. A bare hour without am/pm is between 8 am and 8 pm.',
                  system=None, temperature=0, max_tokens=120, response_format={"type": "json_object"})
    d = json.loads(raw)
    start = dt.datetime.strptime(d["start"], "%Y-%m-%d %H:%M")
    # the model's weekday arithmetic is unreliable ("monday" came back as a Saturday): named days are computed here
    today = now.date()
    days = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
    named = next((i for i, n in enumerate(days) if re.search(rf"\b{n}\b", text, I)), None)
    if re.search(r"\btomorrow\b", text, I):
        day = today + dt.timedelta(days=1)
    elif re.search(r"\btoday|tonight\b", text, I):
        day = today
    elif named is not None:
        ahead = (named - today.weekday()) % 7 or (7 if start.time() <= now.time() else 0)
        day = today + dt.timedelta(days=ahead + (7 if re.search(r"\bnext\s+" + days[named], text, I) and ahead < 7 else 0))
    else:
        day = start.date()
    start = dt.datetime.combine(day, start.time())
    return str(d.get("title") or "Event").strip(), start, start + dt.timedelta(minutes=int(d.get("minutes") or 60)), \
        str(d.get("place") or "").strip(), d.get("repeat") if d.get("repeat") in ("weekly", "daily") else ""


def occurrences(start_day, end_day):
    """Events (repeats expanded) overlapping [start_day, end_day]: [(start, end, title, place, id)]."""
    lo, hi = dt.datetime.combine(start_day, dt.time()), dt.datetime.combine(end_day, dt.time(23, 59))
    out = []
    with db() as c:
        for eid, s, e, title, place, rep in c.execute("SELECT id, start, end, title, place, repeat FROM events"):
            s, e = dt.datetime.fromtimestamp(s), dt.datetime.fromtimestamp(e)
            step = {"daily": dt.timedelta(days=1), "weekly": dt.timedelta(days=7)}.get(rep)
            while s <= hi:
                if e >= lo:
                    out.append((s, e, title, place, eid))
                if not step:
                    break
                s, e = s + step, e + step
    return sorted(out)


def op_add(m, text, session):
    try:
        title, start, end, place, repeat = _parse(text)
    except (ValueError, KeyError, TypeError):
        return "When is it? e.g. \"add a meeting with Ali tomorrow at 3pm for an hour\"."
    clash = [o for o in occurrences(start.date(), start.date()) if o[0] < end and o[1] > start]
    with db() as c:
        c.execute("INSERT INTO events VALUES(?,?,?,?,?,?)", (uuid.uuid4().hex, start.timestamp(), end.timestamp(), title, place, repeat))
    try:  # a heads-up 15 minutes before (one-off events)
        from ishti.skills.reminders.skill import _db as rdb
        import time
        if not repeat and start - dt.timedelta(minutes=15) > dt.datetime.now():
            f = rdb()
            with f.lock, f.db:
                f.db.execute("INSERT INTO reminders(due, text, created) VALUES(?,?,?)",
                             ((start - dt.timedelta(minutes=15)).timestamp(), f"{title} at {start:%H:%M}" + (f" ({place})" if place else ""), time.time()))
    except Exception:
        pass
    when = f"{start:%a %d %b, %H:%M}-{end:%H:%M}" + (f", {repeat}" if repeat else "")
    warn = ("\n⚠ Clashes with: " + ", ".join(f"{o[2]} ({o[0]:%H:%M}-{o[1]:%H:%M})" for o in clash)) if clash else ""
    reply = f"Added **{title}** on {when}" + (f" at {place}" if place else "") + ". I'll remind you 15 minutes before." * (not repeat) + warn
    if GOOGLE.search(text):
        return to_google(title, start, end, place, session, reply)
    return reply


GOOGLE = re.compile(r"\bgoogle\s+calendar\b|\bgcal\b", I)


def google_url(title, start, end, place=""):
    """Google Calendar's own "new event" page, prefilled (times in the PC's local zone)."""
    from urllib.parse import urlencode
    import time as _t
    zone = {5 * 3600: "Asia/Karachi"}.get(-_t.timezone, "")
    q = {"action": "TEMPLATE", "text": title, "dates": f"{start:%Y%m%dT%H%M%S}/{end:%Y%m%dT%H%M%S}"}
    if place:
        q["location"] = place
    if zone:
        q["ctz"] = zone
    return "https://calendar.google.com/calendar/render?" + urlencode(q)


def to_google(title, start, end, place, session, reply):
    """Open the prefilled event in the Google-signed-in profile; Save only after the owner's yes."""
    from ishti.skills import ask_first
    from ishti.skills.browser import skill as browser
    url = google_url(title, start, end, place)
    with browser.LOCK:
        try:
            browser.use_server(browser.profile_for("calendar"), session)
        except RuntimeError as exc:
            return f"{reply}\n\nGoogle Calendar: {exc}"
        page = browser._call("browser_navigate", {"url": url})
        save = re.search(r'button "Save"[^\n]*\[ref=([^\]]+)\]', page)
    if not save:
        return f"{reply}\n\nI opened it in Google Calendar but couldn't find its Save button - it's open in your Chrome."

    def go():
        with browser.LOCK:
            browser._call("browser_click", {"element": "Save", "ref": save.group(1)})
        return f"Saved **{title}** to your Google Calendar too."
    return ask_first(session, "calendar", reply + "\n\nIt's filled in on Google Calendar - save it there too", go)


def _range(text):
    today = dt.date.today()
    if re.search(r"\btomorrow\b", text, I):
        return today + dt.timedelta(days=1), today + dt.timedelta(days=1), "tomorrow"
    if re.search(r"\b(?:this\s+)?week\b", text, I):
        return today, today + dt.timedelta(days=6 - today.weekday()), "this week"
    day = next((i for i, d in enumerate(("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")) if d in text.lower()), None)
    if day is not None:
        target = today + dt.timedelta(days=(day - today.weekday()) % 7)
        return target, target, f"{target:%A}"
    return today, today, "today"


def op_list(m, text, session):
    a, b, label = _range(text)
    items = occurrences(a, b)
    if not items:
        return f"Nothing on your calendar {label}."
    return f"Your calendar {label}:\n" + "\n".join(f"- {s:%a %H:%M}-{e:%H:%M} **{t}**" + (f" ({p})" if p else "") for s, e, t, p, _ in items)


def op_free(m, text, session):
    a, _, label = _range(text)
    busy = [(s, e) for s, e, *_ in occurrences(a, a)]
    t, end = dt.datetime.combine(a, dt.time(9)), dt.datetime.combine(a, dt.time(21))
    if a == dt.date.today():
        t = max(t, dt.datetime.now().replace(second=0, microsecond=0))
    gaps = []
    for s, e in sorted(busy):
        if s > t and (s - t).seconds >= 1800:
            gaps.append((t, min(s, end)))
        t = max(t, e)
    if end > t and (end - t).seconds >= 1800:
        gaps.append((t, end))
    return (f"Free {label} (9 am-9 pm, 30 min or more):\n" + "\n".join(f"- {s:%H:%M}-{e:%H:%M}" for s, e in gaps)) if gaps else f"No free 30-minute slot {label}."


def op_delete(m, text, session):
    words = m.group("what").lower().split()
    with db() as c:
        hits = [r for r in c.execute("SELECT id, start, title FROM events") if all(w in r[2].lower() for w in words if w not in ("my", "the"))]
    if not hits:
        return "No event matches that."
    eid, start, title = hits[0]

    def remove():
        with db() as c:
            c.execute("DELETE FROM events WHERE id=?", (eid,))
        return f"Deleted {title}."
    return ask_first(session, "calendar", f"Delete **{title}** ({dt.datetime.fromtimestamp(start):%a %d %b %H:%M}) from your calendar", remove)


def op_export(m, text, session):
    stamp = lambda d: d.strftime("%Y%m%dT%H%M%S")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//ISHTI//Calendar//EN"]
    with db() as c:
        for eid, s, e, title, place, rep in c.execute("SELECT id, start, end, title, place, repeat FROM events"):
            lines += ["BEGIN:VEVENT", f"UID:{eid}@ishti", f"DTSTART:{stamp(dt.datetime.fromtimestamp(s))}", f"DTEND:{stamp(dt.datetime.fromtimestamp(e))}",
                      f"SUMMARY:{title}"] + ([f"LOCATION:{place}"] if place else []) + ([f"RRULE:FREQ={rep.upper()}"] if rep else []) + ["END:VEVENT"]
    lines.append("END:VCALENDAR")
    path = os.path.join(os.path.expanduser("~"), "Downloads", "ishti_calendar.ics")
    with open(path, "w", encoding="utf-8", newline="\r\n") as f:
        f.write("\n".join(lines) + "\n")
    return f"Exported {sum(l == 'BEGIN:VEVENT' for l in lines)} events to {path} - import it in Google Calendar (Settings > Import) or Outlook."


if __name__ == "__main__":
    import tempfile
    DB = os.path.join(tempfile.mkdtemp(), "c.db")
    cases = {"add a meeting with Ali tomorrow at 3pm": "add", "what's on my calendar today": "list", "my schedule this week": "list",
             "when am I free tomorrow": "free", "cancel the Ali meeting": "delete", "export my calendar": "export",
             "I have an exam on monday at 9am": "add"}
    for t, want in cases.items():
        got = next((n for n, rx in OPS if rx.search(t)), None)
        assert got == want, (t, got, want)
    assert not match("what's the weather")
    now = dt.datetime.now()
    base = dt.datetime.combine(dt.date.today() + dt.timedelta(days=1), dt.time(14))
    with db() as c:
        c.execute("INSERT INTO events VALUES('a',?,?,'Class','','weekly')", (base.timestamp(), (base + dt.timedelta(hours=1)).timestamp()))
    assert len(occurrences(base.date(), base.date() + dt.timedelta(days=8))) == 2  # weekly repeat expanded
    free = op_free(None, "when am I free tomorrow", {})
    assert "09:00-14:00" in free and "15:00-21:00" in free, free
    u = google_url("Meeting with Ali", dt.datetime(2026, 10, 15, 15, 0), dt.datetime(2026, 10, 15, 16, 0), "IMSciences")
    assert "action=TEMPLATE" in u and "dates=20261015T150000%2F20261015T160000" in u and "location=IMSciences" in u, u
    assert GOOGLE.search("add a meeting with Ali tomorrow at 3pm to my google calendar")
    print("[self-check] calendar OK - routing, weekly repeats, free slots, Google Calendar link")
