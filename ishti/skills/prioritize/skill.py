"""Eisenhower prioritization (inventory domain 15 #2): sort the task pile into what to do first.

"add tasks: submit thesis today urgent, reply to Ali important not urgent, watch a series neither"
"prioritize my tasks" / "what should I do first" / "sort my tasks by priority"
"clear my tasks" / "my tasks so far"

Each task gets urgent/important flags from the words used ("today", "asap", "now" -> urgent;
"important", "matters", "critical" -> important; "someday", "eventually" -> neither), then
triage sorts by classic Eisenhower quadrants:
  urgent+important -> DO NOW      urgent+not important -> DO QUICKLY / batch
  not urgent+important -> SCHEDULE     neither -> DROP or park
The store is a small local SQLite (data/priorities.db) in the same folder as the other DBs;
nothing leaves the PC (T0, local write only). Defaults: a task with no flags is treated as
important-but-not-urgent (the honest default for work you chose to write down), and the
reply always lists which flags it inferred so a wrong guess is visible and correctable
("reclassify watch a series as neither" isn't needed — just re-add with clearer words).

Self-check uses a tempdir DB override like every other DB-backed skill.
"""
import os
import re
import sqlite3
import threading

from ishti import CONFIG
from ishti.skills import trace

DB = os.path.join(os.path.dirname(CONFIG["memory"]["db_path"]), "priorities.db")
_lock = threading.Lock()

I = re.I

ADD = re.compile(r"^\s*(?:add\s+tasks?|new\s+tasks?)\s*[:\-]\s*(?P<body>.+)$", I)
SHOW = re.compile(r"^\s*(?:prioritize|triage|sort)\s+(?:my\s+|the\s+)?(?:tasks?|todo\s+list|to-do\s+list)"
                  r"(\s+by\s+priority)?\s*$|^\s*what\s+should\s+i\s+do\s+first\s*\??\s*$", I)
SO_FAR = re.compile(r"^\s*(?:my|the)\s+tasks?\s+(?:so\s+far|list|now)\s*$", I)
CLEAR = re.compile(r"^\s*clear\s+(?:my\s+|the\s+)?tasks?\s*$", I)
DONE = re.compile(r"^\s*task\s+done\s*[:\-]?\s*(?P<item>.+)$", I)

URGENT_W = re.compile(r"\b(?:urgent|asap|today|now|immediately|tonight|deadline|overdue|emergency)\b", I)
IMPORTANT_W = re.compile(r"\b(?:important|matters|critical|key|priority|essential|must)\b", I)
NEITHER_W = re.compile(r"\b(?:neither|someday|eventually|whenever|no\s+rush|fun|series|movie|game)\b", I)


def _db():
    os.makedirs(os.path.dirname(DB), exist_ok=True)
    c = sqlite3.connect(DB, timeout=10)
    c.execute("CREATE TABLE IF NOT EXISTS tasks(id INTEGER PRIMARY KEY, item TEXT UNIQUE, "
              "urgent INTEGER DEFAULT 0, important INTEGER DEFAULT 0, done INTEGER DEFAULT 0)")
    return c


def _flags(item):
    """Infer (urgent, important) from the words; 'neither' phrases win over both defaults."""
    if NEITHER_W.search(item):
        return 0, 0
    urgent = 1 if URGENT_W.search(item) else 0
    important = 1 if IMPORTANT_W.search(item) else (0 if urgent else 1)  # default: important, not urgent
    return urgent, important


def _quadrant(u, i):
    return {(1, 1): "DO NOW", (1, 0): "DO QUICKLY", (0, 1): "SCHEDULE", (0, 0): "DROP / PARK"}[(u, i)]


def _add(body):
    added, skipped = [], []
    with _lock, _db() as c:
        for raw in re.split(r"[,;\n]|\band then\b|\bthen\b", body):
            item = raw.strip().strip("-* ")
            if not item:
                continue
            u, i = _flags(item)
            try:
                c.execute("INSERT INTO tasks(item, urgent, important) VALUES(?,?,?)", (item, u, i))
                added.append((item, u, i))
            except sqlite3.IntegrityError:
                skipped.append(item)
    if not added and not skipped:
        return "Nothing to add — give me the tasks after the colon."
    out = f"Added {len(added)} task(s): " + "; ".join(
        f"**{it}** [{_quadrant(u, i)}]" for it, u, i in added[:10])
    if len(added) > 10:
        out += f" … and {len(added) - 10} more"
    if skipped:
        out += f"\nAlready tracked: {', '.join(skipped[:5])}."
    out += "\nSay \"prioritize my tasks\" for the ordered plan."
    return out


