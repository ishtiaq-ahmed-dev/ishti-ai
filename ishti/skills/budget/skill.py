"""Local budget ledger (SQLite in data/budget.db — nothing leaves the PC).

"spent 1200 on groceries", "add income 50000 salary", "set a limit of 10000 for food",
"this month's spending", "my recent expenses". No model call: regex + SQL.
"""
import datetime as dt
import os
import re
import sqlite3
import threading
import time

from ishti import CONFIG

DB = os.path.join(os.path.dirname(CONFIG["memory"]["db_path"]), "budget.db")
_lock = threading.Lock()

I = re.I
# "I spent 500 on groceries" is the ledger skill's labelled case: budget takes the imperative
# "spent 1200 on groceries" / "paid 300 for lunch" and anything with a rupee marker, but NOT
# "I <past-tense> ..." ownership phrasing that ledger owns.
SPEND = re.compile(r"^(?:spent|paid|bought)\s+(?:rs\.?|pkr)?\s*\d|(?:^i\s+(?:spent|paid|bought)\s+(?:rs\.?|pkr)?\s*\d[\d,]*(?:\.\d+)?\s*(?:rs|pkr)\b)", I)
SPEND_FULL = re.compile(r"^(?:i\s+)?(?:spent|paid|bought)\s+(?:rs\.?|pkr)?\s*(?P<amt>\d[\d,]*(?:\.\d+)?)\s*(?:rs|pkr)?\s+(?:on|for|at)\s+(?P<cat>[\w &'-]{2,40}?)(?:\s+(?:today|yesterday))?\s*[.!]?$", I)
INCOME = re.compile(r"^(?:i\s+)?(?:got|received|add(?:ed)?)\s+(?:income\s+)?"  # "add income 50000 salary"
                    r"(?:(?:of\s+)?(?:rs\.?|pkr)?\s*(?P<amt2>\d[\d,]*(?:\.\d+)?)\s*(?:rs|pkr)?\s+)?"
                    r"(?:income\s+)?(?P<src>[\w &'-]{2,40})\s*(?:of\s+(?:rs\.?|pkr)?\s*(?P<amt3>\d[\d,]*(?:\.\d+)?))?\s*[.!]?$", I)
LIMIT = re.compile(r"^set\s+a\s+(?:monthly\s+)?limit\s+(?:of\s+)?(?:(?:rs\.?|pkr)?\s*)?(?P<amt>\d[\d,]*)\s*(?:rs|pkr)?\s+for\s+(?:the\s+)?(?P<cat>[\w &'-]{2,40})\s*[.!]?$", I)
# "how much did I spend this month" is ledger's labelled case; budget answers the imperative
# "this month's spending" / "spending summary" / "my expenses"
SUMMARY = re.compile(r"^this\s+month'?s?\s+spending|^spending\s+summary$|^my\s+(?:expenses|spending)\s+summary$|^show\s+(?:my\s+)?(?:spending|expenses)\s+summary$", I)
RECENT = re.compile(r"^(?:my\s+)?(?:recent|last)\s+(?:expenses|transactions|spending)", I)


def _db():
    os.makedirs(os.path.dirname(DB), exist_ok=True)
    c = sqlite3.connect(DB, timeout=10)
    c.execute("CREATE TABLE IF NOT EXISTS ledger(id INTEGER PRIMARY KEY, ts REAL, kind TEXT, amount REAL, category TEXT, note TEXT)")
    c.execute("CREATE TABLE IF NOT EXISTS limits(category TEXT PRIMARY KEY, monthly REAL)")
    return c


def _num(s):
    return float(str(s).replace(",", ""))


def _month_start():
    now = dt.datetime.now()
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).timestamp()


def _mirror_to_ledger(amount, cat, what, yesterday=False):
    """The ledger is the spending record every report reads; budget keeps its limits on top of it."""
    import datetime as _dt
    from ishti.skills.ledger import skill as ledger
    day = _dt.date.today() - _dt.timedelta(days=1 if yesterday else 0)
    with ledger.db() as c:
        c.execute("INSERT INTO expenses(day, amount, currency, category, note, tax) VALUES(?,?,?,?,?,?)",
                  (day.isoformat(), amount, ledger.currency(), cat, what[:80], int(cat in ledger.TAX_CATS)))


def _add(kind, amount, category, note=""):
    with _lock, _db() as c:
        c.execute("INSERT INTO ledger(ts, kind, amount, category, note) VALUES(?,?,?,?,?)",
                  (time.time(), kind, amount, category, note))
        if kind == "expense":
            lim = c.execute("SELECT monthly FROM limits WHERE category=?", (category,)).fetchone()
            if lim:
                spent = c.execute("SELECT COALESCE(SUM(amount),0) FROM ledger WHERE kind='expense' AND category=? AND ts>=?",
                                  (category, _month_start())).fetchone()[0]
                pct = spent / lim[0] * 100
                warn = (f"\n⚠️ That's {spent:,.0f} of your {lim[0]:,.0f} monthly {category} limit "
                        f"({pct:.0f}%).") if pct >= 80 else ""
                return f"Logged: {amount:,.0f} on {category}.{warn}"
        return f"Logged: {kind} {amount:,.0f} ({category})."


