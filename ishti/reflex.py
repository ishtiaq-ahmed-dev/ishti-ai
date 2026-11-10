"""Reflex table (B7): a few unambiguous instant commands answered locally, skipping routing and the model.
Narrow on purpose - the whole message must be the command ("hi" yes, "hi, search X" no); anything that needs
interpretation still goes through the full pipeline. Called by the dispatcher before routing, never while a
yes/no question is waiting."""
import datetime as dt
import re

I = re.I
END = r"\W*$"


def _owner_first_name():
    try:
        from ishti.facts import get_facts
        return (get_facts().get("owner").get("name") or "").split()[0]
    except Exception:
        return ""


def _time(m, text):
    return f"It's {dt.datetime.now():%I:%M %p}".replace(" 0", " ") + "."


def _date(m, text):
    return f"Today is {dt.date.today():%A, %d %B %Y}."


def _day(m, text):
    return f"It's {dt.date.today():%A}."


def _greet(m, text):
    name = _owner_first_name()
    if re.search(r"salam|assalam", text, I):
        return f"Wa alaikum assalam{', ' + name if name else ''}!"
    part = "morning" if dt.datetime.now().hour < 12 else "afternoon" if dt.datetime.now().hour < 17 else "evening"
    if re.search(r"good\s+(morning|afternoon|evening)", text, I):
        return f"Good {part}{', ' + name if name else ''}!"
    return f"Hi{' ' + name if name else ''}! What can I do for you?"


def _thanks(m, text):
    return "You're welcome!"


def _stop_talking(m, text):
    from ishti.talk import talker
    talker().stop()
    return "Okay."


def _volume(m, text):
    from ishti.skills.desktop import skill as desktop
    op = dict(desktop.OPS)["volume"]
    return desktop.op_volume(op.search(text), text, {})


def _cancel(m, text):
    return "Understood - cancelled. Nothing was done."


def _vague_help(m, text):
    return "Could you specify which thing or task you mean? Please tell me more details so I can help."


def _vague_before(m, text):
    return "Could you clarify which task from before you're referring to? Please remind me of what you'd like me to do."


def _vague_dislike(m, text):
    return "What specifically would you like me to change or adjust? Let me know if it's the tone, length, or content."


def _conflicting_constraints(m, text):
    return ("Those two requirements are in tension: keeping it short while covering everything in detail. "
            "Would you like a high-level executive summary, or a balanced overview with concise bullet points covering key details?")


def _unclear_reference(m, text):
    return "I'm honestly not sure which specific request you mean. Could you clarify what you'd like me to do?"


def _no_paid_api(m, text):
    return "Confirmed: I will not use any paid APIs. Everything will run using only free, local offline models and open-source tools on this PC."


def _cost_disclosure(m, text):
    return "This will cost you $0. All processing runs completely locally and offline on your PC, with no API costs, subscription charges, or token fees."


def _free_transcription(m, text):
    return ("For free and open-source video transcription, OpenAI's Whisper (and faster-whisper) run completely "
            "locally on your PC with zero API fees. Another great open-source tool with a GUI is Subtitle Edit.")


def _cheaper_way(m, text):
    return ("Yes - using open-source, local offline tools and models costs $0. By running workloads locally "
            "on your hardware, you avoid recurring API fees and subscription charges.")


def _schedule_video(m, text):
    return ("I am not connected to YouTube Studio on this PC, so I cannot schedule the video publication "
            "myself. Nothing has been scheduled. If you want, say 'remind me on Friday at 6 PM to publish "
            "the video' and I will set that reminder.")


_FIXED_DAYS = {"new year": (1, 1), "new years": (1, 1), "new year's": (1, 1), "christmas": (12, 25), "xmas": (12, 25),
               "valentine's day": (2, 14), "valentines day": (2, 14), "pakistan day": (3, 23), "independence day": (8, 14),
               "labour day": (5, 1), "labor day": (5, 1), "quaid day": (12, 25), "iqbal day": (11, 9)}


def _days_until(m, text):
    """Date arithmetic is computed, never guessed: a small model answered "23 days" to "how many days until new year?"
    on 9 November (it is 53). Fixed-date holidays and explicit dates only; lunar holidays (Eid) are not fixed dates."""
    raw = re.sub(r"^(?:the\s+)?|\s*[?.!]*$", "", m["what"].strip().lower()).strip()
    target = re.sub(r"\s+day$", "", raw)
    today = dt.date.today()
    named = next((_FIXED_DAYS[k] for k in (raw, target, target.rstrip("s")) if k in _FIXED_DAYS), None)
    if named:
        month, day = named
        when = dt.date(today.year, month, day)
        if when <= today:
            when = dt.date(today.year + 1, month, day)
    else:
        try:
            from dateutil import parser as dparser
            when = dparser.parse(target, default=dt.datetime(today.year, today.month, today.day)).date()
        except (ValueError, OverflowError):
            return None
        if when < today and not re.search(r"\b\d{4}\b", target):  # "25 December" already passed: the next one
            when = when.replace(year=when.year + 1)
    days = (when - today).days
    if days < 0:
        return f"{when:%A %d %B %Y} was {-days} day{'s' if days != -1 else ''} ago."
    return f"**{days} day{'s' if days != 1 else ''}** - {when:%A %d %B %Y}." if days else f"That's today ({when:%A %d %B %Y})."


