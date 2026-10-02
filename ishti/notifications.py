"""Notifications the bell shows: reminders, finished background jobs, downloads, alerts and errors.

One small SQLite table in the same database as facts/reminders (ishti/facts.py pattern), so the list
survives a restart. add() also publishes a "notification" event on the existing /ws socket; the server
exposes GET /api/notifications and POST /api/notifications/read on top of this module.

Never raises (a missing bell row must not break a reminder). Self-check: python -m ishti.notifications
"""
from __future__ import annotations

import json
import time

from ishti.core.logutil import swallowed
from ishti.facts import get_facts

KINDS = ("reminder", "job", "download", "alert", "error")
KEEP = 1000  # newest rows kept; older notifications are pruned on insert (they are notices, not records)

_COLS = "id, ts, kind, title, body, meta, read"


def _db():
    f = get_facts()
    with f.lock, f.db:
        f.db.execute("CREATE TABLE IF NOT EXISTS notifications("
                     "id INTEGER PRIMARY KEY, ts REAL, kind TEXT, title TEXT, body TEXT, meta TEXT, "
                     "read INTEGER DEFAULT 0)")
    return f


def _row(r):
    try:
        meta = json.loads(r[5]) if r[5] else {}
    except (TypeError, ValueError):
        meta = {}
    return {"id": r[0], "ts": r[1], "kind": r[2], "title": r[3], "body": r[4], "meta": meta, "read": bool(r[6])}


def add(kind, title, body="", meta=None):
    """Store one notification and push it to every open /ws client. Returns the row, or None if empty/failed."""
    try:
        title = str(title or "").strip()[:300]
        if not title:
            return None
        kind = str(kind or "").strip().lower()
        if kind not in KINDS:
            kind = "alert"
        f = _db()
        with f.lock, f.db:
            cur = f.db.execute("INSERT INTO notifications(ts, kind, title, body, meta) VALUES(?,?,?,?,?)",
                               (time.time(), kind, title, str(body or "")[:2000],
                                json.dumps(meta or {}, default=str)[:2000]))
            row = _row(f.db.execute(f"SELECT {_COLS} FROM notifications WHERE id=?", (cur.lastrowid,)).fetchone())
            f.db.execute("DELETE FROM notifications WHERE id <= ?", (max(0, int(cur.lastrowid or 0) - KEEP),))
        try:
            from ishti import events
            events.publish("notification", row)
        except Exception as exc:
            swallowed("ishti/notifications.py:add.publish", exc)
        return row
    except Exception as exc:
        swallowed("ishti/notifications.py:add", exc)
        return None


def get(nid):
    """One notification by id, or None."""
    try:
        f = _db()
        with f.lock:
            r = f.db.execute(f"SELECT {_COLS} FROM notifications WHERE id=?", (int(nid),)).fetchone()
        return _row(r) if r else None
    except Exception as exc:
        swallowed("ishti/notifications.py:get", exc)
        return None


def recent(limit=50):
    """Newest first; at most 500 rows."""
    try:
        f = _db()
        limit = max(1, min(int(limit or 50), 500))
        with f.lock:
            rows = f.db.execute(f"SELECT {_COLS} FROM notifications ORDER BY ts DESC, id DESC LIMIT ?",
                                (limit,)).fetchall()
        return [_row(r) for r in rows]
    except Exception as exc:
        swallowed("ishti/notifications.py:recent", exc)
        return []


def unread():
    try:
        f = _db()
        with f.lock:
            return int(f.db.execute("SELECT COUNT(*) FROM notifications WHERE read=0").fetchone()[0])
    except Exception as exc:
        swallowed("ishti/notifications.py:unread", exc)
        return 0


def mark_read(nid=None):
    """Mark one notification read (by id) or every one when id is None; returns the remaining unread count."""
    try:
        f = _db()
        with f.lock, f.db:
            if nid in (None, "", 0):
                f.db.execute("UPDATE notifications SET read=1 WHERE read=0")
            else:
                f.db.execute("UPDATE notifications SET read=1 WHERE id=?", (int(nid),))
    except Exception as exc:
        swallowed("ishti/notifications.py:mark_read", exc)
    return unread()


if __name__ == "__main__":
    import os
    import tempfile

    import ishti.facts as F
    F._facts = F.Facts(os.path.join(tempfile.mkdtemp(), "t.db"))
    a = add("reminder", "Reminder: call Ali", body="14:20")
    b = add("download", "Model downloaded", body="ishti-qwen2.5:14b")
    assert a and b and a["read"] is False and b["id"] > a["id"]
    rows = recent()
    assert [r["id"] for r in rows] == [b["id"], a["id"]], rows                    # newest first
    assert unread() == 2
    assert get(a["id"])["title"] == "Reminder: call Ali"
    assert mark_read(a["id"]) == 1
    assert unread() == 1 and recent()[1]["read"] is True                          # the older row is the read one
    assert mark_read() == 0 and all(r["read"] for r in recent())                  # mark all
    assert add("nonsense", "x")["kind"] == "alert"                                # unknown kind falls back
    assert add("alert", "   ") is None                                            # empty title ignored
    assert add("error", "Request failed", meta={"where": "chat"})["meta"]["where"] == "chat"
    assert recent(1)[0]["title"] == "Request failed"                              # limit works
    print("[self-check] notifications OK - SQLite rows, newest first, unread count, mark one/all, kind fallback")
