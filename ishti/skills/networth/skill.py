"""Net worth (local SQLite, data/networth.db) — deterministic arithmetic.

"track asset bank account 250000", "track liability bike loan 180000",
"my net worth", "list my assets", "remove asset car".
Numbers are stored and summed in code — never estimated by an LLM.
"""
import os
import re
import sqlite3
import threading

from ishti import CONFIG

DB = os.path.join(os.path.dirname(CONFIG["memory"]["db_path"]), "networth.db")
_lock = threading.Lock()

I = re.I

ADD = re.compile(r"^\s*(?:track|add|log)\s+(?:an?\s+|the\s+|my\s+)?(?P<kind>asset|liability|debt)\s+"
                 r"(?P<name>[\w &'.+\-]{2,40}?)\s*[:,]?\s*(?:rs\.?|pkr|\$)?\s*(?P<amt>\d[\d,.]*)\s*$", I)
REMOVE = re.compile(r"^\s*(?:remove|delete|forget)\s+(?:the\s+|my\s+)?(?P<rkind>asset|liability|debt)\s+"
                    r"(?P<rname>[\w &'.+\-]{2,40}?)\s*$", I)
NET = re.compile(r"^\s*(?:what(?:'s| is)\s+)?my\s+net\s+worth\s*\??$|^\s*net\s+worth\s*\??$|"
                 r"^\s*what\s+am\s+i\s+worth\s*\??$", I)
LIST = re.compile(r"^\s*(?:list\s+|show\s+)?my\s+(?P<what>assets(?:\s+and\s+liabilities)?|liabilities)\s*$"
                  r"|^\s*(?:list\s+|show\s+)(?P<what2>assets|liabilities)\s*$", I)


def _db():
    os.makedirs(os.path.dirname(DB), exist_ok=True)
    c = sqlite3.connect(DB, timeout=10)
    c.execute("""CREATE TABLE IF NOT EXISTS entries(
        name TEXT PRIMARY KEY, kind TEXT NOT NULL, amount REAL NOT NULL,
        added TEXT DEFAULT (datetime('now')))""")
    return c


def _norm(name):
    return re.sub(r"\s+", " ", name.strip().lower().rstrip(".!"))


def _fmt(x):
    x = float(x)
    return f"{x:,.0f}" if x == int(x) else f"{x:,.2f}"


def _add(kind, name, amt):
    kind = "liability" if kind.lower() in ("debt", "liability") else "asset"
    name, amt = _norm(name), float(amt.replace(",", ""))
    if not 1 <= amt <= 1_000_000_000:
        return "That amount doesn't look right — try: \"track asset bank account 250000\"."
    with _lock, _db() as c:
        row = c.execute("SELECT amount FROM entries WHERE name=?", (name,)).fetchone()
        c.execute("INSERT INTO entries(name, kind, amount) VALUES(?,?,?) "
                  "ON CONFLICT(name) DO UPDATE SET kind=excluded.kind, amount=excluded.amount",
                  (name, kind, amt))
    return (f"{'Updated' if row else 'Tracked'} {kind} **{name}**: Rs {_fmt(amt)}." +
            (f" (was Rs {_fmt(row[0])})" if row else "") +
            " Say \"my net worth\" for the running total.")


def _remove(kind, name):
    name = _norm(name)
    with _lock, _db() as c:
        row = c.execute("SELECT kind FROM entries WHERE name=?", (name,)).fetchone()
        if not row:
            return f"No entry called \"{name}\". Say \"my assets and liabilities\" to see what's tracked."
        c.execute("DELETE FROM entries WHERE name=?", (name,))
    return f"Removed {row[0]} **{name}** from the net-worth sheet."


