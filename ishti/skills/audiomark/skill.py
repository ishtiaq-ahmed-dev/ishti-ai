"""Audio bookmarking (inventory domain 2 #19): timestamped notes into long recordings.

"bookmark 12:34 in D:/talk.mp3: key claim about batteries"
"mark 00:07:10 in D:/interview.wav: Ali mentions the deadline"
"my bookmarks for D:/talk.mp3", "audio bookmarks"

Timestamps are HH:MM or HH:MM:SS and are kept exactly as given (nothing is snapped or
guessed). Bookmarks are listed chronologically per file with their notes. Deleting by
number keeps it reversible. Store: data/bookmarks.db. T0, local write only.
"""
import os
import re
import sqlite3
import threading

from ishti import CONFIG
from ishti.skills import trace

DB = os.path.join(os.path.dirname(CONFIG["memory"]["db_path"]), "bookmarks.db")
_lock = threading.Lock()
I = re.I

TS = r"(?P<ts>\d{1,2}:\d{2}(?::\d{2})?)"
PATH = r"(?P<path>[A-Za-z]:[\\/][^\"']+?\.(?:mp3|wav|m4a|flac|ogg|opus|aac|wma))"
MARK = re.compile(rf"^\s*(?:bookmark|mark)\s+{TS}\s+in\s+{PATH}\s*[:\-]?\s*(?P<note>.*)$", I)
SHOW = re.compile(rf"^\s*(?:my\s+)?(?:audio\s+)?bookmarks?\s+(?:for|in|of)\s+(?:the\s+)?{PATH}\s*$", I)
DEL = re.compile(r"^\s*delete\s+bookmark\s+(?P<n>\d+)\s*$", I)


def _norm_path(p):
    return re.sub(r"\s+", " ", p.strip().lower())


def _db():
    os.makedirs(os.path.dirname(DB), exist_ok=True)
    c = sqlite3.connect(DB, timeout=10)
    c.execute("CREATE TABLE IF NOT EXISTS marks("
              "id INTEGER PRIMARY KEY, file TEXT, ts TEXT, note TEXT)")
    return c


def _secs(ts):
    parts = [int(x) for x in ts.split(":")]
    return parts[0] * 3600 + parts[-2] * 60 + parts[-1] if len(parts) == 3 else \
        parts[0] * 60 + parts[1]


def mark(ts, path, note, session=None):
    with _lock, _db() as c:
        c.execute("INSERT INTO marks(file, ts, note) VALUES(?,?,?)",
                  (_norm_path(path), ts, note.strip()))
        n = c.total_changes
    show = f" note: {note.strip()}" if note.strip() else ""
    return f"Bookmarked **{ts}** in `{os.path.basename(path)}`.{show}"


def show(path):
    with _lock:
        rows = _db().execute("SELECT id, ts, note FROM marks WHERE file=? ORDER BY id",
                             (_norm_path(path),)).fetchall()
    if not rows:
        return f"No bookmarks for `{os.path.basename(path)}` — \"bookmark 12:34 in {path}: note\"."
    rows.sort(key=lambda r: _secs(r[1]))
    lines = [f"**Bookmarks for {os.path.basename(path)}** ({len(rows)}):"]
    for i, (bid, ts, note) in enumerate(rows, 1):
        lines.append(f"{i}. {ts}" + (f" — {note}" if note else ""))
    return "\n".join(lines)


def delete(n):
    with _lock, _db() as c:
        rows = c.execute("SELECT id FROM marks ORDER BY id").fetchall()
        if not (1 <= int(n) <= len(rows)):
            return f"No bookmark #{n} (there are {len(rows)})."
        c.execute("DELETE FROM marks WHERE id=?", (rows[int(n) - 1][0],))
    return f"Bookmark #{n} deleted."


def match(text):
    t = text.strip()
    return bool(MARK.match(t) or SHOW.match(t) or DEL.match(t))


def run(text, session):
    t = text.strip()
    if (m := MARK.match(t)):
        trace(session, "audiomark", "mark")
        return {"reply": mark(m.group("ts"), m.group("path"), m.group("note"), session), "sources": []}
    if (m := SHOW.match(t)):
        trace(session, "audiomark", "show")
        return {"reply": show(m.group("path")), "sources": []}
    if (m := DEL.match(t)):
        trace(session, "audiomark", "delete")
        return {"reply": delete(m.group("n")), "sources": []}
    return None


if __name__ == "__main__":
    import tempfile
    DB = os.path.join(tempfile.mkdtemp(), "b.db")   # rebind: CONFIG read at import

    f = "D:/calls/talk.mp3"
    assert match("bookmark 12:34 in " + f + ": key claim")
    assert match("mark 00:07:10 in " + f)
    assert match("my bookmarks for " + f)
    assert not match("bookmark my place in the book")
    assert not match("mark this task done")

    r = run("bookmark 12:34 in " + f + ": the battery claim", {})
    assert "**12:34**" in r["reply"] and "battery claim" in r["reply"], r
    run("bookmark 00:05:00 in " + f, {})
    run("bookmark 01:10 in " + f + ": intro ends", {})
    r2 = run("my bookmarks for " + f, {})
    assert r2["reply"].index("01:10") < r2["reply"].index("05:00") < r2["reply"].index("12:34"), r2
    r3 = run("delete bookmark 2", {})
    assert "deleted" in r3["reply"], r3
    r4 = run("my bookmarks for " + f, {})
    assert "(2)" in r4["reply"] and "05:00" not in r4["reply"], r4   # deletion removed #2
    print("[self-check] audiomark OK - timestamps kept exactly, chronological per-file listing, "
          "delete-by-number reversible list, local data/bookmarks.db")
