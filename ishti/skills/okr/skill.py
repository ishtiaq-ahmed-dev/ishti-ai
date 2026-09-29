"""OKR tracker (inventory domain 15 #13): objectives, measurable key results, roll-up.

"set objective grow the channel"
"add key result 1000 subscribers to grow the channel"
"update kr subscribers to 400" / "update kr 2 of grow the channel to 400"
"my okrs"

Each key result has a target and a current value; an objective's progress is the average of
its KRs' completion — shown with the exact numbers. Store: data/okrs.db. T0, local only.
"""
import os
import re
import sqlite3
import threading

from ishti import CONFIG
from ishti.skills import trace

DB = os.path.join(os.path.dirname(CONFIG["memory"]["db_path"]), "okrs.db")
_lock = threading.Lock()
I = re.I

SET_OBJ = re.compile(r"^\s*(?:set|new|add)\s+objective\s+(?P<name>.+)$", I)
ADD_KR = re.compile(
    r"^\s*(?:add\s+)?key\s+result\s+(?P<target>\d[\d,]*(?:\.\d+)?)\s*(?P<unit>[%a-z ]{0,15}?)\s*(?:to|for|of|on)\s+"
    r"(?:the\s+)?(?:objective\s+)?(?P<obj>.+)$", I)
UPDATE = re.compile(
    r"^\s*(?:update|set)\s+(?:the\s+)?kr?\w*\s*(?:(?P<idx>\d+)\s+(?:of|on|in)\s+(?P<obj>.+?)\s*)?to\s+"
    r"(?P<val>\d[\d,]*(?:\.\d+)?)\s*$|^\s*(?:update|set)\s+(?:the\s+)?(?P<kr>[a-z][\w &'-]{2,40})\s+(?:kr\s+)?to\s+"
    r"(?P<val2>\d[\d,]*(?:\.\d+)?)\s*$", I)
LIST = re.compile(r"^\s*(?:my\s+)?okrs?\s*$|^\s*(?:show|list)\s+(?:my\s+)?okrs?\s*$", I)
KR_LIST = re.compile(r"^\s*(?:key\s+results?|krs?)\s+(?:of|for|on)\s+(?:the\s+)?(?P<obj>.+)$", I)


def _db():
    os.makedirs(os.path.dirname(DB), exist_ok=True)
    c = sqlite3.connect(DB, timeout=10)
    c.execute("CREATE TABLE IF NOT EXISTS objectives(id INTEGER PRIMARY KEY, name TEXT UNIQUE)")
    c.execute("CREATE TABLE IF NOT EXISTS krs(id INTEGER PRIMARY KEY, obj_id INTEGER, kr TEXT, "
              "target REAL, current REAL DEFAULT 0)")
    return c


def _norm(s):
    return re.sub(r"\s+", " ", s.strip().lower()).rstrip(".!")


def _num(s):
    return float(str(s).lower().replace(",", ""))


def set_obj(name):
    name = _norm(name)
    with _lock, _db() as c:
        try:
            c.execute("INSERT INTO objectives(name) VALUES(?)", (name,))
        except sqlite3.IntegrityError:
            return f"Objective **{name}** already exists — add key results to it."
    return f"Objective **{name}** set. Now add measurable key results, e.g. \"add key result 1000 subscribers to {name}\"."


def add_kr(target, unit, obj):
    obj = _norm(obj)
    unit = (unit or "").strip()
    kr = f"{target:g} {unit}".strip()
    with _lock, _db() as c:
        row = c.execute("SELECT id FROM objectives WHERE name=?", (obj,)).fetchone()
        if not row:
            c.execute("INSERT INTO objectives(name) VALUES(?)", (obj,))
        obj_id = c.execute("SELECT id FROM objectives WHERE name=?", (obj,)).fetchone()[0]
        c.execute("INSERT INTO krs(obj_id, kr, target, current) VALUES(?,?,?,0)",
                  (obj_id, _norm(kr) if unit else str(target), _num(target)))
    return f"Key result **{kr}** added to **{obj}** (progress 0/{target:g})."


def _kr_rows(obj_name):
    with _lock:
        return _db().execute(
            "SELECT k.id, k.kr, k.target, k.current FROM krs k JOIN objectives o ON o.id=k.obj_id "
            "WHERE o.name=? ORDER BY k.id", (obj_name,)).fetchall()


