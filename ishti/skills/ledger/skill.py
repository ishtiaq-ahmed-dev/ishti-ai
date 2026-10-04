"""Money the owner tracks: expenses ("I spent 500 on groceries"), spending reports by category and period, monthly
budgets, and a stock/crypto portfolio ("I bought 10 NVDA at 180") valued live with profit/loss. Local SQLite
(data/ledger.db); amounts in the owner's currency unless another is named."""
import datetime as dt
import os
import re
import sqlite3

from ishti import ROOT
from ishti.skills import trace

I = re.I
DB = os.path.join(ROOT, "data", "ledger.db")
CATS = {"food": r"grocer|food|restaurant|lunch|dinner|breakfast|meal|pizza|burger|snack|tea|coffee|fruit|vegetable|bakery|khana",
        "transport": r"fuel|petrol|diesel|uber|careem|bykea|taxi|rickshaw|bus|metro|parking|car\b|bike|toll|fare",
        "bills": r"bill|electric|gas\b|water\b|internet|wifi|mobile\s+package|recharge|rent|load|sui",
        "shopping": r"cloth|shoe|shirt|amazon|daraz|mall|shopping|gift|electronics|phone|laptop",
        "health": r"medicine|doctor|pharmacy|hospital|clinic|gym|test|lab",
        "education": r"book|course|fee|tuition|university|stationery|udemy|exam",
        "entertainment": r"movie|cinema|netflix|game|spotify|subscription|youtube\s+premium|outing",
        "donations": r"zakat|donat|charity|sadqa|sadaqah|khairat|fitrana"}
TAX_CATS = {"donations"}  # zakat and donations are deductible: tagged automatically
TAX_SAID = re.compile(r"\b(?:tax[-\s]?deductible|for\s+tax(?:es)?|business\s+expense|deductible)\b", I)
SPENT = re.compile(r"\b(?:i\s+)?(?:spent|paid|bought|gave|expense[d]?)\s+(?:rs\.?\s*|pkr\s*|\$)?(?P<amt>\d[\d,]*(?:\.\d+)?)\s*(?:k\b)?\s*"
                   r"(?:rs|rupees?|pkr|dollars?|usd)?\s*(?:on|for|at|in)?\s*(?P<what>[^.,;]*)", I)
BOUGHT_STOCK = re.compile(r"\b(?:i\s+)?(?P<side>bought|sold)\s+(?P<qty>\d+(?:\.\d+)?)\s+(?:shares?\s+(?:of\s+)?|units?\s+of\s+)?"
                          r"(?P<sym>[A-Za-z.\-]{1,12})\s+(?:shares?\s+)?(?:at|@|for)\s+(?:rs\.?\s*|\$)?(?P<price>\d[\d,]*(?:\.\d+)?)", I)
OPS = [
    ("tax_tag", re.compile(r"\b(?:mark|tag|flag|set)\s+(?:my\s+|the\s+)?(?:(?P<last>last|latest|previous)|(?P<what>[\w ]+?))\s+expense\s+as\s+"
                           r"(?P<undo>not\s+)?(?:tax[-\s]?deductible|deductible|tax|business)\b", I)),
    ("tax_report", re.compile(r"\btax\s+(?:report|summary|deductions?|expenses?)\b|\b(?:tax[-\s]?)?deductible\s+expenses\b", I)),
    ("trade", BOUGHT_STOCK),
    ("portfolio", re.compile(r"\b(?:my\s+)?(?:portfolio|holdings|investments?)\b", I)),
    ("budget_set", re.compile(r"\b(?:set|make)\s+(?:a\s+|my\s+)?(?:monthly\s+)?budget\b|\bbudget\s+(?:of|for)\b.*\d", I)),
    ("report", re.compile(r"\b(?:how\s+much\s+(?:did|have)\s+i\s+spen[dt]|my\s+(?:expenses|spending)|expenses?\s+(?:this|last|today|report)|spending\s+(?:this|last|report)|budget\s+(?:status|left|report))\b", I)),
    ("spent", SPENT),
]


