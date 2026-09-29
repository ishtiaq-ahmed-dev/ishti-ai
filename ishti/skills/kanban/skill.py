"""Personal kanban (inventory domain 15 #8): todo → doing → done, with a WIP guard.

"kanban add write the report", "move write the report to doing", "kanban done for write the report",
"my kanban board", "clear kanban done", "delete card write the report"

Store: data/kanban.db (card, column, moved_at). The board view warns when >3 cards sit in
doing (the personal-kanban WIP rule: finish, don't start). T0, local write only.
"""
import os
import re
import sqlite3
import threading

from ishti import CONFIG
from ishti.skills import trace

DB = os.path.join(os.path.dirname(CONFIG["memory"]["db_path"]), "kanban.db")
_lock = threading.Lock()
I = re.I

COLUMNS = ("todo", "doing", "done")
ADD = re.compile(r"^\s*kanban\s+add\s+(?P<card>.+)$", I)
MOVE = re.compile(
    r"^\s*(?:move|push)\s+(?P<card>.+?)\s+to\s+(?P<col>todo|doing|done)\s*$|"
    r"^\s*kanban\s+(?:done|complete|finish)\s+(?:for\s+)?(?P<card2>.+)$", I)
BOARD = re.compile(r"^\s*(?:my\s+)?kanban\s+board|^\s*(?:show|list)\s+(?:my\s+)?kanban\s*$", I)
CLEAR = re.compile(r"^\s*clear\s+kanban\s+done\s*$", I)
DELETE = re.compile(r"^\s*(?:delete|remove)\s+(?:the\s+)?(?:kanban\s+)?card\s+(?P<card>.+)$", I)


def _db():
    os.makedirs(os.path.dirname(DB), exist_ok=True)
    c = sqlite3.connect(DB, timeout=10)
    c.execute("CREATE TABLE IF NOT EXISTS cards(id INTEGER PRIMARY KEY, card TEXT UNIQUE, col TEXT, moved_at TEXT)")
    return c


def _norm(s):
    return re.sub(r"\s+", " ", s.strip().lower()).rstrip(".")


def add(card):
    card = _norm(card)
    with _lock, _db() as c:
        try:
            import datetime as dt
            c.execute("INSERT INTO cards(card, col, moved_at) VALUES(?,?,?)",
                      (card, "todo", dt.date.today().isoformat()))
        except sqlite3.IntegrityError:
            return f"Card **{card}** is already on the board."
    return f"Added to **todo**: {card}."


def move(card, col):
    card, col = _norm(card), {"blocker": "doing"}.get(col, col)
    with _lock, _db() as c:
        n = c.execute("UPDATE cards SET col=?, moved_at=? WHERE card=?",
                      (col, __import__("datetime").date.today().isoformat(), card)).rowcount
        if n and col == "doing":
            doing = c.execute("SELECT COUNT(*) FROM cards WHERE col='doing'").fetchone()[0]
            warn = f"\n⚠ {doing} cards in doing — over the WIP limit of 3. Finish before starting." if doing > 3 else ""
            return (f"**{card}** → doing.{warn}") if n else f"No card named **{card}**."
    return (f"**{card}** → {col}." if n else f"No card named **{card}**.")


def board():
    with _lock:
        rows = _db().execute("SELECT card, col FROM cards ORDER BY id").fetchall()
    if not rows:
        return "The board is empty — \"kanban add write the report\"."
    buckets = {c: [] for c in COLUMNS}
    for card, col in rows:
        buckets.setdefault(col, []).append(card)
    lines = ["**Kanban board**"]
    for col in COLUMNS:
        cards = buckets.get(col, [])
        lines.append(f"**{col.upper()}** ({len(cards)}):")
        lines += [f"  - {c}" for c in cards]
    doing = len(buckets.get("doing", []))
    if doing > 3:
        lines.append(f"⚠ {doing} in doing — over the WIP limit of 3.")
    return "\n".join(lines)


def clear_done():
    with _lock, _db() as c:
        n = c.execute("DELETE FROM cards WHERE col='done'").rowcount
    return f"Cleared {n} done card(s)."


def delete(card):
    card = _norm(card)
    with _lock, _db() as c:
        n = c.execute("DELETE FROM cards WHERE card=?", (card,)).rowcount
    return f"Deleted **{card}**." if n else f"No card named **{card}**."


def match(text):
    t = text.strip()
    return bool(ADD.match(t) or MOVE.match(t) or BOARD.match(t) or CLEAR.match(t) or DELETE.match(t))


def run(text, session):
    t = text.strip()
    if (m := ADD.match(t)):
        trace(session, "kanban", "add")
        return {"reply": add(m.group("card")), "sources": []}
    if (m := MOVE.match(t)):
        card, col = (m.group("card"), m.group("col")) if m.group("card") else (m.group("card2"), "done")
        trace(session, "kanban", f"move {col}")
        return {"reply": move(card, col), "sources": []}
    if BOARD.match(t):
        trace(session, "kanban", "board")
        return {"reply": board(), "sources": []}
    if CLEAR.match(t):
        trace(session, "kanban", "clear done")
        return {"reply": clear_done(), "sources": []}
    if (m := DELETE.match(t)):
        trace(session, "kanban", "delete")
        return {"reply": delete(m.group("card")), "sources": []}
    return None


if __name__ == "__main__":
    import tempfile
    DB = os.path.join(tempfile.mkdtemp(), "k.db")   # rebind: CONFIG read at import

    assert match("kanban add write the report")
    assert match("move write the report to doing")
    assert match("kanban done for write the report")
    assert match("my kanban board")
    assert match("clear kanban done")
    assert not match("kanban of japan")
    assert not match("add milk to the list")          # not kanban-prefixed

    r = run("kanban add write the report", {})
    assert "Added to **todo**" in r["reply"], r
    run("kanban add task a", {})
    run("kanban add task b", {})
    run("kanban add task c", {})
    run("kanban add task d", {})
    for card in ("write the report", "task a", "task b", "task c"):
        run(f"move {card} to doing", {})
    r2 = run("my kanban board", {})
    assert "WIP limit" in r2["reply"], r2             # 4 in doing -> warning
    r3 = run("kanban done for write the report", {})
    assert "→ done" in r3["reply"], r3
    assert "Cleared 1 done card(s)" in run("clear kanban done", {})["reply"]
    r4 = run("move task d to done", {})
    assert "No card" not in r4["reply"], r4
    r5 = run("my kanban board", {})
    assert r5["reply"].count("task a") == 1, r5     # no double-listing on the board
    print("[self-check] kanban OK - add/move/done/board/clear/delete, WIP>3 warning, no double-listing, local data/kanban.db")