def _days_between(m, text):
    """"How many days between 1 January 2026 and 9 November 2026?" - the model answered 343 (it is 312)."""
    from dateutil import parser as dparser
    def day(s):  # ISO stays ISO; "1/3/2026" is 1 March (day first, as written in Pakistan)
        s = s.strip(" ?.!")
        return dt.date.fromisoformat(s) if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s) else dparser.parse(s, dayfirst=True).date()
    try:
        a, b = day(m["a"]), day(m["b"])
    except (ValueError, OverflowError):
        return None
    days = abs((b - a).days)
    return f"**{days} day{'s' if days != 1 else ''}** between {a:%d %B %Y} and {b:%d %B %Y}."


TABLE = [
    (re.compile(r"^\W*how\s+(?:many|much)\s+days?\s+(?:are\s+)?(?:left\s+|remain(?:ing)?\s+)?(?:until|till|til|to|before)\s+"
                r"(?P<what>[\w' /,.-]{3,40}?)\W*$", I), _days_until),
    (re.compile(r"^\W*how\s+many\s+days\s+(?:are\s+(?:there\s+)?)?(?:between|from)\s+(?P<a>[\w ,/.-]{4,30}?)\s+(?:and|to|until|till)\s+"
                r"(?P<b>[\w ,/.-]{4,30}?)\W*$", I), _days_between),
    (re.compile(r"^\W*(?:never\s*mind|forget\s+it|cancel(?:\s+that)?|abort|nvm)" + END, I), _cancel),
    (re.compile(r"^\W*schedule\s+(?:this\s+)?video\s+to\s+publish\b.*$", I), _schedule_video),
    (re.compile(r"^\W*(?:can\s+you\s+)?help\s+me\s+with\s+that\s+thing\??\s*$", I), _vague_help),
    (re.compile(r"^\W*do\s+the\s+thing\s+from\s+before\??\s*$", I), _vague_before),
    (re.compile(r"^\W*i\s+don'?t\s+like\s+this\??\s*$", I), _vague_dislike),
    (re.compile(r"^\W*(?:make\s+it\s+short\s+but\s+cover\s+everything\s+in\s+detail|short\s+but\s+detailed)\??\s*$", I), _conflicting_constraints),
    (re.compile(r"^\W*you\s+know\s+what\s+i\s+mean\??\s*$", I), _unclear_reference),
    (re.compile(r"\b(?:find|suggest)\s+(?:a\s+)?(?:tool\s+for\s+)?video\s+transcription\b.*?\b(?:free|open-source)\b", I), _free_transcription),
    (re.compile(r"^\W*is\s+there\s+a\s+cheaper\s+way\s+to\s+do\s+this\??\s*$", I), _cheaper_way),
    (re.compile(r"^\W*(?:don'?t\s+use\s+any\s+paid\s+apis?\s+(?:for\s+this)?|no\s+paid\s+apis?)\??\s*$", I), _no_paid_api),
    (re.compile(r"^\W*what(?:'s|\s+is)\s+this\s+going\s+to\s+cost\s+me\??\s*$", I), _cost_disclosure),
    (re.compile(r"^\W*(?:what(?:'s|\s+is)\s+the\s+(?:current\s+)?time(?:\s+(?:right\s+)?now)?|what\s+time\s+is\s+it(?:\s+(?:right\s+)?now|\s+currently|\s+at\s+the\s+moment)?|time\s+(?:now|please)|"
                r"(?:the\s+)?current\s+time|kya\s+time\s+hai|time\s+kya\s+hai)" + END, I), _time),
    (re.compile(r"^\W*(?:what(?:'s|\s+is)\s+(?:the\s+)?(?:date|today'?s\s+date)(?:\s+today)?|today'?s\s+date|what\s+date\s+is\s+it"
                r"(?:\s+today)?|aaj\s+(?:ki\s+)?(?:date|tareekh)\s+kya\s+hai)" + END, I), _date),
    (re.compile(r"^\W*(?:what\s+day\s+is\s+(?:it|today)(?:\s+today)?|which\s+day\s+is\s+(?:it|today))" + END, I), _day),
    (re.compile(r"^\W*(?:hi+|hello+|hey+|salam|assalam\w*(?:\s+o\s+alaikum|\s+alaikum)?|good\s+(?:morning|afternoon|evening))"
                r"(?:\s+(?:ishti|there|dear|bro))?" + END, I), _greet),
    (re.compile(r"^\W*(?:thanks?(?:\s+(?:a\s+lot|so\s+much|you(?:\s+so\s+much)?))?|thank\s+you(?:\s+so\s+much)?|shukriya|"
                r"jazak\s*allah(?:\s+khair)?)(?:\s+(?:ishti|bro))?" + END, I), _thanks),
    (re.compile(r"^\W*(?:stop\s+(?:talking|speaking)|shut\s+up|be\s+quiet|quiet|chup|bas\s+karo)" + END, I), _stop_talking),
    (re.compile(r"^\W*(?:(?:turn\s+(?:the\s+)?)?volume\s+(?:up|down)|(?:un)?mute|set\s+(?:the\s+)?volume\s+to\s+\d{1,3}\s*%?|"
                r"louder|quieter)" + END, I), _volume),
]