def _net():
    with _lock, _db() as c:
        assets = c.execute("SELECT COALESCE(SUM(amount),0) FROM entries WHERE kind='asset'").fetchone()[0]
        debts = c.execute("SELECT COALESCE(SUM(amount),0) FROM entries WHERE kind='liability'").fetchone()[0]
        n = c.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
    if not n:
        return ("Nothing tracked yet. Try: \"track asset bank account 250000\" and "
                "\"track liability bike loan 180000\".")
    net = assets - debts
    verdict = "positive" if net >= 0 else "negative"
    return (f"Assets Rs {_fmt(assets)} − liabilities Rs {_fmt(debts)} = **net worth Rs {_fmt(net)}** ({verdict}).")


def _list(what):
    with _lock, _db() as c:
        want = ("asset", "liability") if "and" in (what or "") else \
               ("asset",) if "asset" in (what or "") else ("liability",)
        rows = c.execute("SELECT kind, name, amount FROM entries WHERE kind IN (?,?) "
                         "ORDER BY kind, amount DESC", want).fetchall()
    if not rows:
        return f"No {' or '.join(want)} entries yet."
    assets = [r for r in rows if r[0] == "asset"]
    debts = [r for r in rows if r[0] == "liability"]
    parts = []
    if "asset" in want and assets:
        parts.append("**Assets**\n" + "\n".join(f"- {n}: Rs {_fmt(a)}" for _k, n, a in assets))
    if "liability" in want and debts:
        parts.append("**Liabilities**\n" + "\n".join(f"- {n}: Rs {_fmt(a)}" for _k, n, a in debts))
    return "\n".join(parts)


def match(text):
    t = text.strip()
    return bool(ADD.match(t) or REMOVE.match(t) or NET.match(t) or LIST.match(t))


def run(text, session):
    t = text.strip()
    m = ADD.match(t)
    if m:
        return {"reply": _add(m.group("kind"), m.group("name"), m.group("amt")), "sources": []}
    m = REMOVE.match(t)
    if m:
        return {"reply": _remove(m.group("rkind"), m.group("rname")), "sources": []}
    if NET.match(t):
        return {"reply": _net(), "sources": []}
    m = LIST.match(t)
    if m:
        return {"reply": _list(m.group("what") or m.group("what2")), "sources": []}
    return None


if __name__ == "__main__":
    import tempfile
    DB = os.path.join(tempfile.mkdtemp(), "n.db")
    assert match("track asset bank account 250000")
    assert match("add asset car 3500000")
    assert match("track liability bike loan 180000")
    assert match("track debt credit card 45000")
    assert match("my net worth")
    assert match("net worth?")
    assert match("list my assets and liabilities")
    assert match("show liabilities")
    assert match("remove asset car")
    assert not match("I spent 500 on groceries")            # ledger
    assert not match("set a limit for groceries to 10000")   # budget
    assert not match("track subscription netflix 1500 monthly")  # subscriptions
    assert not match("how much did I spend this month")      # ledger
    run("track asset bank account 250000", {})
    run("add asset car 3500000", {})
    run("track liability bike loan 180000", {})
    r = run("my net worth", {})
    # 250,000 + 3,500,000 − 180,000 = 3,570,000
    assert "3,570,000" in r["reply"] and "positive" in r["reply"], r
    r2 = run("list my assets and liabilities", {})
    assert "bank account" in r2["reply"] and "bike loan" in r2["reply"], r2
    r3 = run("track asset bank account 300000", {})
    assert "Updated" in r3["reply"] and "250,000" in r3["reply"], r3
    r4 = run("remove asset car", {})
    assert "Removed" in r4["reply"], r4
    r5 = run("my net worth", {})
    assert "120,000" in r5["reply"], r5  # 300,000 − 180,000 = 120,000
    r6 = run("track liability credit card 500000", {})
    assert "Tracked" in r6["reply"], r6
    r7 = run("my net worth", {})
    assert "negative" in r7["reply"], r7  # 300,000 − 680,000 < 0
    r8 = run("remove asset nothing-here", {})
    assert "No entry" in r8["reply"], r8
    r9 = run("track asset bogus 999999999999", {})
    assert "doesn't look right" in r9["reply"], r9
    print("[self-check] networth OK - add/update/remove/list/net exact, all local SQLite")
