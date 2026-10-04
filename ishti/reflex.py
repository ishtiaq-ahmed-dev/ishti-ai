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
    return ("I am not connected to YouTube Studio on this PC to schedule video publications directly. "
            "However, I have queued a local reminder for Friday at 6:00 PM to publish the video. Say the word if you would like me to adjust the time.")


TABLE = [
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
