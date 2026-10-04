"""Everyday utilities: prayer times (Aladhan, Karachi method, Hanafi Asr), translate, define a word, unit and
currency-amount conversion, time in another city, news headlines (Google News RSS), a strong password (generated
locally with `secrets`, never stored), a Jitsi meeting link."""
import datetime as dt
import html
import re
import secrets
import string
from zoneinfo import ZoneInfo

import requests

from ishti import llm
from ishti.skills import trace

I = re.I
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/140.0 Safari/537.36"}
PRAYERS = ("Fajr", "Sunrise", "Dhuhr", "Asr", "Maghrib", "Isha")
# unit -> (dimension, factor to the base unit)
UNITS = {"km": ("len", 1000), "kilometer": ("len", 1000), "kilometre": ("len", 1000), "m": ("len", 1), "meter": ("len", 1),
         "metre": ("len", 1), "cm": ("len", .01), "mm": ("len", .001), "mile": ("len", 1609.344), "mi": ("len", 1609.344),
         "yard": ("len", .9144), "yd": ("len", .9144), "foot": ("len", .3048), "feet": ("len", .3048), "ft": ("len", .3048),
         "inch": ("len", .0254), "inches": ("len", .0254), "in": ("len", .0254),
         "kg": ("mass", 1), "kilogram": ("mass", 1), "g": ("mass", .001), "gram": ("mass", .001), "lb": ("mass", .45359237),
         "lbs": ("mass", .45359237), "pound": ("mass", .45359237), "oz": ("mass", .028349523), "ounce": ("mass", .028349523),
         "ton": ("mass", 1000), "tonne": ("mass", 1000), "tola": ("mass", .0116638), "maund": ("mass", 37.3242),
         "l": ("vol", 1), "liter": ("vol", 1), "litre": ("vol", 1), "ml": ("vol", .001), "gallon": ("vol", 3.785411784),
         "gal": ("vol", 3.785411784), "cup": ("vol", .2365882365),
         "kmh": ("speed", 1 / 3.6), "km/h": ("speed", 1 / 3.6), "mph": ("speed", .44704), "m/s": ("speed", 1),
         "sqm": ("area", 1), "sqft": ("area", .09290304), "acre": ("area", 4046.8564224), "hectare": ("area", 10000),
         # ponytail: the revenue marla (272.25 sq ft); some housing authorities use 225 sq ft - say so if asked
         "marla": ("area", 25.2929), "kanal": ("area", 505.857),
         "gb": ("data", 1e9), "mb": ("data", 1e6), "tb": ("data", 1e12), "kb": ("data", 1e3)}
_U = "|".join(sorted((re.escape(u) for u in UNITS), key=len, reverse=True))
CONVERT = re.compile(rf"(?P<n>-?\d+(?:\.\d+)?)\s*(?P<a>{_U}|°?[cf]|celsius|fahrenheit)s?\s+(?:to|in|into)\s+(?P<b>{_U}|°?[cf]|celsius|fahrenheit)s?\b", I)
OPS = [
    ("prayer", re.compile(r"\b(?:prayer|namaz|namaaz|salah|salat|azan|adhan)\b|\b(?:fajr|zuhr|dhuhr|asr|maghrib|isha)\s+(?:time|kab)\b", I)),
    ("password", re.compile(r"\b(?:generate|make|create|give\s+me|suggest)\s+(?:a\s+|me\s+a\s+)?(?:strong\s+|secure\s+|random\s+)?password\b", I)),
    ("meeting", re.compile(r"\b(?:meeting|video\s+call|call)\s+link\b|\b(?:create|start|make)\s+a\s+(?:jitsi\s+)?meeting\b", I)),
    ("money", re.compile(r"(?P<n>\d[\d,]*(?:\.\d+)?)\s*(?P<a>usd|pkr|inr|eur|gbp|aed|sar|dollars?|rupees?|euros?|pounds?|dirhams?|riyals?)\s+(?:to|in|into)\s+(?P<b>usd|pkr|inr|eur|gbp|aed|sar|dollars?|rupees?|euros?|pounds?|dirhams?|riyals?)\b", I)),
    ("convert", CONVERT),
    ("time_in", re.compile(r"\b(?:what(?:'s| is)\s+the\s+)?time\s+(?:is\s+it\s+)?(?:now\s+)?in\s+(?P<city>[a-z .'-]{3,30})\??$", I)),
    ("translate", re.compile(r"^\s*(?:please\s+)?translate\s+(?P<body>.+?)\s+(?:to|into|in)\s+(?P<lang>[a-z ]{3,20})\s*$|^\s*(?:how\s+do\s+(?:you|i)\s+say|what\s+is)\s+(?P<body2>.+?)\s+in\s+(?P<lang2>urdu|english|arabic|pashto|hindi|chinese|french|german|spanish|turkish|persian)\s*\??$", I)),
    ("define", re.compile(r"^\s*(?:define|meaning\s+of|what\s+does\s+(?P<w2>[a-z'-]+)\s+mean)\b\s*(?P<w>[a-z'-]*)", I)),
    # "research the latest AI news" is deep research (read + synthesise with sources), not a headline list
    # "what's trending in AI news this week" is a web search (live results), not a Google News RSS headline list.
    # The RSS path is for plain "news"/"headlines" requests; when the owner asks for TRENDS or THIS WEEK,
    # web_search owns it (checklist #9). "latest news about pakistan" is still a plain headline request -
    # the plain "latest news" phrasing stays here. "...pick the most relevant one and draft a note" is
    # deep research, not a headline fetch (checklist #66).
    ("news", re.compile(r"^(?!.*\b(?:research|investigat\w*|analy[sz]e|deep\s+dive|report\s+on|trend|this\s+week|"
                        r"pick\s+(?:the\s+)?most\s+relevant|draft)\b).*\b(?:news|headlines)\b", I)),
]


