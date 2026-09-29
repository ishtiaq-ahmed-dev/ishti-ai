"""Pantry / expiry tracker (inventory domain 18): what's in the kitchen, what expires soon.

"add milk to the pantry, expires 2026-10-05", "add 2 bread to the pantry",
"what's expiring soon", "pantry list", "used up the milk", "remove bread from the pantry"

Store: data/pantry.db (item, qty, expires). "Expiring soon" = within 3 days or already past —
computed from today's date, stated openly. Quantities are whatever the owner says (default 1).
T0, local write only.
"""
import datetime as dt
import os
import re
import sqlite3
import threading

from ishti import CONFIG
from ishti.skills import trace

DB = os.path.join(os.path.dirname(CONFIG["memory"]["db_path"]), "pantry.db")
_lock = threading.Lock()
I = re.I

ADD = re.compile(
    r"^\s*add\s+(?:(?P<qty>\d+)\s+)?(?P<item>[\w .'-]{2,40}?)\s+to\s+the\s+pantry"
    r"(?:\s*,?\s*expires?\s+(?P<exp>\d{4}-\d{2}-\d{2}))?\s*$", I)
SOON = re.compile(r"^\s*(?:what'?s\s+)?expiring\s+soon\s*\??$", I)
LIST = re.compile(r"^\s*(?:my\s+)?pantry\s+list\s*$|^\s*what'?s\s+in\s+the\s+pantry\s*\??$", I)
USED = re.compile(r"^\s*used\s+up\s+(?:the\s+)?(?P<item>[\w .'-]{2,40})\s*$", I)
REMOVE = re.compile(r"^\s*remove\s+(?:the\s+)?(?P<item>[\w .'-]{2,40})\s+from\s+the\s+pantry\s*$", I)


def _norm(s):
    return re.sub(r"\s+", " ", s.strip().lower()).rstrip(".")


def _db():
    os.makedirs(os.path.dirname(DB), exist_ok=True)
    c = sqlite3.connect(DB, timeout=10)
    c.execute("CREATE TABLE IF NOT EXISTS pantry("
              "id INTEGER PRIMARY KEY, item TEXT UNIQUE, qty REAL DEFAULT 1, expires TEXT)")
    return c


def add(item, qty, expires, session=None):
    item, qty = _norm(item), float(qty or 1)
    with _lock, _db() as c:
        if c.execute("SELECT 1 FROM pantry WHERE item=?", (item,)).fetchone():
            c.execute("UPDATE pantry SET qty=qty+? WHERE item=?", (qty, item))
            return f"**{item}** is already on the list — quantity increased to the total on hand."
        c.execute("INSERT INTO pantry(item, qty, expires) VALUES(?,?,?)", (item, qty, expires))
    exp = f", expires {expires}" if expires else ""
    return f"Added **{qty:g} × {item}**{exp}."


def soon():
    today = dt.date.today()
    with _lock:
        rows = _db().execute(
            "SELECT item, qty, expires FROM pantry WHERE expires IS NOT NULL ORDER BY expires").fetchall()
    if not rows:
        return "No expiry dates stored — \"add milk to the pantry, expires 2026-10-05\"."
    lines = [f"**Expiring within 3 days or already past** (today is {today.isoformat()}):"]
    hits = 0
    for item, qty, exp in rows:
        days = (dt.date.fromisoformat(exp) - today).days
        if days <= 3:
            hits += 1
            when = "already past" if days < 0 else f"in {days} day(s)"
            lines.append(f"- **{item}** ({qty:g}) — expires {exp} ({when})")
    if not hits:
        lines.append("- nothing — the nearest expiry is far out.")
    return "\n".join(lines)


def list_pantry():
    with _lock:
        rows = _db().execute("SELECT item, qty, expires FROM pantry ORDER BY item").fetchall()
    if not rows:
        return "The pantry list is empty — \"add milk to the pantry, expires 2026-10-05\"."
    return "\n".join(["**Pantry:**"] +
                     [f"- {item} × {qty:g}" + (f" (expires {exp})" if exp else "")
                      for item, qty, exp in rows])


def used_up(item):
    item = _norm(item)
    with _lock, _db() as c:
        n = c.execute("DELETE FROM pantry WHERE item=?", (item,)).rowcount
    return f"**{item}** used up and removed." if n else f"No **{item}** in the pantry list."


def match(text):
    t = text.strip()
    return bool(ADD.match(t) or SOON.match(t) or LIST.match(t) or USED.match(t) or REMOVE.match(t))


def run(text, session):
    t = text.strip()
    if (m := ADD.match(t)):
        trace(session, "pantry", "add")
        return {"reply": add(m.group("item"), m.group("qty"), m.group("exp"), session), "sources": []}
    if SOON.match(t):
        trace(session, "pantry", "expiring soon")
        return {"reply": soon(), "sources": []}
    if LIST.match(t):
        trace(session, "pantry", "list")
        return {"reply": list_pantry(), "sources": []}
    if (m := USED.match(t)):
        trace(session, "pantry", "used up")
        return {"reply": used_up(m.group("item")), "sources": []}
    if (m := REMOVE.match(t)):
        trace(session, "pantry", "remove")
        return {"reply": used_up(m.group("item")), "sources": []}
    return None


if __name__ == "__main__":
    import tempfile
    DB = os.path.join(tempfile.mkdtemp(), "p.db")   # rebind: CONFIG read at import

    assert match("add milk to the pantry, expires 2026-10-05")
    assert match("add 2 bread to the pantry")
    assert match("what's expiring soon")
    assert match("pantry list")
    assert match("used up the milk")
    assert not match("add milk to the shopping cart")   # shopping owns carts
    assert not match("pantry of the world")

    r = run("add milk to the pantry, expires 2026-10-05", {})
    assert "Added **1 × milk**" in r["reply"], r
    run("add 2 bread to the pantry", {})
    r2 = run("add milk to the pantry", {})
    assert "already" in r2["reply"], r2
    r3 = run("pantry list", {})
    assert "milk × 2" in r3["reply"] and "bread × 2" in r3["reply"], r3   # dup add incremented 1→2
    r4 = run("what's expiring soon", {})
    assert "today is" in r4["reply"], r4                # the reference date is always shown
    r5 = run("used up the milk", {})
    assert "removed" in r5["reply"], r5
    r6 = run("pantry list", {})
    assert "milk" not in r6["reply"], r6
    print("[self-check] pantry OK - add/qty/expiry, expiring-soon computed from today's date, "
          "used-up removes, local data/pantry.db")
