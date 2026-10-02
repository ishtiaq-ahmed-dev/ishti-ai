"""Paper trading: the execution half of the markets desk, on simulated money only.

Why this exists: the checklist's trading domain was "not built" - markets/research.py can SAY
"buy NVDA under 180, stop 172" but nothing could act on the call, and jumping straight to a real
broker is a T3 escalation that needs its own design and approval. This
skill closes the gap safely: orders run against a LOCAL SQLITE LEDGER (paper_account /
paper_orders / paper_positions) with the same hard rules as everything else:

  1. PAPER ONLY - there is no broker connection and there are no real orders. Any ask to connect
     a real/live broker is refused with an explanation, never attempted, never routed to the LLM.
  2. CONFIRMATION - every order is T2 via ask_first: the owner sees "paper-buy 10 NVDA at 500?"
     and nothing is placed until a yes. Insufficient cash / insufficient position is refused
     BEFORE the gate, so a "yes" can never fire an order that was already invalid.
  3. VALIDATION - qty > 0, price > 0, cash >= cost for buys, held qty >= qty for sells; the
     account can never go negative or short on paper either.

Routing is deliberately narrow: the word "paper" is required for orders ("paper buy 10 NVDA"),
so the markets skill keeps every research question and nothing real-sounding lands here by
accident. Prices: use the price in the request ("at 500"); without one the live price is fetched
through the markets skill's own data layer (and a fetch failure asks for a price instead of
guessing).
"""
import os
import re
import sys
import time

if __package__ in (None, ""):  # run directly: python ishti/skills/trading/skill.py
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from ishti import CONFIG
from ishti.facts import get_facts
from ishti.skills import ask_first, trace

I = re.I


def _cfg():
    t = CONFIG.get("trading", {}) if isinstance(CONFIG, dict) else {}
    return {"starting_cash": float(t.get("starting_cash", 100000)),
            "currency": str(t.get("currency", "USD"))}


def _db():
    """(facts, cash, starting_cash) - creates the paper ledger and opens the account on first use."""
    f = get_facts()
    with f.lock, f.db:
        f.db.executescript("""
            CREATE TABLE IF NOT EXISTS paper_account(
                id INTEGER PRIMARY KEY CHECK(id=1), cash REAL, starting REAL, updated REAL);
            CREATE TABLE IF NOT EXISTS paper_orders(
                id INTEGER PRIMARY KEY, ts REAL, side TEXT, symbol TEXT, qty REAL, price REAL,
                status TEXT, note TEXT DEFAULT '');
            CREATE TABLE IF NOT EXISTS paper_positions(
                symbol TEXT PRIMARY KEY, qty REAL, avg_price REAL);
        """)
        row = f.db.execute("SELECT cash, starting FROM paper_account WHERE id=1").fetchone()
        if not row:
            start = _cfg()["starting_cash"]
            f.db.execute("INSERT INTO paper_account(id, cash, starting, updated) VALUES(1,?,?,?)",
                         (start, start, time.time()))
            f.db.execute("INSERT INTO paper_orders(ts, side, symbol, qty, price, status, note) "
                         "VALUES(?,?,?,?,?,?,?)",
                         (time.time(), "open", "ACCOUNT", 0, start, "done", "paper account opened"))
            row = (start, start)
    return f, float(row[0]), float(row[1])


