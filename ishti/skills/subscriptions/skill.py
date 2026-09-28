"""Subscription tracker (local SQLite, data/subscriptions.db) — deterministic.

"track subscription netflix 1500 monthly on the 5th",
"add subscription spotify 299 per month", "my subscriptions",
"upcoming renewals", "cancel subscription spotify".
Totals are computed, never estimated by an LLM: weekly→x52/12, monthly→x1, yearly→/12.
"""
import datetime as dt
import os
import re
import sqlite3
import threading

from ishti import CONFIG

DB = os.path.join(os.path.dirname(CONFIG["memory"]["db_path"]), "subscriptions.db")
_lock = threading.Lock()

I = re.I

ADD = re.compile(r"^\s*(?:track|add|log)(?:\s+a|\s+the)?\s+subscription\s+"
                 r"(?P<name>[A-Za-z][\w &'.+-]{1,38}?)\s*[:,]?\s*(?:rs\.?|pkr|\$)?\s*(?P<amt>\d[\d,.]*)\s*"
                 r"(?:rs|pkr)?\s*(?:per|a|each|every|/)?\s*"
                 r"(?P<per>month|monthly|months?|year|yearly|annual|annually|year(?:s)?|week|weekly|weeks?)\b"
                 r"(?:\s+on\s+(?:the\s+)?(?P<day>\d{1,2})(?:st|nd|rd|th)?)?\s*$", I)
CANCEL = re.compile(r"^\s*(?:cancel|remove|delete)(?:\s+the|\s+my)?\s+subscription\s+(?P<name2>[A-Za-z][\w &'.+-]{1,38}?)\s*$", I)
LIST = re.compile(r"^\s*(?:my\s+|show\s+(?:my\s+)?|list\s+(?:my\s+)?)subscriptions?\s*(?:tracker|list)?\s*$", I)
RENEW = re.compile(r"^\s*(?:upcoming\s+|when\s+are\s+my\s+|my\s+)?renewals?(?:\s+due|\s+upcoming)?\s*$", I)

MONTHS = {"week": 52 / 12, "weekly": 52 / 12, "month": 1, "monthly": 1,
          "year": 1 / 12, "yearly": 1 / 12, "annual": 1 / 12, "annually": 1 / 12}


def _db():
    os.makedirs(os.path.dirname(DB), exist_ok=True)
    c = sqlite3.connect(DB, timeout=10)
    c.execute("""CREATE TABLE IF NOT EXISTS subs(
        name TEXT PRIMARY KEY, display TEXT, amount REAL NOT NULL, period TEXT NOT NULL,
        day INTEGER, active INTEGER DEFAULT 1, added TEXT DEFAULT (datetime('now')))""")
    return c


def _norm(name):
    return re.sub(r"\s+", " ", name.strip().lower().rstrip(".!"))


def _fmt(x):
    x = float(x)
    return f"{x:,.0f}" if x == int(x) else f"{x:,.2f}"


def _monthly(amount, per):
    per = per.lower()
    for k, mult in MONTHS.items():
        if per.startswith(k):
            return amount * mult
    return amount


def _add(name, amt, per, day):
    raw = re.sub(r"\s+", " ", name.strip().rstrip(".!"))  # keep original casing for display
    name, amt = _norm(name), float(amt.replace(",", ""))
    if not 1 <= amt <= 10_000_000:
        return "That amount doesn't look right — try: \"track subscription netflix 1500 monthly\"."
    day = int(day) if day else None
    if day and not 1 <= day <= 28:
        return "Renewal day must be 1–28 (months differ in length)."
    with _lock, _db() as c:
        have = c.execute("SELECT display FROM subs WHERE name=?", (name,)).fetchone()
        display = have[0] if have else raw  # keep the first-seen casing
        c.execute("INSERT INTO subs(name, display, amount, period, day) VALUES(?,?,?,?,?) "
                  "ON CONFLICT(name) DO UPDATE SET amount=excluded.amount, period=excluded.period, "
                  "day=excluded.day, active=1", (name, display, amt, per.lower(), day))
    monthly = _monthly(amt, per)
    return (f"**{display}** tracked: {_fmt(amt)} {per.lower()}" + (f", renews on the {day}th" if day else "") +
            f" ≈ Rs {_fmt(monthly)}/month. Say \"my subscriptions\" for the running total.")


def _cancel(name):
    name = _norm(name)
    with _lock, _db() as c:
        row = c.execute("SELECT display FROM subs WHERE name=?", (name,)).fetchone()
        if not row:
            known = [r[0] for r in c.execute("SELECT display FROM subs WHERE active=1").fetchall()]
            return ("No subscription called \"" + name + "\"." +
                    (f" You have: {', '.join(known)}." if known else ""))
        c.execute("DELETE FROM subs WHERE name=?", (name,))
    return f"**{row[0]}** removed from the tracker (actual cancellation is on you — done it in the app?)"


