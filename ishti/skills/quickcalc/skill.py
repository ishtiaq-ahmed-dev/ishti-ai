"""Everyday micro-calculations (deterministic — no LLM arithmetic).

bill split (tip included), BMI + healthy range, discount price, fuel cost,
sleep-cycle wake times (90-minute cycles from now or a set alarm time).
"""
import datetime as dt
import re

I = re.I

BILLSPLIT = re.compile(r"^\s*(?:split|divide)\s+(?:the\s+)?(?:bill\s+)?(?:of\s+)?(?:rs\.?|pkr|\$)?\s*(?P<amt>\d[\d,.]*)\s*(?:rs|pkr)?\s*(?:between|among|by|for)\s*(?P<n>\d+)\s*(?:people|persons?|ways|of\s*us)?\s*(?:.*?(?P<tip>\d[\d.]*)\s*%\s*tip)?\s*$", I)
BMI = re.compile(r"^\s*(?:what(?:'s| is)\s+)?(?:my\s+)?bmi\s+(?:of|for)?\s*(?P<kg>\d[\d.]*)\s*(?:kg|kilos?)?\s*(?:and|,)?\s*(?P<cm>\d[\d.]*)\s*(?:cm|centimet\w*)?\s*$|^\s*bmi\s+(?P<kg2>\d[\d.]*)\s*(?:kg)?\s+(?P<cm2>\d[\d.]*)\s*(?:cm)?\s*$", I)
DISCOUNT = re.compile(r"^\s*(?:what(?:'s| is)\s+)?(?:the\s+)?price\s+(?:after\s+)?(?:a\s+)?(?P<pct>\d[\d.]*)\s*%\s*discount\s+(?:on|of)\s+(?:rs\.?|pkr|\$)?\s*(?P<amt>\d[\d,.]*)\s*$|^\s*(?:rs\.?|pkr|\$)?\s*(?P<amt2>\d[\d,.]*)\s+(?:with|after)\s+(?:a\s+)?(?P<pct2>\d[\d.]*)\s*%\s*(?:off|discount)\s*$", I)
FUEL = re.compile(r"^\s*fuel\s+cost\s+(?:of\s+)?(?P<km>\d[\d.]*)\s*(?:km|kilomet\w*)\s*(?:at|with|@)\s*(?P<mileage>\d[\d.]*)\s*(?:km\/l|kmpl)\s*(?:at|@)\s*(?:rs\.?|pkr)?\s*(?P<price>\d[\d.]*)\s*(?:per\s*litre|per\s*liter|\/l)?\s*$", I)
SLEEP = re.compile(r"^\s*(?:what\s+time\s+should\s+i\s+)?(?:wake\s*up|sleep)\s*(?:if\s+i\s+(?:go\s+to\s+bed|sleep)\s+(?:(?P<now>now)|at\s*(?P<at>\d{1,2})(?::(?P<min>\d{2}))?\s*(?P<ap>am|pm)?))?(?:\s*\??\s*(?P<cyc>\d+)\s*cycles?)?\s*$", I)

CYCLE_MIN = 90  # one sleep cycle ≈ 90 min; falling asleep ~14 min
FALL_ASLEEP = 14


def match(text):
    t = text.strip()
    return bool(BILLSPLIT.match(t) or BMI.match(t) or DISCOUNT.match(t) or FUEL.match(t) or SLEEP.match(t))


def _fmt(x):
    return f"{x:,.0f}" if float(x) == int(x) else f"{x:,.2f}"


