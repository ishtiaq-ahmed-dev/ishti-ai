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

try:  # best-effort failure log (never breaks the module if logging is unavailable)
    from ishti.core.logutil import swallowed
except Exception:  # pragma: no cover
    def swallowed(*_a, **_k):
        return None


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
    # "Find a time this week that works for me and the JQ AI client" (checklist #153) - coordinate from
    # the owner's OWN calendar and be plain that the other side's is not visible.
    ("find_time", re.compile(r"\b(?:find|suggest|pick|propose|give\s+me)\s+(?:me\s+)?(?:a\s+|an\s+|some\s+)?(?:meeting\s+)?(?:time|slot|opening|free\s+slot)\b|"
                              r"\b(?:meeting\s+)?time\s+(?:this|next|that|which)?\s*works\b|\bthat\s+works\b", I)),
    # "What do I have back-to-back tomorrow?" / "I'm double-booked Thursday, which one should I move?"
    # (checklist #155, #156) - schedule analysis and a judgement call, not a plain list.
    ("conflicts", re.compile(r"\b(?:back[\s-]?to[\s-]?back|double[\s-]?book(?:ed|ing)?|overlapping)\b|"
                             r"\b(?:conflict(?:s|ing)?|clash(?:es|ing)?)\b.*\b(?:schedule|calendar|meeting|event|call|appointment)\b", I)),
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
        str(d.get("place") or "").strip(), (d.get("repeat") if d.get("repeat") in ("weekly", "daily")
                                            and re.search(r"\b(?:every|each|weekly|daily)\b", text, I) else "")  # "an exam on monday" is once


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
    all_day = not TIMED.search(text)  # "I have an exam on monday": no time said -> the whole day, not midnight-to-1am
    if all_day:
        start = dt.datetime.combine(start.date(), dt.time())
        end = start + dt.timedelta(hours=23, minutes=59)
    clash = [] if all_day else [o for o in occurrences(start.date(), start.date()) if o[0] < end and o[1] > start]
    when = (f"{start:%a %d %b} (all day)" if all_day else f"{start:%a %d %b, %H:%M}-{end:%H:%M}") + (f", {repeat}" if repeat else "")
    warn = ("\n⚠ Clashes with: " + ", ".join(f"{o[2]} ({o[0]:%H:%M}-{o[1]:%H:%M})" for o in clash)) if clash else ""

    def save():
        with db() as c:
            c.execute("INSERT INTO events VALUES(?,?,?,?,?,?)", (uuid.uuid4().hex, start.timestamp(), end.timestamp(), title, place, repeat))
        heads_up = start - (dt.timedelta(hours=4) if all_day else dt.timedelta(minutes=15))  # all-day: 20:00 the evening before
        try:  # a heads-up before (one-off events)
            from ishti.skills.reminders.skill import _db as rdb
            import time
            if not repeat and heads_up > dt.datetime.now():
                f = rdb()
                with f.lock, f.db:
                    f.db.execute("INSERT INTO reminders(due, text, created) VALUES(?,?,?)",
                                 (heads_up.timestamp(), (f"{title} tomorrow" if all_day else f"{title} at {start:%H:%M}") + (f" ({place})" if place else ""),
                                  time.time()))
        except Exception as _exc:
            swallowed('ishti/skills/calendar/skill.py:op_add', _exc)
        nudge = (" I'll remind you the evening before at 20:00." if all_day else " I'll remind you 15 minutes before.") if not repeat else ""
        reply = f"Added **{title}** on {when}" + (f" at {place}" if place else "") + "." + nudge + warn
        if GOOGLE.search(text):
            return to_google(title, start, end, place, session, reply)
        return reply

    # Confirm the exact person/date/time back before saving (checklist #152): read back what was parsed so a
    # mis-heard name or a wrong day is caught here, not silently written to the calendar.
    who = _person(text)
    recap = f"Schedule **{title}**" + (f" with {who}" if who and who.lower() not in title.lower() else "") + f" on {when}"
    if place:
        recap += f" at {place}"
    return ask_first(session, "calendar", recap + warn + "\nConfirm and I'll put it in your calendar", save)


