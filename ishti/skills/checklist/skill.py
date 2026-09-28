"""Persistent checklists (local SQLite, data/checklists.db).

"checklist for the trip: passport, tickets, charger" (creates + adds),
"add charger to the trip checklist", "tick passport", "my trip checklist",
"what's left on the trip checklist", "reset the trip checklist".
"""
import os
import re
import sqlite3
import threading

from ishti import CONFIG

DB = os.path.join(os.path.dirname(CONFIG["memory"]["db_path"]), "checklists.db")
_lock = threading.Lock()

I = re.I

NEW = re.compile(r"^\s*(?:new\s+)?checklist\s+(?:for|named|called)\s+(?P<name>[\w &'-]{2,40}?)\s*[:\-]\s*(?P<items>.+)$", I)
ADD = re.compile(r"^\s*add\s+(?P<item>[\w &'-]{2,60}?)\s+to\s+(?:the\s+|my\s+)?(?P<name>[\w &'-]{2,40}?)\s+checklist\s*$", I)
TICK = re.compile(r"^\s*(?:tick|check)\s+off\s+(?P<item>[\w &'-]{2,60})\s*$|^\s*(?:tick|mark)\s+(?P<item2>[\w &'-]{2,60}?)\s+(?:as\s+)?done\s*$", I)
SHOW = re.compile(r"^\s*(?:my\s+|the\s+)?(?P<name>[\w &'-]{2,40}?)\s+checklist\s*$|^\s*(?:show|open)\s+(?:the\s+|my\s+)?(?P<name2>[\w &'-]{2,40}?)\s+checklist\s*$", I)
LEFT = re.compile(r"^\s*what(?:'s| is)\s+left\s+on\s+(?:the\s+|my\s+)?(?P<name>[\w &'-]{2,40}?)\s+checklist\s*$", I)
RESET = re.compile(r"^\s*reset\s+(?:the\s+|my\s+)?(?P<name>[\w &'-]{2,40}?)\s+checklist\s*$", I)
LISTS = re.compile(r"^\s*(?:my\s+)?checklists?\s*$", I)


def _db():
    os.makedirs(os.path.dirname(DB), exist_ok=True)
    c = sqlite3.connect(DB, timeout=10)
    c.execute("CREATE TABLE IF NOT EXISTS items(id INTEGER PRIMARY KEY, list TEXT, item TEXT, done INTEGER DEFAULT 0, UNIQUE(list, item))")
    return c


def _norm(name):
    n = re.sub(r"\s+", " ", name.strip().lower().rstrip(".!"))
    return re.sub(r"^(?:the|my)\s+", "", n)  # "the trip" and "trip" are one list


def _add_items(name, items):
    name = _norm(name)
    added, dup = [], []
    with _lock, _db() as c:
        for it in re.split(r"[,;\n]|\band\b", items):
            it = it.strip().strip("-* ")
            if not it:
                continue
            try:
                c.execute("INSERT INTO items(list, item) VALUES(?,?)", (name, it))
                added.append(it)
            except sqlite3.IntegrityError:
                dup.append(it)
    out = f"Checklist **{name}**: added {len(added)} item(s): " + ", ".join(added)
    if dup:
        out += f" (already there: {', '.join(dup)})"
    return out + "."


def _tick(item):
    item_l = item.strip().lower()
    with _lock, _db() as c:
        rows = c.execute("SELECT id, list, item FROM items WHERE LOWER(item) LIKE ? AND done=0", (f"%{item_l}%",)).fetchall()
        if not rows:
            return f"No unticked item matching \"{item.strip()}\"."
        if len(rows) > 1:
            return "Which one? " + "; ".join(f"{r[1]}: {r[2]}" for r in rows[:6])
        c.execute("UPDATE items SET done=1 WHERE id=?", (rows[0][0],))
        left = c.execute("SELECT COUNT(*) FROM items WHERE list=? AND done=0", (rows[0][1],)).fetchone()[0]
        return f"✅ {rows[0][2]} ({rows[0][1]}) — {left} left on that list."