def run(text, session):
    t = text.strip()
    m = BILLSPLIT.match(t)
    if m:
        amt = float(m.group("amt").replace(",", ""))
        n = int(m.group("n"))
        tip = float(m.group("tip") or 0)
        total = amt * (1 + tip / 100)
        per = total / n
        reply = f"{_fmt(amt)} split {n} ways = {_fmt(per)} each"
        if tip:
            reply += f" (with {tip:.0f}% tip: total {_fmt(total)})"
        return {"reply": reply + ".", "sources": []}
    m = BMI.match(t)
    if m:
        kg = float(m.group("kg") or m.group("kg2") or 0)
        cm = float(m.group("cm") or m.group("cm2") or 0)
        if kg <= 0 or cm <= 0:
            return None
        bmi = kg / (cm / 100) ** 2
        band = ("underweight" if bmi < 18.5 else "normal" if bmi < 25 else
                "overweight" if bmi < 30 else "obese")
        lo = 18.5 * (cm / 100) ** 2
        hi = 24.9 * (cm / 100) ** 2
        return {"reply": (f"BMI: {bmi:.1f} ({band}). Healthy weight for {cm:.0f} cm is "
                          f"{lo:.0f}–{hi:.0f} kg. Informational only — not medical advice."), "sources": []}
    m = DISCOUNT.match(t)
    if m:
        pct = float(m.group("pct") or m.group("pct2") or 0)
        amt = float((m.group("amt") or m.group("amt2") or "0").replace(",", ""))
        final = amt * (1 - pct / 100)
        return {"reply": f"{pct:.0f}% off {_fmt(amt)} = {_fmt(final)} (you save {_fmt(amt - final)}).", "sources": []}
    m = FUEL.match(t)
    if m:
        km = float(m.group("km"))
        mileage = float(m.group("mileage"))
        price = float(m.group("price"))
        litres = km / mileage if mileage else 0
        return {"reply": (f"{km:.0f} km at {mileage:.0f} km/l needs {litres:.1f} litres → "
                          f"Rs {_fmt(litres * price)} at Rs {_fmt(price)}/litre."), "sources": []}
    m = SLEEP.match(t)
    if m:
        cycles = int(m.group("cyc") or 6)
        now = dt.datetime.now()
        if m.group("at"):
            hour = int(m.group("at"))
            if (m.group("ap") or "").lower() == "pm" and hour != 12:
                hour += 12
            if (m.group("ap") or "").lower() == "am" and hour == 12:
                hour = 0
            bed = now.replace(hour=hour, minute=int(m.group("min") or 0), second=0, microsecond=0)
            if bed < now:
                bed += dt.timedelta(days=1)
        else:
            bed = now
        wake0 = bed + dt.timedelta(minutes=FALL_ASLEEP + cycles * CYCLE_MIN)
        alts = [bed + dt.timedelta(minutes=FALL_ASLEEP + c * CYCLE_MIN) for c in (cycles - 1, cycles, cycles + 1)]
        lines = [f"{a.strftime('%H:%M')} ({c} cycles)" for a, c in zip(alts, (cycles - 1, cycles, cycles + 1))]
        if m.group("at"):
            return {"reply": (f"From {bed.strftime('%H:%M')}: wake at " + ", ".join(lines) +
                              f". {cycles} cycles ≈ {cycles * CYCLE_MIN + FALL_ASLEEP} min in bed."), "sources": []}
        if m.group("now"):
            return {"reply": (f"From {bed.strftime('%H:%M')} (now): wake at " + ", ".join(lines) +
                              f". {cycles} cycles ≈ {cycles * CYCLE_MIN + FALL_ASLEEP} min of sleep."), "sources": []}
        return {"reply": ("Go to bed at " + ", ".join(lines) +
                          f" for {cycles - 1}–{cycles + 1} full cycles (90 min each + ~{FALL_ASLEEP} min to fall asleep)."), "sources": []}
    return None


if __name__ == "__main__":
    assert match("split the bill of 4500 between 5 people with 10% tip")
    assert match("divide 1200 between 4 people")
    assert match("bmi 70 kg and 175 cm")
    assert match("price after 25% discount on 3999")
    assert match("fuel cost of 250 km at 15 km/l at 272 per litre")
    assert match("what time should I wake up if I go to bed at 11 pm")
    assert match("wake up if i go to bed now")
    assert not match("what's the weather")
    r = run("split the bill of 4500 between 5 people with 10% tip", {})
    assert "990" in r["reply"], r
    r2 = run("bmi 70 kg and 175 cm", {})
    assert "22.9" in r2["reply"], r2
    r3 = run("price after 25% discount on 3999", {})
    assert "2,999" in r3["reply"].replace(",", ","), r3
    r4 = run("fuel cost of 250 km at 15 km/l at 272 per litre", {})
    assert "4,533" in r4["reply"], r4
    r5 = run("what time should I wake up if I go to bed at 11 pm", {})
    assert "cycles" in r5["reply"], r5
    r6 = run("wake up if i go to bed now", {})
    assert "wake at" in r6["reply"] and "(now)" in r6["reply"], r6
    print("[self-check] quickcalc OK - billsplit/bmi/discount/fuel/sleep exact")