def _person(text):
    """The named person in a scheduling request ("call with Sana" -> Sana), or None."""
    m = re.search(r"\bwith\s+(?:my\s+)?([A-Z][\w'-]*(?:\s+[A-Z][\w'-]*)?)", text)
    if m:
        return m.group(1)
    m = re.search(r"\b(?:with|to)\s+(?:my\s+)?([a-z][\w'-]{1,20})\b", text, I)
    if m and m.group(1).lower() not in ("a", "an", "the", "my", "me", "you", "tomorrow", "today", "friday", "monday", "tuesday", "wednesday", "thursday", "saturday", "sunday"):
        return m.group(1).title()
    return None


GOOGLE = re.compile(r"\bgoogle\s+calendar\b|\bgcal\b", I)
TIMED = re.compile(r"\b\d{1,2}(?::\d{2})?\s*(?:am|pm|a\.m\.|p\.m\.)|\bat\s+\d{1,2}\b|\b\d{1,2}:\d{2}\b|\b(?:noon|midnight|morning|afternoon|"
                   r"evening|tonight|night)\b|\bfor\s+(?:an?\s+|\d+\s+)?(?:hour|minute)s?\b", I)


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
        used = browser.SERVER  # Profile 10 if Profile 9 asked for a login: Save is clicked where the event is open
        save = browser.ref(page, "button", "Save")
    if not save:
        return f"{reply}\n\nI opened it in Google Calendar but couldn't find its Save button - it's open in your Chrome."

    def go():
        with browser.LOCK:
            browser.use_server(used, session)
            browser._call("browser_click", {"element": "Save", "ref": save})
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


def op_find_time(m, text, session):
    """Coordinate a meeting time from the owner's OWN calendar (checklist #153).

    ISHTI can only see Ishtiaq's schedule, not the client's - so it says so and proposes the owner's
    own open slots to send, rather than inventing the other side's availability."""
    a, b, label = _range(text)
    free = []
    day = a
    while day <= b:
        busy = [(s, e) for s, e, *_ in occurrences(day, day)]
        t, end = dt.datetime.combine(day, dt.time(9)), dt.datetime.combine(day, dt.time(18))
        if day == dt.date.today():
            t = max(t, dt.datetime.now().replace(second=0, microsecond=0))
        for s, e in sorted(busy):
            if s > t and (s - t).seconds >= 1800:
                free.append((t, min(s, end)))
            t = max(t, e)
        if end > t and (end - t).seconds >= 1800:
            free.append((t, end))
        day += dt.timedelta(days=1)
    head = ("I can only see **your** calendar - I don't have the other side's availability on file, "
            "so I can't confirm a time that works for both of you.")
    if not free:
        return head + f"\n\nYour own calendar is full {label} (9 am-6 pm); there's no free 30-minute slot to propose."
    slots = "\n".join(f"- {s:%a %H:%M}-{e:%H:%M}" for s, e in free[:6])
    return head + f"\n\nYour open slots {label} you could propose to them:\n{slots}"


def op_conflicts(m, text, session):
    """Back-to-back / double-booked analysis (checklist #155, #156).

    Lays out the overlapping items and, for a double-booking, asks which one matters more rather
    than deciding unilaterally. Never invents items."""
    a, b, label = _range(text)
    items = occurrences(a, b)
    if not items:
        return f"Nothing on your calendar {label} - so nothing back-to-back."
    clashes, adjacent = [], []
    for i, (s, e, title, place, _) in enumerate(items):
        for s2, e2, t2, _, _ in items[i + 1:]:
            if s2 < e:  # overlapping -> double-booked
                clashes.append((s, e, title, s2, e2, t2))
            elif s2 - e <= dt.timedelta(minutes=15) and s2 > e:  # touching/adjacent -> back-to-back
                adjacent.append((s, e, title, s2, e2, t2))
    if clashes:
        lines = [f"You're double-booked {label}:"]
        for s, e, title, s2, e2, t2 in clashes:
            lines.append(f"- **{title}** {s:%a %H:%M}-{e:%H:%M} overlaps **{t2}** {s2:%H:%M}-{e2:%H:%M}")
        lines.append("Which one matters more to you? I won't move either without your say-so.")
        return "\n".join(lines)
    if adjacent:
        lines = [f"Back-to-back on your calendar {label}:"]
        for s, e, title, s2, e2, t2 in adjacent:
            lines.append(f"- **{title}** ends {e:%H:%M}, **{t2}** starts {s2:%H:%M}")
        return "\n".join(lines)
    return f"Nothing back-to-back {label} - your {len(items)} item(s) all have gaps around them."


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
