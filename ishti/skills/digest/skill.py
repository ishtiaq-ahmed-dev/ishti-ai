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
    lines = op_news(None, "top headlines", {}).splitlines()[1:4]
    return "\n".join(lines)


def _screen():
    from ishti.skills.lifelog.skill import report
    out = report("screen time today")
    return out.splitlines()[0] if out.startswith("Screen time") else ""


PARTS = [("Weather", _weather), ("Prayer", _prayer), ("Today", _reminders), ("Markets", _markets), ("Headlines", _news),
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