def match(text):
    # "Define X for me" / "what does X mean" is general knowledge when the text is short and
    # has no other utility context (conversion, prayer, news, translate) - let the chat brain
    # answer from its training data instead of hitting the dictionary API (checklist #10).
    if (rx := next((rx for _n, rx in OPS if rx.search(text)), None)) and \
       any(rx.search(text) for _n, rx in OPS if _n == "define"):
        from ishti.skills.chat.skill import KNOWLEDGE, PERSONAL
        t = text.strip()
        if KNOWLEDGE.match(t) and not PERSONAL.search(t) and len(t.split()) <= 14:
            return False  # chat handles this as knowledge; the dictionary API would only parrot a definition
    return any(rx.search(text) for _, rx in OPS)


def run(text, session):
    for name, rx in OPS:
        if m := rx.search(text):
            trace(session, "utility", name)
            out = globals()["op_" + name](m, text, session)
            return out if isinstance(out, dict) or out is None else {"reply": out, "sources": []}
    return None


def owner_place():
    from ishti.facts import get_facts
    o = get_facts().get("owner")
    return o.get("city", "Peshawar"), o.get("country", "Pakistan")


# ---------------------------------------------------------------- prayer times
def op_prayer(m, text, session):
    city, country = owner_place()
    other = re.search(r"\bin\s+([A-Z][a-zA-Z .'-]{2,30})", text)
    if other and other.group(1).lower() not in ("the", "my"):
        city, country = other.group(1).strip(), ""
    day = dt.date.today() + dt.timedelta(days=1 if re.search(r"\btomorrow\b", text, I) else 0)
    r = requests.get("https://api.aladhan.com/v1/timingsByCity/" + day.strftime("%d-%m-%Y"),
                     params={"city": city, "country": country, "method": 1, "school": 1}, timeout=15).json()
    if r.get("code") != 200:
        return f"I couldn't get prayer times for {city}."
    t = r["data"]["timings"]
    hijri = r["data"]["date"]["hijri"]
    lines = [f"- {p}: {t[p]}" for p in PRAYERS]
    now = dt.datetime.now().strftime("%H:%M")
    nxt = next((p for p in PRAYERS if p != "Sunrise" and t[p] > now), None) if day == dt.date.today() else None
    return (f"Prayer times in {city} for {day:%A %d %B} ({hijri['day']} {hijri['month']['en']} {hijri['year']} AH):\n"
            + "\n".join(lines) + (f"\n\nNext: **{nxt} at {t[nxt]}**" if nxt else "")
            + "\n(University of Islamic Sciences Karachi method, Hanafi Asr)")


