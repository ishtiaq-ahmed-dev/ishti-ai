"""Meeting cost (inventory domain 14 #20): people × minutes × rate, exactly.

"cost of a 60 minute meeting with 8 people at 2500 per hour"
"meeting cost: 45 min, 5 people, 3000/hour"
"yearly cost of a weekly 30 minute meeting with 6 people at 2000/hour"

Pure arithmetic stated in the reply (the formula is shown, so the number is auditable).
Recurring meetings are extrapolated with the stated frequency (weekly 52×, daily 260
workdays, monthly 12×). T0.
"""
import re

from ishti.skills import trace

I = re.I

ASK = re.compile(
    r"^\s*(?:(?:what(?:'s| is)\s+the\s+)?(?:yearly|annual|weekly|monthly)?\s*cost\s+(?:of|for)\b.*?\b"
    r"(?:meeting|standup|sync|call)|meeting\s+cost)\b.*$", I)
MIN = re.compile(r"(\d+(?:\.\d+)?)\s*(?:minute|min\b|mins\b)", I)
HOUR = re.compile(r"(\d+(?:\.\d+)?)\s*(?:hour|hr)", I)
PEOPLE = re.compile(r"(\d{1,4})\s*(?:people|persons?|attendees?|folks|participants|of\s+us)", I)
RATE = re.compile(r"(?:at|rate(?:\s+of)?|@)\s*(?:rs\.?\s*|pkr\s*)?(\d[\d,]*(?:\.\d+)?)\s*(?:per\s*|/)\s*hour", I)
FREQ = re.compile(r"\b(weekly|daily|monthly)\b", I)


def cost(minutes, people, rate, freq=None):
    hours = minutes / 60
    per = hours * rate
    total = per * people
    out = (f"**Meeting cost**: {minutes:g} min × {people} people @ {rate:g}/hour = **{total:,.0f}** "
           f"({per:,.0f} per person). Formula: minutes ÷ 60 × rate × people.")
    if freq:
        times = {"weekly": 52, "daily": 260, "monthly": 12}[freq]
        out += f"\nRecurring {freq} ({times}×/year): **{total * times:,.0f}** a year — that's the real price of the calendar invite."
    return out


def match(text):
    t = text.strip()
    return bool(ASK.match(t) and MIN.search(t) and PEOPLE.search(t))


def run(text, session):
    t = text.strip()
    m = ASK.match(t)
    if not m:
        return None
    hm = HOUR.search(t)
    mm = MIN.search(t)
    if hm:
        mins = float(hm.group(1)) * 60
    elif mm:
        mins = float(mm.group(1))
    else:
        return {"reply": "How long is the meeting? Say e.g. 60 minutes.", "sources": []}
    people = int(PEOPLE.search(t).group(1))
    rm = RATE.search(t)
    if not rm:
        return {"reply": "At what hourly rate? e.g. \"... at 2500 per hour\".", "sources": []}
    rate = float(rm.group(1).replace(",", ""))
    freq = (FREQ.search(t) or [None, None])[1]
    trace(session, "meetingcost", freq or "once")
    return {"reply": cost(mins, people, rate, freq), "sources": []}


if __name__ == "__main__":
    assert match("cost of a 60 minute meeting with 8 people at 2500 per hour")
    assert match("yearly cost of a weekly 30 minute meeting with 6 people at 2000/hour")
    assert match("meeting cost: 45 min, 5 people, 3000 per hour")
    assert not match("cost of living is rising")        # no minutes+people
    assert not match("meeting with Ali tomorrow")       # not a cost ask

    r = run("cost of a 60 minute meeting with 8 people at 2500 per hour", {})
    assert "20,000" in r["reply"], r                    # 1h * 2500 * 8 = 20000
    r2 = run("yearly cost of a weekly 30 minute meeting with 6 people at 2000 per hour", {})
    assert "52" in r2["reply"] and "312,000" in r2["reply"], r2  # 0.5h*2000*6*52 = 312000
    r3 = run("cost of a 2 hour meeting with 4 people at 1500 per hour", {})
    assert "12,000" in r3["reply"], r3                  # 2*1500*4
    r4 = run("cost of a 30 minute meeting with 5 people", {})
    assert "hourly rate" in r4["reply"], r4
    print("[self-check] meetingcost OK - exact people×minutes×rate, yearly recurrence multipliers, "
          "missing rate asked for")