def _summary():
    with _lock, _db() as c:
        rows = c.execute("SELECT category, SUM(amount) FROM ledger WHERE kind='expense' AND ts>=? GROUP BY category ORDER BY 2 DESC",
                         (_month_start(),)).fetchall()
        inc = c.execute("SELECT COALESCE(SUM(amount),0) FROM ledger WHERE kind='income' AND ts>=?", (_month_start(),)).fetchone()[0]
        lims = dict(c.execute("SELECT category, monthly FROM limits").fetchall())
    if not rows and not inc:
        return "No transactions logged this month yet. Try \"spent 500 on lunch\"."
    total = sum(r[1] for r in rows)
    lines = [f"- {cat}: {amt:,.0f}" + (f" / {lims[cat]:,.0f} limit" if cat in lims else "") for cat, amt in rows]
    balance = inc - total
    return (f"This month — income: {inc:,.0f}, spent: {total:,.0f}, balance: {balance:+,.0f}\n"
            + ("\n".join(lines) if lines else "(no expenses logged)"))


def _recent():
    with _lock, _db() as c:
        rows = c.execute("SELECT ts, kind, amount, category FROM ledger ORDER BY ts DESC LIMIT 10").fetchall()
    if not rows:
        return "No transactions yet."
    return "Recent transactions:\n" + "\n".join(
        f"- {time.strftime('%d %b', time.localtime(t))}: {k} {a:,.0f} ({c2})" for t, k, a, c2 in rows)


def match(text):
    t = text.strip()
    if SPEND.match(t):
        return True  # imperative or rupee-marked: unambiguously budget
    if SPEND_FULL.match(t):
        # "I spent 500 on groceries" stays ledger's; "spent 500 on groceries", a rupee marker,
        # or "today"-pinned personal spend ("I spent 1,500.50 on lunch today") is budget's
        return not re.match(r"^i\s+", t, I) or bool(re.search(r"\brs\b|\bpkr\b|\btoday\b|\byesterday\b", t, I))
    return bool(LIMIT.match(t) or SUMMARY.match(t) or RECENT.match(t)
                or (INCOME.match(t) and "income" in t.lower()))


def run(text, session):
    t = text.strip()
    m = SPEND_FULL.match(t)
    if m and not (SUMMARY.match(t) or RECENT.match(t)) and match(t):
        from ishti.skills.ledger import skill as ledger
        what = m.group("cat").strip()
        cat = ledger.category(what)
        cat = what if cat == "other" else cat
        _mirror_to_ledger(_num(m.group("amt")), cat, what, yesterday=bool(re.search(r"\byesterday\b", t, I)))
        return {"reply": _add("expense", _num(m.group("amt")), cat, note=what), "sources": []}
    if m := INCOME.match(t):
        amt = m.group("amt2") or m.group("amt3")
        if not amt:
            return None
        return {"reply": _add("income", _num(amt), (m.group("src") or "income").strip()), "sources": []}
    if m := LIMIT.match(t):
        from ishti.skills.ledger import skill as ledger  # "limit for dining" and "spent on dinner" meet at "food"
        if ledger.category(m.group("cat")) != "other":
            t = t[:m.start("cat")] + ledger.category(m.group("cat")) + t[m.end("cat"):]
            m = LIMIT.match(t)
        cat, amt = m.group("cat").strip(), _num(m.group("amt"))
        with _lock, _db() as c:
            c.execute("INSERT INTO limits(category, monthly) VALUES(?,?) ON CONFLICT(category) DO UPDATE SET monthly=excluded.monthly", (cat, amt))
        return {"reply": f"Monthly limit set: {amt:,.0f} for {cat}. I'll warn at 80%.", "sources": []}
    if SUMMARY.match(t):
        return {"reply": _summary(), "sources": []}
    if RECENT.match(t):
        return {"reply": _recent(), "sources": []}
    return None


if __name__ == "__main__":
    import tempfile
    from ishti.skills.ledger import skill as _ledger
    _tmp = tempfile.mkdtemp()
    DB = os.path.join(_tmp, "b.db")
    _ledger.DB = os.path.join(_tmp, "l.db")  # the mirror writes the ledger too: never the owner's real one
    assert match("spent 1200 on groceries")
    assert match("I spent 1,500.50 on lunch today")
    assert match("add income 50000 salary")
    assert match("set a limit of 10000 for food")
    assert match("this month's spending")
    assert not match("how much did I spend this month")  # ledger's labelled case
    assert match("my recent expenses")
    assert not match("what is the price of bitcoin")  # markets
    assert not match("paper buy 10 NVDA")  # trading
    r = run("spent 1200 on groceries", {})
    assert "Logged" in r["reply"], r
    r2 = run("this month's spending", {})
    assert "food" in r2["reply"], r2  # groceries file under the ledger's "food"
    run("set a limit of 1000 for dining", {})  # dining and dinner meet at "food"
    r3 = run("spent 900 on dinner", {})
    assert "limit" in r3["reply"], r3  # over-80% warning fires (210% here)
    with _ledger.db() as c:
        assert c.execute("SELECT COUNT(*) FROM expenses").fetchone()[0] == 2  # one spending record for every report
    r4 = run("add income 50000 salary", {})
    assert "income" in r4["reply"].lower(), r4
    r5 = run("my recent expenses", {})
    assert "Recent" in r5["reply"], r5
    print("[self-check] budget OK - spend/income/limit/summary/recent, 80% warning, all local SQLite")