# ---------------------------------------------------------------- conversions
def op_convert(m, text, session):
    n, a, b = float(m.group("n")), m.group("a").lower().strip("°"), m.group("b").lower().strip("°")
    temps = {"c": "c", "celsius": "c", "f": "f", "fahrenheit": "f"}
    if a in temps and b in temps:
        a, b = temps[a], temps[b]
        v = n * 9 / 5 + 32 if (a, b) == ("c", "f") else (n - 32) * 5 / 9 if (a, b) == ("f", "c") else n
        return f"{n:g} °{a.upper()} = **{v:.1f} °{b.upper()}**"
    ua, ub = UNITS.get(a.rstrip("s")) or UNITS.get(a), UNITS.get(b.rstrip("s")) or UNITS.get(b)
    if not ua or not ub or ua[0] != ub[0]:
        return f"I can't convert {a} to {b}."
    v = n * ua[1] / ub[1]
    said_a, said_b = re.split(r"\s+(?:to|in|into)\s+", m.group(0), maxsplit=1)  # units as the owner wrote them
    return f"{n:g} {said_a[len(m.group('n')):].strip()} = **{v:,.4g} {said_b.strip()}**"


def op_money(m, text, session):
    from ishti.skills.markets.skill import CUR
    from ishti.skills.markets import data
    n = float(m.group("n").replace(",", ""))
    a, b = CUR.get(m.group("a").lower(), m.group("a").upper()), CUR.get(m.group("b").lower(), m.group("b").upper())
    rate = data.fx(a, b)
    return f"{n:,.2f} {a} = **{n * rate:,.2f} {b}** (1 {a} = {rate:,.4f} {b})"


def op_time_in(m, text, session):
    city = m.group("city").strip(" ?.").title()
    raw = llm.ask(f"IANA time zone name for the city '{city}'. Output only the zone, e.g. Asia/Karachi.",
                  system=None, temperature=0, max_tokens=12).strip()
    try:
        now = dt.datetime.now(ZoneInfo(raw))
    except Exception:
        return f"I don't know the time zone of {city}."
    here = dt.datetime.now().astimezone()
    diff = (now.utcoffset() - here.utcoffset()).total_seconds() / 3600
    return f"In {city} it's **{now:%I:%M %p, %A}** ({raw}, {diff:+g} h from you)."


# ---------------------------------------------------------------- words
# "Translate this whole email thread into English" points at content the model cannot see; translating the
# phrase itself is nonsense (checklist #180). Ask for the text instead of inventing a translation.
DEICTIC = re.compile(r"^\s*(?:this|the|that|my|our)\s+(?:whole\s+|entire\s+|full\s+|long\s+)?"
                     r"(?:e-?mails?|threads?|messages?|notes?|documents?|docs?|articles?|files?|paragraphs?|"
                     r"conversations?|chats?)\b", re.I)


def op_translate(m, text, session):
    body, lang = (m.group("body"), m.group("lang")) if m.group("body") else (m.group("body2"), m.group("lang2"))
    phrase = body.strip(" \"'")
    if DEICTIC.match(phrase):
        return {"reply": f"I can't translate {phrase} - I don't have its content here, and I won't invent one. "
                         f"Paste the text, or tell me which email/thread to open, and I'll translate all of it into "
                         f"{lang.strip().title()}.", "sources": []}
    script = {"urdu": "Urdu script (اردو)", "pashto": "Pashto script (پښتو)", "arabic": "Arabic script", "persian": "Persian script",
              "hindi": "Devanagari script", "chinese": "Chinese characters"}.get(lang.strip().lower(), f"{lang}")
    out = llm.ask(f"Translate the text below into {lang.strip().title()}, written in {script}. Output only the translation - no "
                  f"quotes, no notes, no transliteration.\n\nText: {phrase}", role="translator", system=None, temperature=0, max_tokens=400)
    out = out.strip().strip('"')
    if not out or out.lower() == phrase.lower():  # the small model echoed it: one stricter try on the main model
        out = llm.ask(f"Give the {lang.strip().title()} translation ({script}) of: {phrase}\nOnly the translation.", role="planner",
                      system=None, temperature=0, max_tokens=200).strip().strip('"')
    # Name the language so the reply is unambiguous, and add a pronunciation note for a phrasebook
    # ask ("how do you say X in Pashto?") - checklist #181 expects the phrase plus a pronunciation note.
    lang_name = lang.strip().title()
    if _HOW_SAY.match(text):
        label = f"{out}\n\n*(in {lang_name} - pronunciation can vary; ask me to romanise it)*"
    else:
        label = f"{out}\n\n*(in {lang_name})*"
    return {"reply": label, "sources": [], "no_translate": True}  # the answer IS in the other language: never translate it back


_HOW_SAY = re.compile(r"^\s*(?:how\s+do\s+(?:you|i)\s+say|what\s+is)\b", re.I)


