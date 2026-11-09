"""The daily brief: today's weather, the next prayer, today's reminders, the markets watchlist, top headlines and
yesterday's screen time - gathered in parallel from the other skills. Schedule it with
"every morning at 8 give me my brief" (the reminders skill runs it and announces the answer)."""
import concurrent.futures as cf
import datetime as dt
import re

from ishti.skills import trace

I = re.I
BRIEF = re.compile(r"\b(?:(?:my\s+)?(?:daily|morning|evening)\s+(?:brief|briefing|digest|summary|update)|brief\s+me|give\s+me\s+my\s+brief|my\s+brief)\b", I)


def match(text):
    return bool(BRIEF.search(text)) and not re.match(r"^\s*(?:every|each)\s", text, I)


def _weather():
    from ishti.skills.weather import skill as w
    return (w.run("weather today", {}) or {}).get("reply", "")


def _prayer():
    from ishti.skills.utilities.skill import op_prayer
    out = op_prayer(None, "prayer times today", {})
    nxt = re.search(r"Next: \*\*(.+?)\*\*", out)
    if nxt:
        return f"Next prayer: {nxt.group(1)}"
    fajr = re.search(r"Fajr: (\d{2}:\d{2})", op_prayer(None, "prayer times tomorrow", {}))  # after Isha
    return f"Next prayer: Fajr tomorrow at {fajr.group(1)}" if fajr else ""


def _reminders():
    from ishti.skills.reminders.skill import _db, _pending
    end = dt.datetime.combine(dt.date.today(), dt.time(23, 59)).timestamp()
    rows = [(d, t) for _, d, t in _pending(_db()) if d <= end]
    return "\n".join(f"- {dt.datetime.fromtimestamp(d):%H:%M} {t}" for d, t in rows) or "No reminders left today."


def _markets():
    from ishti.skills.markets.skill import quote, watchlist
    out = []
    for item in watchlist()[:5]:
        try:
            q = quote(item)
            ch = f" ({q['change_pct']:+.1f}%)" if q.get("change_pct") is not None else ""
            out.append(f"- {q['label']}: {q['price']:,.2f} {q.get('currency', '')}{ch}")
        except Exception:
            continue
    return "\n".join(out)


def _news():
    from ishti.skills.utilities.skill import op_news
    out = op_news(None, "top headlines", {})
    lines = (out.get("reply", "") if isinstance(out, dict) else str(out)).splitlines()[1:4]
    return "\n".join(lines)


def _screen():
    from ishti.skills.lifelog.skill import report
    out = report("screen time today")
    return out.splitlines()[0] if out.startswith("Screen time") else ""


def _calendar():
    """Today's events: the local calendar, plus Google Calendar (read-only) when it is connected."""
    from ishti.skills.calendar.skill import occurrences
    today = dt.date.today()
    items = {(f"{s:%H:%M}", title.strip().lower()): (f"{s:%H:%M}", title) for s, _e, title, _p, _id in occurrences(today, today)}
    try:
        from ishti import oauth
        if oauth.connected("calendar"):
            lo = dt.datetime.combine(today, dt.time()).astimezone()
            got = oauth.api("calendar", "/calendars/primary/events",
                            {"timeMin": lo.isoformat(), "timeMax": (lo + dt.timedelta(days=1)).isoformat(),
                             "singleEvents": "true", "orderBy": "startTime", "maxResults": 15}) or {}
            for ev in got.get("items", []):
                start = ev.get("start", {})
                when = (dt.datetime.fromisoformat(start["dateTime"].replace("Z", "+00:00")).astimezone().strftime("%H:%M")
                        if start.get("dateTime") else "all day")
                title = ev.get("summary") or "(no title)"
                items.setdefault((when, title.strip().lower()), (when, title))
    except Exception as exc:  # Google not reachable: the local calendar still counts
        from ishti.core.logutil import swallowed
        swallowed("ishti/skills/digest/skill.py:_calendar", exc)
    return "\n".join(f"- {w} {t}" for w, t in sorted(items.values(), key=lambda x: (x[0] != "all day", x[0]))) or "Nothing on the calendar."


def _email():
    """Unread Gmail, newest first - only when Gmail is connected (otherwise the section is left out)."""
    from ishti import gmail_api
    if not gmail_api.available():
        return ""
    rows = gmail_api.unread(5)
    who = lambda h: re.sub(r"\s*<[^>]+>\s*$", "", h or "").strip(' "') or h
    return "\n".join(f"- {who(r['from'])}: {r['subject'] or '(no subject)'}" for r in rows) or "No unread email."


def _waiting():
    """Scheduled jobs that are waiting for the owner's yes in the bell."""
    from ishti import notifications
    n = sum(1 for r in notifications.recent(50) if (r.get("meta") or {}).get("answerable") and not r["read"])
    return f"{n} scheduled job{'s' if n != 1 else ''} waiting for your yes - see the bell." if n else ""


PARTS = [("Weather", _weather), ("Prayer", _prayer), ("Calendar", _calendar), ("Today", _reminders),
         ("Email", _email), ("Waiting for you", _waiting), ("Markets", _markets), ("Headlines", _news),
         ("Screen time", _screen)]


def run(text, session):
    trace(session, "digest", "gathering weather, prayer, reminders, markets, news", status="start")

    def safe(part):
        title, fn = part
        try:
            return title, fn()
        except Exception as exc:
            return title, f"(unavailable: {type(exc).__name__})"
    with cf.ThreadPoolExecutor(len(PARTS)) as pool:
        got = list(pool.map(safe, PARTS))
    trace(session, "digest", "done", status="done")
    hour = dt.datetime.now().hour
    hello = "Good morning" if hour < 12 else "Good afternoon" if hour < 18 else "Good evening"
    body = "\n\n".join(f"**{t}**\n{v}" for t, v in got if v)
    return {"reply": f"{hello}! {dt.date.today():%A %d %B}.\n\n{body}", "sources": []}


if __name__ == "__main__":
    assert match("give me my brief") and match("morning briefing please") and match("brief me")
    assert not match("every morning at 8 give me my brief") and not match("what's the weather")
    print("[self-check] digest OK - routing")