def _next_renewal(day):
    today = dt.date.today()
    try:
        cand = today.replace(day=day)
    except ValueError:
        return None
    if cand < today:
        nxt = cand.replace(year=cand.year + 1) if cand.month == 12 else cand.replace(month=cand.month + 1)
        cand = nxt
    return cand


def _list_subs():
    with _lock, _db() as c:
        rows = c.execute("SELECT display, amount, period, day FROM subs WHERE active=1 "
                         "ORDER BY amount DESC").fetchall()
    if not rows:
        return ("No subscriptions tracked yet. Try: \"track subscription netflix 1500 monthly on the 5th\".\n"
                "If you use the budget skill, that stays your ground truth; this is the recurring view.")
    lines, total = [], 0.0
    for name, amt, per, day in rows:
        m = _monthly(amt, per)
        total += m
        nxt = _next_renewal(day) if day else None
        when = f", renews {nxt.strftime('%d %b')}" if nxt else ""
        lines.append(f"| {name} | Rs {_fmt(amt)} {per} | Rs {_fmt(m)}/mo{when} |")
    yearly = total * 12
    return ("| Subscription | Cost | Monthly |\n|---|---|---|\n" + "\n".join(lines) +
            f"\n**Total: Rs {_fmt(total)}/mo — Rs {_fmt(yearly)}/year.**")


def _renewals(days=31):
    with _lock, _db() as c:
        rows = c.execute("SELECT display, amount, day FROM subs WHERE active=1 AND day IS NOT NULL").fetchall()
    if not rows:
        return ("No renewal days set. Add one like: \"track subscription netflix 1500 monthly on the 5th\".")
    today = dt.date.today()
    items = sorted((( _next_renewal(d), n, a) for n, a, d in rows if _next_renewal(d)), key=lambda x: x[0])
    soon = [(nxt, n, a) for nxt, n, a in items if (nxt - today).days <= days]
    if not soon:
        return "Nothing renews in the next " + str(days) + " days."
    lines = [f"- **{n}** — {nxt.strftime('%a %d %b')} (in {(nxt - today).days} d), Rs {_fmt(a)}" for nxt, n, a in soon]
    return "Upcoming renewals:\n" + "\n".join(lines)


def match(text):
    t = text.strip()
    return bool(ADD.match(t) or CANCEL.match(t) or LIST.match(t) or RENEW.match(t))


def run(text, session):
    t = text.strip()
    m = ADD.match(t)
    if m:
        return {"reply": _add(m.group("name"), m.group("amt"), m.group("per"), m.group("day")), "sources": []}
    m = CANCEL.match(t)
    if m:
        return {"reply": _cancel(m.group("name2")), "sources": []}
    if LIST.match(t):
        return {"reply": _list_subs(), "sources": []}
    if RENEW.match(t):
        return {"reply": _renewals(), "sources": []}
    return None


if __name__ == "__main__":
    import tempfile
    DB = os.path.join(tempfile.mkdtemp(), "s.db")
    assert match("track subscription netflix 1500 monthly on the 5th")
    assert match("add subscription spotify 299 per month")
    assert match("track subscription Adobe CC 30000 yearly")
    assert match("my subscriptions")
    assert match("show my subscriptions")
    assert match("upcoming renewals")
    assert match("cancel subscription spotify")
    assert not match("remind me to pay the electricity bill")   # reminders
    assert not match("set a limit for groceries to 10000")       # budget
    assert not match("I spent 500 on groceries")                 # ledger
    r = run("track subscription netflix 1500 monthly on the 5th", {})
    assert "tracked" in r["reply"] and "/month" in r["reply"], r
    run("add subscription spotify 299 per month", {})
    run("track subscription Adobe CC 30000 yearly", {})
    r2 = run("my subscriptions", {})
    assert "netflix" in r2["reply"] and "spotify" in r2["reply"] and "Adobe CC" in r2["reply"], r2
    # 1500 + 299 + 30000/12 = 4299/mo exactly, 51,588/yr
    assert "4,299/mo" in r2["reply"], r2
    assert "51,588/year" in r2["reply"], r2
    r3 = run("upcoming renewals", {})
    assert "netflix" in r3["reply"] and "renews" in r3["reply"] or "netflix" in r3["reply"], r3
    r4 = run("cancel subscription spotify", {})
    assert "removed" in r4["reply"], r4
    r5 = run("my subscriptions", {})
    assert "spotify" not in r5["reply"], r5
    assert "4,000/mo" in r5["reply"], r5   # 1500 + 2500
    r6 = run("cancel subscription spotify", {})
    assert "No subscription" in r6["reply"], r6
    r7 = run("track subscription bogus 99999999 monthly", {})
    assert "doesn't look right" in r7["reply"], r7
    r8 = run("track subscription bogus 500 monthly on the 40th", {})
    assert "1–28" in r8["reply"], r8
    print("[self-check] subscriptions OK - track/cancel/list/renewals, totals computed exactly, all local SQLite")