def match(text):
    # "if I spend 250 on groceries and 175 on fuel, how much did I spend" is arithmetic about a hypothetical, not a log entry or a report
    return (any(rx.search(text) for _, rx in OPS) and not re.search(r"\b(?:should|would|could|want\s+to|price\s+of)\b", text, I)
            and not re.match(r"\s*(?:what\s+)?if\b", text, I))


def run(text, session):
    for name, rx in OPS:
        if m := rx.search(text):
            trace(session, "ledger", name)
            return {"reply": globals()["op_" + name](m, text, session), "sources": []}
    return None


def db():
    os.makedirs(os.path.dirname(DB), exist_ok=True)
    c = sqlite3.connect(DB)
    c.executescript("""CREATE TABLE IF NOT EXISTS expenses(day TEXT, amount REAL, currency TEXT, category TEXT, note TEXT);
                       CREATE TABLE IF NOT EXISTS budgets(month TEXT, category TEXT, amount REAL, UNIQUE(month, category));
                       CREATE TABLE IF NOT EXISTS trades(day TEXT, symbol TEXT, qty REAL, price REAL);""")
    if "tax" not in {r[1] for r in c.execute("PRAGMA table_info(expenses)")}:  # older databases: add the flag in place
        c.execute("ALTER TABLE expenses ADD COLUMN tax INTEGER DEFAULT 0")
    return c


def currency():
    from ishti.skills.markets.skill import owner_currency
    return owner_currency()


def category(what):
    for cat, rx in CATS.items():
        if re.search(rx, what, I):
            return cat
    return "other"


def op_spent(m, text, session):
    amount = float(m.group("amt").replace(",", "")) * (1000 if re.search(rf"{m.group('amt')}\s*k\b", text, I) else 1)
    cur = "USD" if re.search(r"\$|dollars?|usd", text, I) else currency()
    what = m.group("what").strip() or "misc"
    day = dt.date.today() - dt.timedelta(days=1 if re.search(r"\byesterday\b", text, I) else 0)
    cat = category(what)
    tax = int(cat in TAX_CATS or bool(TAX_SAID.search(text)))
    what = TAX_SAID.sub("", what).strip(" ,-") or what
    with db() as c:
        c.execute("INSERT INTO expenses(day, amount, currency, category, note, tax) VALUES(?,?,?,?,?,?)",
                  (day.isoformat(), amount, cur, cat, what[:80], tax))
        month = c.execute("SELECT COALESCE(SUM(amount),0) FROM expenses WHERE day LIKE ? AND category=? AND currency=?",
                          (day.strftime("%Y-%m") + "%", cat, cur)).fetchone()[0]
        budget = c.execute("SELECT amount FROM budgets WHERE month=? AND category IN (?, 'all')", (day.strftime("%Y-%m"), cat)).fetchone()
    note = f" {cat.title()} this month: {cur} {month:,.0f}" + (f" of {budget[0]:,.0f} budget." if budget else ".")
    return f"Logged {cur} {amount:,.0f} for {what} ({cat}{', tax-deductible' if tax else ''}).{note}"


def op_tax_tag(m, text, session):
    with db() as c:
        if m["last"]:
            row = c.execute("SELECT rowid, note, amount, currency FROM expenses ORDER BY rowid DESC LIMIT 1").fetchone()
        else:
            row = c.execute("SELECT rowid, note, amount, currency FROM expenses WHERE note LIKE ? ORDER BY rowid DESC LIMIT 1",
                            (f"%{m['what'].strip()}%",)).fetchone()
        if not row:
            return "I couldn't find that expense."
        c.execute("UPDATE expenses SET tax=? WHERE rowid=?", (0 if m["undo"] else 1, row[0]))
    return f"{row[1]} ({row[3]} {row[2]:,.0f}) is {'no longer' if m['undo'] else 'now'} marked tax-deductible."