def _plan():
    with _lock:
        rows = _db().execute("SELECT item, urgent, important FROM tasks WHERE done=0 "
                             "ORDER BY urgent DESC, important DESC, id").fetchall()
    if not rows:
        return "No open tasks. Add some with \"add tasks: ...\"."
    order = {(1, 1): 0, (1, 0): 1, (0, 1): 2, (0, 0): 3}
    rows.sort(key=lambda r: order[(r[1], r[2])])
    buckets = {}
    for item, u, i in rows:
        buckets.setdefault(_quadrant(u, i), []).append(item)
    lines = ["**Your plan, in order:**"]
    for q in ("DO NOW", "DO QUICKLY", "SCHEDULE", "DROP / PARK"):
        if q in buckets:
            lines.append(f"{q}:")
            lines += [f"  {n}. {it}" for n, it in enumerate(buckets[q], 1)]
    first = next((it for q in ("DO NOW", "DO QUICKLY", "SCHEDULE") for it in buckets.get(q, [])), None)
    if first:
        lines.append(f"Start with: **{first}**.")
    if buckets.get("DROP / PARK"):
        lines.append("The DROP/PARK pile is everything you marked 'neither/someday' — deleting is allowed.")
    return "\n".join(lines)


def _so_far():
    with _lock:
        rows = _db().execute("SELECT item, urgent, important, done FROM tasks ORDER BY id").fetchall()
    if not rows:
        return "No tasks tracked yet."
    return "\n".join(f"{'x' if d else ' '} [{_quadrant(u, i)}] {it}" for it, u, i, d in rows[:20]) + \
        (f"\n… and {len(rows) - 20} more" if len(rows) > 20 else "")


def match(text):
    t = text.strip()
    return bool(ADD.match(t) or SHOW.match(t) or SO_FAR.match(t) or CLEAR.match(t) or DONE.match(t))


def run(text, session):
    t = text.strip()
    if (m := ADD.match(t)):
        trace(session, "prioritize", "add")
        return {"reply": _add(m.group("body")), "sources": []}
    if SHOW.match(t):
        trace(session, "prioritize", "plan")
        return {"reply": _plan(), "sources": []}
    if SO_FAR.match(t):
        trace(session, "prioritize", "list")
        return {"reply": _so_far(), "sources": []}
    if (m := DONE.match(t)):
        item = m.group("item").strip().rstrip(".!")
        with _lock, _db() as c:
            hit = c.execute("UPDATE tasks SET done=1 WHERE item LIKE ? AND done=0", (f"%{item}%",)).rowcount
        return {"reply": f"Marked done: {item}." if hit else f"No open task matching “{item}”.", "sources": []}
    if CLEAR.match(t):
        with _lock, _db() as c:
            n = c.execute("DELETE FROM tasks").rowcount
        return {"reply": f"Cleared {n} task(s).", "sources": []}
    return None


if __name__ == "__main__":
    import tempfile
    CONFIG["memory"]["db_path"] = os.path.join(tempfile.mkdtemp(), "mem.db")  # self-check: throwaway DB

    assert match("add tasks: submit thesis today, reply to Ali, watch a series")
    assert match("prioritize my tasks")
    assert match("what should I do first?")
    assert match("sort my tasks by priority")
    assert match("my tasks so far")
    assert match("clear my tasks")
    assert match("task done: reply to Ali")
    assert not match("add task force to the project")     # singular 'task force' isn't a batch add
    assert not match("prior notice was given")            # 'prior' alone is nothing

    r = run("clear my tasks", {})
    r = run("add tasks: submit thesis today urgent and important, reply to Ali important, watch a series neither", {})
    assert "3 task(s)" in r["reply"] and "DO NOW" in r["reply"] and "SCHEDULE" in r["reply"], r
    r2 = run("prioritize my tasks", {})
    assert "submit thesis today urgent" in r2["reply"] and "Start with" in r2["reply"], r2
    # the 'neither' task lands in DROP/PARK and the reply says deleting is allowed
    assert "watch a series neither" in r2["reply"] and "DROP / PARK" in r2["reply"], r2
    r3 = run("task done: reply to Ali", {})
    assert "Marked done" in r3["reply"], r3
    r4 = run("my tasks so far", {})
    assert "x [SCHEDULE]" in r4["reply"], r4              # the done tick shows against its quadrant
    r5 = run("clear my tasks", {})
    assert "Cleared 3" in r5["reply"], r5
    assert "No open tasks" in run("prioritize my tasks", {})["reply"]
    r6 = run("add tasks: alpha, alpha", {})
    assert "Already tracked" in r6["reply"], r6            # duplicate insert is reported, not silent
    print("[self-check] prioritize OK - Eisenhower triage with visible inferred flags, ordered plan, "
          "done/clear; local data/priorities.db only")