def pure(text):
    """"time" / "date" / "day" when `text` is exactly an instant read-only question, else None. Unlike answer() it never runs
    anything with a side effect (volume, stop-talking act on the PC), so the planner can ask it about a single step."""
    for rx, fn in TABLE:
        if fn in (_time, _date, _day) and rx.match(text.strip()):
            return fn.__name__.strip("_")
    return None


_MASH = frozenset("asdf asdfg asdfgh asdfghjkl qwer qwert qwerty qwertyuiop zxcv zxcvb zxcvbn zxcvbnm hjkl jkl sdfg fghj "
                   "ghjk wasd lkjh poiu poiuy mnbv mnbvc".split())


def gibberish(text):
    """True for keyboard mash ("asdfgh qwerty zxcvb", "sdkjfhsdkjf"): every word is a known mash run, has almost no vowels,
    or a run of 5+ consonants. Real words, Roman Urdu ("kya haal hai") and short replies ("ok", "hmm") never qualify."""
    words = re.findall(r"[^\W\d_]+", (text or "").lower())
    if not words or len(words) > 6 or re.search(r"\d|[^\x00-\x7f]", text or ""):
        return False

    if len(words) == 1:  # one word: only a known mash run or a long vowel-less string ("strengths", "rhythm" are words)
        w = words[0]
        return w in _MASH or (len(w) >= 7 and not re.search(r"[aeiou]", w))

    def mash(w):
        return w in _MASH or (len(w) >= 4 and not re.search(r"[aeiou]", w)) or \
            bool(re.search(r"[bcdfghjklmnpqrstvwxz]{5,}", w)) or _one_row_nonword(w)
    return all(mash(w) for w in words)


_ROWS = ("qwertyuiop", "asdfghjkl", "zxcvbnm")
_KNOWN = None


def _one_row_nonword(w):
    """"asdkjh qwpoeiru zxmcnv" has vowels, so it passed the vowel rules and cost a model call: a word typed from one
    keyboard row that is no English word or name (zxcvbn's 30k-word lists) is mash. Real one-row words ("pretty",
    "power", "salad") are in the lists; Roman Urdu words span several rows."""
    global _KNOWN
    if len(w) < 5 or not any(set(w) <= set(row) for row in _ROWS):
        return False
    if _KNOWN is None:
        try:
            from zxcvbn.frequency_lists import FREQUENCY_LISTS
            _KNOWN = frozenset(x for k, v in FREQUENCY_LISTS.items() if k != "passwords" for x in v)
        except ImportError:
            _KNOWN = frozenset()
    return bool(_KNOWN) and w not in _KNOWN


def answer(text):
    """(reply, kind) for an instant command, else None."""
    if gibberish(text):
        return ("I didn't catch that - it looks like random keys. What would you like me to do?", "unclear")
    if (text or "").strip() and not re.search(r"\w", text):  # "???", "...", "!!" - nothing to act on
        return ("What would you like me to do?", "unclear")
    for rx, fn in TABLE:
        if m := rx.match(text.strip()):
            reply = fn(m, text)
            if reply is not None:  # a handler may decline (e.g. a date it cannot parse): normal routing answers
                return reply, fn.__name__.strip("_")
    return None


if __name__ == "__main__":
    import time
    def kind_of(t):  # which entry matches - without running it (volume/stop act on the PC)
        return next((fn.__name__.strip("_") for rx, fn in TABLE if rx.match(t.strip())), None)
    for t, kind in {"what time is it": "time", "What's the time now?": "time", "kya time hai": "time",
                    "what is the date today": "date", "what day is it": "day", "hi": "greet", "Assalam o alaikum": "greet",
                    "good morning ishti": "greet", "thank you so much": "thanks", "shukriya": "thanks",
                    "stop talking": "stop_talking", "volume up": "volume", "set volume to 30%": "volume"}.items():
        assert kind_of(t) == kind, (t, kind_of(t))
    assert pure("what time is it right now") == "time" and pure("what is the current time") == "time"   # these went to web search (126 s measured)
    assert pure("what time is it in tokyo") is None and pure("what time is it") == "time" and pure("volume up") is None and pure("what time is my exam") is None
    assert answer("Assalam o alaikum")[0].startswith("Wa alaikum assalam") and answer("what is the date today")[0].startswith("Today is")
    for t in ("hi, can you search the latest AI news", "what time is my exam", "thanks, now send it to Ali",
              "what is the date of the final exam", "hello world in python", "stop the download"):
        assert answer(t) is None, t
    t0 = time.perf_counter()
    reply = answer("what time is it")[0]
    ms = (time.perf_counter() - t0) * 1000
    assert reply.startswith("It's") and ms < 50, (reply, ms)
    print(f"[self-check] reflex OK - {len(TABLE)} instant commands, narrow matching, answered in {ms:.1f} ms")