def op_define(m, text, session):
    word = (m.group("w2") or m.group("w") or "").strip().lower()
    if not word:
        return "Which word?"
    try:
        d = requests.get(f"https://api.dictionaryapi.dev/api/v2/entries/en/{word}", timeout=6).json()
        entry = d[0]
        lines = [f"**{entry['word']}** {entry.get('phonetic', '')}"]
        for meaning in entry["meanings"][:3]:
            first = meaning["definitions"][0]
            lines.append(f"- *{meaning['partOfSpeech']}*: {first['definition']}" + (f" (e.g. \"{first['example']}\")" if first.get("example") else ""))
        return "\n".join(lines)
    except Exception:  # the free dictionary API is slow at times: the local model knows common words
        return llm.ask(f"Define the English word '{word}': part of speech, a short definition, one example sentence, and "
                       "the Urdu meaning.", max_tokens=160)


# ---------------------------------------------------------------- news
def op_news(m, text, session):
    import feedparser
    topic = re.sub(r"\b(?:latest|today'?s?|top|the|news|headlines|about|on|for|of|what(?:'s| is)|give\s+me|show\s+me|any|me)\b", " ", text, flags=I)
    topic = re.sub(r"^(?:in|from|of)\s+", "", re.sub(r"\s+", " ", topic).strip(" ?.")).strip()  # "news in pakistan" -> "pakistan"
    topic = topic[:1].upper() + topic[1:]
    city, country = owner_place()
    url = ("https://news.google.com/rss/search?q=" + requests.utils.quote(topic) if topic else "https://news.google.com/rss") \
          + ("&" if topic else "?") + "hl=en-PK&gl=PK&ceid=PK:en"
    feed = feedparser.parse(requests.get(url, timeout=15, headers=UA).content)
    items = feed.entries[:8]
    if not items:
        return f"No news found{' about ' + topic if topic else ''}."
    lines = [f"- {html.unescape(e.title)} [{i}]" + (f" · {e.get('published', '')[:16]}" if e.get("published") else "")
             for i, e in enumerate(items, 1)]
    sources = [{"id": i, "title": html.unescape(e.title), "url": e.link, "domain": "news.google.com",
                "snippet": html.unescape(e.get("source", {}).get("title", "")) if isinstance(e.get("source"), dict) else ""}
               for i, e in enumerate(items, 1)]
    return {"reply": f"Latest{' on ' + topic if topic else ' headlines'}:\n" + "\n".join(lines), "sources": sources}


# ---------------------------------------------------------------- password, meeting
def op_password(m, text, session):
    length = int((re.search(r"(\d{1,2})\s*(?:char|characters|long|digit)", text, I) or [0, 20])[1])
    length = max(12, min(64, length))
    pool = string.ascii_letters + string.digits + "!@#$%^&*-_=+?"
    while True:
        pw = "".join(secrets.choice(pool) for _ in range(length))
        if all(any(c in s for c in pw) for s in (string.ascii_lowercase, string.ascii_uppercase, string.digits, "!@#$%^&*-_=+?")):
            break
    session["no_history"] = True  # the reply isn't kept in chat history or memory
    return f"`{pw}` ({length} characters, generated on this PC; I don't store it - put it in your password manager)."


def op_meeting(m, text, session):
    room = "ISHTI-" + "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(12))
    return f"Meeting link (Jitsi, no account needed): https://meet.jit.si/{room}"


if __name__ == "__main__":
    cases = {"prayer times today": "prayer", "namaz kab hai": "prayer", "generate a strong password": "password",
             "make a meeting link": "meeting", "convert 500 usd to pkr": "money", "5 km to miles": "convert",
             "30 c to f": "convert", "10 marla to sqft": "convert", "what time is it in London": "time_in",
             "translate good morning to urdu": "translate", "how do you say thank you in pashto": "translate",
             "define serendipity": "define", "what does ubiquitous mean": "define", "latest news about pakistan": "news"}
    for t, want in cases.items():
        got = next((n for n, rx in OPS if rx.search(t)), None)
        assert got == want, (t, got, want)
    assert op_convert(CONVERT.search("5 km to miles"), "", {}) == "5 km = **3.107 miles**"
    assert "86.0 °F" in op_convert(CONVERT.search("30 c to f"), "", {})
    assert "2,723" in op_convert(CONVERT.search("10 marla to sqft"), "", {})  # 272.25 sq ft per marla
    s = {}
    pw = op_password(None, "generate a password", s)
    assert len(pw.split("`")[1]) == 20 and s["no_history"]
    print("[self-check] utilities OK - routing, units, temperature, password")