def update(m):
    if m.group("val") is not None:      # by index + objective, or plain kr-text match
        val = _num(m.group("val"))
        idx, obj = m.group("idx"), m.group("obj")
        if idx and obj:
            rows = _kr_rows(_norm(obj))
            if not rows or not (1 <= int(idx) <= len(rows)):
                return f"No key result #{idx} on **{_norm(obj)}**."
            kid, kr, target, _cur = rows[int(idx) - 1]
        else:
            kr_text = m.group("kr").lower() if m.group("kr") else None
            with _lock:
                rows = _db().execute(
                    "SELECT k.id, k.kr, k.target, k.current FROM krs k WHERE k.kr LIKE ?",
                    (f"%{kr_text}%",)).fetchall() if kr_text else []
            if not rows:
                return "Which key result? e.g. \"update kr subscribers to 400\"."
            if len(rows) > 1:
                names = "; ".join(r[1] for r in rows)
                return f"Several key results match — name the objective. Matches: {names}."
            kid, kr, target, _cur = rows[0]
    else:
        return "Update to what value?"
    with _lock, _db() as c:
        c.execute("UPDATE krs SET current=? WHERE id=?", (val, kid))
    pct = min(100.0, val / target * 100) if target else 0
    return f"Key result **{kr}**: {val:g}/{target:g} ({pct:.0f}%)."


def list_okrs():
    with _lock:
        objs = _db().execute("SELECT id, name FROM objectives ORDER BY id").fetchall()
    if not objs:
        return "No objectives yet — \"set objective grow the channel\"."
    lines = ["**Your OKRs:**"]
    for oid, name in objs:
        rows = _kr_rows(name)
        if not rows:
            lines.append(f"- **{name}** — no key results yet.")
            continue
        pcts = [min(100.0, (cur / tgt) * 100) if tgt else 0 for _i, _k, tgt, cur in rows]
        roll = sum(pcts) / len(pcts)
        lines.append(f"- **{name}** — {roll:.0f}% (avg of {len(rows)} KR)")
        lines += [f"    · {kr}: {cur:g}/{tgt:g} ({p:.0f}%)" for (_i, kr, tgt, cur), p in zip(rows, pcts)]
    return "\n".join(lines)


def kr_list(obj):
    rows = _kr_rows(_norm(obj))
    if not rows:
        return f"No key results on **{_norm(obj)}**."
    return "\n".join(f"{i}. {kr}: {cur:g}/{tgt:g}" for i, (_id, kr, tgt, cur) in enumerate(rows, 1))


def match(text):
    t = text.strip()
    return bool(SET_OBJ.match(t) or ADD_KR.match(t) or UPDATE.match(t) or LIST.match(t) or KR_LIST.match(t))


def run(text, session):
    t = text.strip()
    if (m := SET_OBJ.match(t)):
        trace(session, "okr", "set objective")
        return {"reply": set_obj(m.group("name")), "sources": []}
    if (m := ADD_KR.match(t)):
        trace(session, "okr", "add kr")
        return {"reply": add_kr(_num(m.group("target")), m.group("unit"), m.group("obj")), "sources": []}
    if (m := UPDATE.match(t)):
        trace(session, "okr", "update kr")
        return {"reply": update(m), "sources": []}
    if KR_LIST.match(t):
        trace(session, "okr", "kr list")
        return {"reply": kr_list(KR_LIST.match(t).group("obj")), "sources": []}
    if LIST.match(t):
        trace(session, "okr", "list")
        return {"reply": list_okrs(), "sources": []}
    return None


if __name__ == "__main__":
    import tempfile
    DB = os.path.join(tempfile.mkdtemp(), "o.db")   # rebind: CONFIG read at import

    assert match("set objective grow the channel")
    assert match("add key result 1000 subscribers to grow the channel")
    assert match("update kr 1 of grow the channel to 400")
    assert match("my okrs")
    assert not match("objective journalism")
    assert not match("add key result to my chain")    # needs a number

    r = run("set objective grow the channel", {})
    assert "Objective **grow the channel** set" in r["reply"], r
    r2 = run("add key result 1000 subscribers to grow the channel", {})
    assert "0/1000" in r2["reply"], r2
    run("add key result 12 videos to grow the channel", {})
    r3 = run("update kr 1 of grow the channel to 400", {})
    assert "400/1000 (40%)" in r3["reply"], r3
    run("update kr 2 of grow the channel to 6", {})   # 50%
    r4 = run("my okrs", {})
    assert "**grow the channel** — 45% (avg of 2 KR)" in r4["reply"], r4
    assert "400/1000 (40%)" in r4["reply"], r4
    print("[self-check] okr OK - objectives, key results with targets, updates by index, "
          "average roll-up computed exactly, local data/okrs.db")