# ---------------------------------------------------------------- the ledger (rules 2 + 3)
def _apply(side, symbol, qty, price, note="", commit=True):
    """One order against the paper ledger. commit=False VALIDATES ONLY (no write): ops use it
    before the T2 gate so a 'yes' can never fire an order that was already invalid; the confirmed
    action runs it again with commit=True, re-checking against current state. Returns
    (reply, None) when filled/valid or (None, refusal) when the account can't cover it."""
    f, cash, _starting = _db()
    cost = qty * price
    if side == "buy" and cost > cash + 1e-9:
        return None, (f"Not enough paper cash: {cost:,.2f} needed, {cash:,.2f} available. "
                      "Nothing was ordered.")
    with f.lock:
        pos = f.db.execute("SELECT qty, avg_price FROM paper_positions WHERE symbol=?", (symbol,)).fetchone()
        have = float(pos[0]) if pos else 0.0
    if side == "sell" and qty > have + 1e-9:
        return None, (f"You don't hold {qty:g} {symbol} on paper (held: {have:g}). "
                      "Nothing was ordered.")
    if not commit:
        return (f"validated: {side} {qty:g} {symbol} @ {price:,.2f}", None)
    with f.lock, f.db:
        new_cash = cash - cost if side == "buy" else cash + cost
        f.db.execute("UPDATE paper_account SET cash=?, updated=? WHERE id=1", (new_cash, time.time()))
        if side == "buy":
            new_qty = have + qty
            new_avg = ((have * float(pos[1] if pos else 0.0)) + cost) / new_qty
            f.db.execute("INSERT INTO paper_positions(symbol, qty, avg_price) VALUES(?,?,?) "
                         "ON CONFLICT(symbol) DO UPDATE SET qty=excluded.qty, avg_price=excluded.avg_price",
                         (symbol, new_qty, new_avg))
        else:  # sell: reduce, drop the row when flat (no shorting, even on paper)
            new_qty = have - qty
            if new_qty <= 1e-9:
                f.db.execute("DELETE FROM paper_positions WHERE symbol=?", (symbol,))
            else:
                f.db.execute("UPDATE paper_positions SET qty=? WHERE symbol=?", (new_qty, symbol))
        f.db.execute("INSERT INTO paper_orders(ts, side, symbol, qty, price, status, note) "
                     "VALUES(?,?,?,?,?,?,?)",
                     (time.time(), side, symbol, qty, price, "filled", note))
    verb = "Bought" if side == "buy" else "Sold"
    where = "debited from" if side == "buy" else "credited to"
    return (f"{verb} {qty:g} {symbol} @ {price:,.2f} on the PAPER account - {cost:,.2f} {where} "
            f"simulated cash. Paper cash now {new_cash:,.2f}."), None


def _market_price(symbol):
    """Live price through the markets skill's own data layer (never our own network code)."""
    try:
        from ishti.skills.markets import data
        s = data.stock(symbol)
        if s and s.get("closes"):
            return float(s["closes"][-1])
        q = data.psx(symbol)
        return float(q["price"]) if q and q.get("price") else None
    except Exception:
        return None


# ---------------------------------------------------------------- ops
ORDER = re.compile(r"\bpaper\s+(?P<side>buy|sell)\s+(?P<qty>[\d.]+)\s*(?:shares?\s+of\s*|x\s*|@\s*)?"
                   r"(?P<sym>[A-Za-z][A-Za-z0-9.\-]{0,9})(?:\s+at\s*(?:price\s*)?\$?(?P<price>[\d.]+))?", I)
CLOSE = re.compile(r"\bpaper\s+(?:close|exit|flatten)\s+(?:position\s+(?:of|in)\s+|the\s+)?"
                   r"(?P<sym>[A-Za-z][A-Za-z0-9.\-]{0,9})(?:\s+at\s*\$?(?P<price>[\d.]+))?", I)
ACCOUNT = re.compile(r"\bpaper\s+(?:account|portfolio|cash|balance|positions?|status|equity)\b", I)
HISTORY = re.compile(r"\bpaper\s+(?:trade|order|trading)\s+history\b|\bpaper\s+(?:trades|orders|fills)\b", I)
RESET = re.compile(r"\breset\s+(?:the\s+)?paper\s+(?:account|portfolio|trading)?\b|\bpaper\s+account\s+reset\b", I)
# rule 1: any real-broker ask is caught here and refused - it must never reach an LLM that might
# improvise, and it must never be half-interpreted as a paper order
BROKER = re.compile(r"\b(?:real|live|actual)\s+(?:broker|trading|trade|order|money|account)\b|"
                    r"\b(?:connect|hook(?:\s+up)?|link|plug(?:\s+in)?|attach|integrate|wire)\b[^.?!]*"
                    r"\b(?:broker|exchange|binance|robinhood|alpaca|coinbase|kraken|etoro)\b|"
                    r"\bbroker\s+(?:account|api|integration)\b", I)


def op_order(text, session):
    m = ORDER.search(text)
    if not m:
        return None
    side, symbol, qty = m["side"].lower(), m["sym"].upper(), float(m["qty"])
    if qty <= 0:
        return {"reply": "How many units? e.g. \"paper buy 10 shares of NVDA at 500\".", "sources": []}
    price = float(m["price"]) if m["price"] else _market_price(symbol)
    if not price or price <= 0:
        return {"reply": f"No live paper price for {symbol} right now - give one: "
                         f"\"paper {side} {qty:g} {symbol} at 123.45\".", "sources": []}
    ok, refusal = _apply(side, symbol, qty, price, commit=False)  # validate only: nothing written yet
    if refusal:
        return {"reply": refusal, "sources": []}

    def _do():
        out, err = _apply(side, symbol, qty, price, note="owner confirmed")  # re-checked on the yes
        trace(session, "trading", f"paper {side} {qty:g} {symbol}", status="done" if out else "refused")
        return out or err
    gate = (f"Place the PAPER order: {side} {qty:g} {symbol} at {price:,.2f} "
            f"({qty * price:,.2f} simulated cash)?")
    return ask_first(session, "trading", gate, _do)