def op_tax_report(m, text, session):
    year = re.search(r"\b(20\d\d)\b", text)
    year = year.group(1) if year else str(dt.date.today().year)
    with db() as c:
        rows = c.execute("SELECT day, note, amount, currency, category FROM expenses WHERE tax=1 AND day LIKE ? ORDER BY day",
                         (year + "%",)).fetchall()
    if not rows:
        return f"No tax-deductible expenses logged for {year}. Say \"mark my last expense as tax deductible\" or add " \
               "\"tax deductible\" when you log one (zakat and donations are tagged automatically)."
    totals = {}
    for _, _, amount, cur, _ in rows:
        totals[cur] = totals.get(cur, 0) + amount
    lines = [f"- {d}: {note} - {cur} {amount:,.0f} ({cat})" for d, note, amount, cur, cat in rows[-25:]]
    return f"Tax-deductible expenses {year}: " + ", ".join(f"{c} {t:,.0f}" for c, t in totals.items()) + "\n" + "\n".join(lines)


def _period(text):
    today = dt.date.today()
    if re.search(r"\blast\s+month\b", text, I):
        first = (today.replace(day=1) - dt.timedelta(days=1)).replace(day=1)
        return first, today.replace(day=1) - dt.timedelta(days=1), "last month"
    if re.search(r"\btoday\b", text, I):
        return today, today, "today"
    if re.search(r"\b(?:this\s+)?week\b", text, I):
        return today - dt.timedelta(days=today.weekday()), today, "this week"
    return today.replace(day=1), today, "this month"


def op_report(m, text, session):
    start, end, label = _period(text)
    only = next((cat for cat, rx in CATS.items() if re.search(rf"\b{cat}\b|{rx}", text, I) and cat != "bills" or re.search(r"\bbills?\b", text, I) and cat == "bills"), None)
    with db() as c:
        rows = c.execute("SELECT category, currency, SUM(amount), COUNT(*) FROM expenses WHERE day BETWEEN ? AND ? GROUP BY 1, 2 ORDER BY 3 DESC",
                         (start.isoformat(), end.isoformat())).fetchall()
        budgets = dict(c.execute("SELECT category, amount FROM budgets WHERE month=?", (start.strftime("%Y-%m"),)).fetchall())
    if only:
        rows = [r for r in rows if r[0] == only]
    if not rows:
        return f"No expenses logged {label}" + (f" for {only}" if only else "") + ". Tell me like \"I spent 500 on groceries\"."
    total = sum(r[2] for r in rows if r[1] == currency())
    lines = [f"| {cat.title()} | {cur} {amt:,.0f} | {n} |" + (f" {amt / budgets[cat] * 100:.0f}% of budget |" if cat in budgets else " |")
             for cat, cur, amt, n in rows]
    head = f"Spending {label}: **{currency()} {total:,.0f}**" + (f" of {budgets['all']:,.0f} budget ({total / budgets['all'] * 100:.0f}%)" if "all" in budgets else "")
    return head + "\n\n| Category | Amount | Entries | Budget |\n|---|---|---|---|\n" + "\n".join(lines)


def op_budget_set(m, text, session):
    amt = re.search(r"(\d[\d,]*(?:\.\d+)?)\s*(k\b)?", text, I)
    if not amt:
        return "How much? e.g. \"set a budget of 50000 for this month\" or \"budget for food 15000\"."
    value = float(amt.group(1).replace(",", "")) * (1000 if amt.group(2) else 1)
    cat = next((c for c in CATS if re.search(rf"\b{c}\b", text, I)), "all")
    month = dt.date.today().strftime("%Y-%m")
    with db() as c:
        c.execute("INSERT OR REPLACE INTO budgets VALUES(?,?,?)", (month, cat, value))
    return f"Budget for {'everything' if cat == 'all' else cat} in {dt.date.today():%B}: {currency()} {value:,.0f}."


def op_trade(m, text, session):
    side, qty = m.group("side").lower(), float(m.group("qty"))
    sym, price = m.group("sym").upper(), float(m.group("price").replace(",", ""))
    with db() as c:
        c.execute("INSERT INTO trades VALUES(?,?,?,?)", (dt.date.today().isoformat(), sym, qty if side == "bought" else -qty, price))
    return f"Recorded: {side} {qty:g} {sym} at {price:,.2f}. Say \"my portfolio\" for its live value."