def _show(name):
    name = _norm(name)
    with _lock, _db() as c:
        rows = c.execute("SELECT item, done FROM items WHERE list=? ORDER BY id", (name,)).fetchall()
    if not rows:
        return f"No checklist named \"{name}\" yet. Create one: \"checklist for {name}: item one, item two\"."
    lines = [("✅ " if d else "⬜ ") + it for it, d in rows]
    left = sum(1 for _i, d in rows if not d)
    return (f"**{name}** ({left} of {len(rows)} left):\n" + "\n".join(lines)) if left else \
           f"**{name}** — all {len(rows)} done! 🎉"


def _lists():
    with _lock, _db() as c:
        rows = c.execute("SELECT list, COUNT(*), SUM(done) FROM items GROUP BY list ORDER BY list").fetchall()
    if not rows:
        return "No checklists yet. Try: \"checklist for the trip: passport, tickets\"."
    return "Your checklists:\n" + "\n".join(f"- {n}: {t - d}/{t} done" for n, t, d in rows)


def match(text):
    t = text.strip()
    return bool(NEW.match(t) or ADD.match(t) or TICK.match(t) or LEFT.match(t)
                or RESET.match(t) or LISTS.match(t) or SHOW.match(t))


def run(text, session):
    t = text.strip()
    m = NEW.match(t)
    if m:
        return {"reply": _add_items(m.group("name"), m.group("items")), "sources": []}
    m = ADD.match(t)
    if m:
        return {"reply": _add_items(m.group("name"), m.group("item")), "sources": []}
    m = TICK.match(t)
    if m:
        return {"reply": _tick(m.group("item") or m.group("item2")), "sources": []}
    m = LEFT.match(t)
    if m:
        return {"reply": _show(m.group("name")), "sources": []}
    m = RESET.match(t)
    if m:
        name = _norm(m.group("name"))
        with _lock, _db() as c:
            n = c.execute("SELECT COUNT(*) FROM items WHERE list=?", (name,)).fetchone()[0]
            if not n:
                return f"No checklist named \"{name}\" yet. Create one: \"checklist for {name}: item one, item two\"."
            c.execute("UPDATE items SET done=0 WHERE list=?", (name,))
            return {"reply": (f"Checklist \"{name}\" reset — all {n} item(s) unticked and open again. "
                              f"(Say \"checklists\" to see every list; items are kept, not deleted.)"), "sources": []}
    if LISTS.match(t):
        return {"reply": _lists(), "sources": []}
    m = SHOW.match(t)
    if m:
        return {"reply": _show(m.group("name") or m.group("name2")), "sources": []}
    return None


if __name__ == "__main__":
    import tempfile
    DB = os.path.join(tempfile.mkdtemp(), "c.db")
    assert match("checklist for the trip: passport, tickets and charger")
    assert match("add sunscreen to the trip checklist")
    assert match("tick off passport")
    assert match("mark sunscreen as done")
    assert match("my trip checklist")
    assert match("what's left on the trip checklist")
    assert match("checklists")
    assert not match("note that I packed the charger")  # notes skill
    r = run("checklist for the trip: passport, tickets and charger", {})
    assert "3 item(s)" in r["reply"], r
    run("add sunscreen to the trip checklist", {})
    r2 = run("tick off passport", {})
    assert "✅" in r2["reply"] and "3 left" in r2["reply"], r2
    r3 = run("what's left on the trip checklist", {})
    assert "⬜ tickets" in r3["reply"], r3
    r4 = run("reset the trip checklist", {})
    assert "unticked" in r4["reply"], r4
    r5 = run("my trip checklist", {})
    assert "⬜ passport" in r5["reply"], r5  # reset keeps the items, un-ticks them all
    print("[self-check] checklist OK - create/add/tick/show/left/reset, all local SQLite")