def op_close(text, session):
    m = CLOSE.search(text)
    if not m:
        return None
    sym = m["sym"].upper()
    f, _cash, _starting = _db()
    with f.lock:
        pos = f.db.execute("SELECT qty, avg_price FROM paper_positions WHERE symbol=?", (sym,)).fetchone()
    if not pos or float(pos[0]) <= 0:
        return {"reply": f"No open paper position in {sym} - \"paper account\" lists what you hold.",
                "sources": []}
    qty = float(pos[0])
    price = float(m["price"]) if m["price"] else _market_price(sym)
    if not price or price <= 0:
        return {"reply": f"No live paper price for {sym} - give one: \"paper close {sym} at 123.45\".",
                "sources": []}
    ok, refusal = _apply("sell", sym, qty, price, commit=False)  # validate only
    if refusal:
        return {"reply": refusal, "sources": []}

    def _do():
        out, err = _apply("sell", sym, qty, price, note="position closed")
        trace(session, "trading", f"paper close {sym}", status="done" if out else "refused")
        return out or err
    return ask_first(session, "trading",
                     f"Close the paper position: sell {qty:g} {sym} at {price:,.2f}?", _do)


def op_status(text, session):
    if not ACCOUNT.search(text):
        return None
    f, cash, starting = _db()
    with f.lock:
        positions = f.db.execute("SELECT symbol, qty, avg_price FROM paper_positions ORDER BY symbol").fetchall()
        n = f.db.execute("SELECT COUNT(*) FROM paper_orders WHERE status='filled'").fetchone()[0]
    cost = sum(q * p for _s, q, p in positions)
    lines = [f"Paper account: cash {cash:,.2f}, positions at cost {cost:,.2f}, equity {cash + cost:,.2f} "
             f"(started {starting:,.2f}; {n} filled paper order(s)).",
             "Simulated money only - no broker connection, no real orders, by design."]
    lines += [f"- {sym}: {q:g} @ {p:,.2f} avg (cost {q * p:,.2f})" for sym, q, p in positions]
    return {"reply": "\n".join(lines), "sources": []}


def op_history(text, session):
    if not HISTORY.search(text):
        return None
    f, _cash, _starting = _db()
    with f.lock:
        rows = f.db.execute("SELECT ts, side, symbol, qty, price, status, note FROM paper_orders "
                            "ORDER BY id DESC LIMIT 10").fetchall()
    if not rows:
        return {"reply": "No paper orders yet - \"paper buy 10 shares of NVDA at 500\" starts the ledger.",
                "sources": []}
    lines = [f"- [{time.strftime('%d %b %H:%M', time.localtime(ts))}] {side.upper()} {qty:g} {sym} @ "
             f"{price:,.2f} ({status}{', ' + note if note else ''})"
             for ts, side, sym, qty, price, status, note in rows]
    return {"reply": "Last paper orders:\n" + "\n".join(lines), "sources": []}


def op_reset(text, session):
    if not RESET.search(text):
        return None
    f, _cash, starting = _db()

    def _do():
        with f.lock, f.db:
            f.db.execute("DELETE FROM paper_positions")
            f.db.execute("UPDATE paper_account SET cash=?, updated=? WHERE id=1", (starting, time.time()))
            f.db.execute("INSERT INTO paper_orders(ts, side, symbol, qty, price, status, note) "
                         "VALUES(?,?,?,?,?,?,?)",
                         (time.time(), "reset", "ACCOUNT", 0, starting, "done", "account reset"))
        trace(session, "trading", "paper account reset", status="done")
        return f"Paper account reset: positions cleared, cash back to {starting:,.2f}."
    return ask_first(session, "trading",
                     "Reset the paper account (clear all positions, restore starting cash)?", _do)


def _refuse_broker():
    return {"reply": "No real broker and no real orders - connecting one is deliberately not built, "
                     "so there is nothing to connect to. What exists is the PAPER account: your "
                     "markets-desk calls run on simulated money with a yes/no gate on every order "
                     "(\"paper buy 10 shares of NVDA at 500\"). Going live is a T3 escalation that "
                     "needs a broker-adapter design, a hard confirmation gate and the kill switch "
                     "wired in first - that is not built, on purpose.", "sources": []}


OPS = ((ORDER, op_order), (CLOSE, op_close), (RESET, op_reset), (HISTORY, op_history),
       (ACCOUNT, op_status))