def op_portfolio(m, text, session):
    from ishti.skills.markets import data
    from ishti.skills.markets.skill import psx_symbol
    with db() as c:
        rows = c.execute("SELECT symbol, SUM(qty), SUM(CASE WHEN qty > 0 THEN qty * price ELSE 0 END), SUM(CASE WHEN qty > 0 THEN qty ELSE 0 END) "
                         "FROM trades GROUP BY symbol HAVING SUM(qty) > 0").fetchall()
    if not rows:
        return "Your portfolio has no holdings recorded yet - I won't estimate. Tell me a trade like \"I bought 10 NVDA at 180\" and I'll start tracking it."
    lines, total_cost, total_value = [], {}, {}
    for sym, qty, cost_all, bought in rows:
        avg = cost_all / bought
        psx = psx_symbol(sym, "")  # KSE-100 names/tickers only; other symbols try Yahoo first
        try:
            s = None if psx else data.stock(sym)
            if s:
                now, cur = s["closes"][-1], s["currency"] or "USD"
            else:  # a PSX company (also smaller ones outside the KSE-100)
                q = data.psx(psx or sym)
                now, cur = q["price"], "PKR"
        except Exception:
            lines.append(f"| {sym} | {qty:g} | {avg:,.2f} | n/a | n/a |")
            continue
        pl = (now - avg) * qty
        total_cost[cur] = total_cost.get(cur, 0) + avg * qty
        total_value[cur] = total_value.get(cur, 0) + now * qty
        lines.append(f"| {sym} | {qty:g} | {avg:,.2f} | {now:,.2f} {cur} | {pl:+,.0f} ({(now / avg - 1) * 100:+.1f}%) |")
    summary = " · ".join(f"{cur} {v:,.0f} ({(v / total_cost[cur] - 1) * 100:+.1f}%)" for cur, v in total_value.items())
    return (f"Portfolio value: **{summary}**\n\n| Holding | Qty | Avg cost | Now | P&L |\n|---|---|---|---|---|\n" + "\n".join(lines)
            + "\n\n_Live prices; not financial advice._")


if __name__ == "__main__":
    import tempfile
    DB = os.path.join(tempfile.mkdtemp(), "l.db")
    cases = {"I spent 500 on groceries": "spent", "paid 3000 for the internet bill": "spent", "spent 1.5k on fuel yesterday": "spent",
             "how much did I spend this month": "report", "my expenses last month": "report", "set a budget of 50000 for this month": "budget_set",
             "I bought 10 NVDA at 180": "trade", "sold 5 shares of OGDC at 320": "trade", "show my portfolio": "portfolio"}
    for t, want in cases.items():
        got = next((n for n, rx in OPS if rx.search(t)), None)
        assert got == want, (t, got, want)
    assert not match("should I buy nvidia") and not match("what is the price of gold")
    s = {}
    assert "Logged" in op_spent(SPENT.search("I spent 500 on groceries"), "I spent 500 on groceries", s)
    assert "(food)" in op_spent(SPENT.search("spent 1.5k on lunch"), "spent 1.5k on lunch", s)
    op_budget_set(None, "budget for food 3000", s)
    rep = op_report(None, "my expenses this month", s)
    assert "2,000" in rep and "67% of budget" in rep, rep
    assert "tax-deductible" in op_spent(SPENT.search("paid 5000 zakat"), "paid 5000 zakat", s)
    assert "tax-deductible" not in op_spent(SPENT.search("spent 90000 on a laptop"), "spent 90000 on a laptop", s)
    assert "now marked" in run("mark my last expense as tax deductible", s)["reply"]
    assert "tax-deductible" in op_spent(SPENT.search("paid 12000 for a course"), "paid 12000 for a course, tax deductible", s)
    assert "no longer" in run("tag the course expense as not tax deductible", s)["reply"]
    rep = run("tax report", s)["reply"]
    assert "95,000" in rep and "zakat" in rep and "laptop" in rep and "course" not in rep, rep
    print("[self-check] ledger OK - routing, categories, budget, report, tax tags (auto zakat, last, by name, undo), tax report")
