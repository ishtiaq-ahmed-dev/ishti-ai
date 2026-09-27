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


TABLE = [
    (re.compile(r"^\W*(?:what(?:'s|\s+is)\s+the\s+time(?:\s+now)?|what\s+time\s+is\s+it(?:\s+now)?|time\s+(?:now|please)|"
                r"current\s+time|kya\s+time\s+hai|time\s+kya\s+hai)" + END, I), _time),
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


def answer(text):
    """(reply, kind) for an instant command, else None."""
    for rx, fn in TABLE:
        if m := rx.match(text.strip()):
            return fn(m, text), fn.__name__.strip("_")
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
    assert answer("Assalam o alaikum")[0].startswith("Wa alaikum assalam") and answer("what is the date today")[0].startswith("Today is")
    for t in ("hi, can you search the latest AI news", "what time is my exam", "thanks, now send it to Ali",
              "what is the date of the final exam", "hello world in python", "stop the download"):
        assert answer(t) is None, t
    t0 = time.perf_counter()
    reply = answer("what time is it")[0]
    ms = (time.perf_counter() - t0) * 1000
    assert reply.startswith("It's") and ms < 50, (reply, ms)
    print(f"[self-check] reflex OK - {len(TABLE)} instant commands, narrow matching, answered in {ms:.1f} ms")