def match(text):
    return bool(BROKER.search(text) or PAPER.search(text))


# "paper" is the routing keyword: orders, account, history, reset all start with it
PAPER = re.compile(r"\bpaper\s+(?:trading?|trades?|trader|account|portfolio|positions?|orders?|"
                   r"history|fills?|buy|sell|close|exit|flatten|reset|cash|balance|equity|status)\b|"
                   r"\bon\s+(?:the\s+)?paper\s+(?:account|side)\b", I)


def run(text, session):
    if BROKER.search(text):
        return _refuse_broker()
    for rx, op in OPS:
        if rx.search(text):
            out = op(text, session)
            if out is not None:
                return out
    return None


if __name__ == "__main__":
    import tempfile
    import ishti.facts as F
    F._facts = F.Facts(os.path.join(tempfile.mkdtemp(), "t.db"))

    # routing: paper phrases are ours, everything real-sounding is not
    assert match("paper buy 10 shares of NVDA at 500") and match("paper sell 5 btc at 60000")
    assert match("how is my paper account doing") and match("paper trade history")
    assert match("reset the paper account") and match("connect my real broker")
    assert match("hook up binance to the bot")
    assert not match("what's the price of gold today") and not match("should I buy BTC now")
    assert not match("analyze nvidia stock") and not match("buy a gold ring") and not match("check my email")
    assert not match("buy 5 shares of NVDA")  # the word "paper" is REQUIRED - no accidental orders
    assert not match("how did your market calls do") and not match("usd to pkr rate")

    # buy: T2 gate first, fill only on yes
    session = {"pending": None}
    r = run("paper buy 10 shares of NVDA at 500", session)
    assert "(yes/no)" in r["reply"] and session["pending"]["skill"] == "trading", r
    out = session["pending"]["action"]()
    session["pending"] = None  # the dispatcher clears the gate after it runs
    assert "Bought 10 NVDA" in out and "5,000" in out, out
    f, cash, starting = _db()
    with f.lock:
        pos = f.db.execute("SELECT qty, avg_price FROM paper_positions WHERE symbol='NVDA'").fetchone()
    assert pos and abs(pos[0] - 10) < 1e-9 and abs(pos[1] - 500) < 1e-9, pos
    assert abs(cash - (starting - 5000)) < 0.01, (cash, starting)

    # validation BEFORE the gate: overspend and oversell are refused, nothing written, no gate set
    r = run("paper buy 100000 shares of PLTR at 400", {})
    assert "Not enough paper cash" in r["reply"], r
    assert session["pending"] is None, session["pending"]
    r = run("paper sell 50 shares of NVDA at 500", {})
    assert "don't hold" in r["reply"], r

    # sell: gate, fill, position shrinks, cash credited
    r = run("paper sell 4 shares of NVDA at 520", session)
    assert "(yes/no)" in r["reply"], r
    out = session["pending"]["action"]()
    session["pending"] = None
    assert "Sold 4 NVDA" in out, out
    f, cash, _starting = _db()
    with f.lock:
        pos = f.db.execute("SELECT qty FROM paper_positions WHERE symbol='NVDA'").fetchone()
    assert pos and abs(pos[0] - 6) < 1e-9 and abs(cash - (starting - 5000 + 4 * 520)) < 0.01, (pos, cash)

    # close: flattens the whole position at the given price
    r = run("paper close NVDA at 510", session)
    assert "(yes/no)" in r["reply"], r
    out = session["pending"]["action"]()
    session["pending"] = None
    assert "Sold 6 NVDA" in out, out
    f, _cash, _s = _db()
    with f.lock:
        n = f.db.execute("SELECT COUNT(*) FROM paper_positions").fetchone()[0]
    assert n == 0, n

    # status + history reflect the ledger
    r = run("paper account", {})
    assert "Paper account" in r["reply"] and "no broker" in r["reply"].lower(), r
    r = run("paper trade history", {})
    assert "BUY 10 NVDA" in r["reply"] and "SELL 4 NVDA" in r["reply"], r

    # rule 1: real money asks are refused, never attempted
    r = run("connect my real broker", {})
    assert "No real broker" in r["reply"] and "not built, on purpose" in r["reply"], r

    # reset: gate, then back to starting cash
    r = run("reset the paper account", session)
    assert "(yes/no)" in r["reply"], r
    session["pending"]["action"]()
    session["pending"] = None
    f, cash, starting = _db()
    assert abs(cash - starting) < 0.01, (cash, starting)

    print("[self-check] trading OK - paper-only ledger, every order T2-gated, cash/position validation "
          "before the gate, close/history/reset, and real-broker asks refused by design")
