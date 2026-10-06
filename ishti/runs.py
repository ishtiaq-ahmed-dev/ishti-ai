"""Durable run engine (docs/API_CONTRACT.md §14, WP1): chats, messages, runs, feedback, the global
event log, and learning-mode/proposal registries.

One owner of run state. The user message is persisted BEFORE generation; runs execute server-side on
worker threads, independent of any UI connection; every event gets a monotonically increasing `seq`
in an append-only SQLite log; /ws/events?since_seq=N replays from N (ishti/lifecycle_api.py).
Idempotent send on (chat_id, client_msg_id). Restart recovery marks interrupted runs failed-visibly.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import uuid

try:
    from ishti.core.logutil import swallowed
except Exception:  # pragma: no cover
    def swallowed(*_a, **_k):
        return None

from ishti import profiles as _profiles

DB_PATH_OVERRIDE = None
_lock = threading.RLock()
_conn = None
_LISTENER = None  # set by lifecycle_api: fn(event_dict) -> None (fans out to live WS subscribers)
GEN_LABELS = {"research": 0, "tool": 0, "verify": 0}

STATES = ("queued", "running", "done", "failed", "cancelled")


def db_path() -> str:
    if DB_PATH_OVERRIDE:
        return DB_PATH_OVERRIDE
    try:
        from ishti import CONFIG
        return os.path.join(os.path.dirname(CONFIG["memory"]["db_path"]), "runs.db")
    except Exception:
        return os.path.join("data", "runs.db")


def conn() -> sqlite3.Connection:
    global _conn
    with _lock:
        if _conn is None:
            os.makedirs(os.path.dirname(db_path()) or ".", exist_ok=True)
            _conn = sqlite3.connect(db_path(), check_same_thread=False)
            _conn.row_factory = sqlite3.Row
            _conn.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS chats(
                chat_id TEXT PRIMARY KEY, title TEXT, profile TEXT NOT NULL DEFAULT 'personal',
                created_at REAL, updated_at REAL, deleted_at REAL, delete_token TEXT);
            CREATE TABLE IF NOT EXISTS messages(
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                message_id TEXT UNIQUE, chat_id TEXT, role TEXT, text TEXT,
                run_id TEXT, client_msg_id TEXT, channel TEXT DEFAULT 'text',
                profile TEXT, ts REAL,
                UNIQUE(chat_id, client_msg_id));
            CREATE TABLE IF NOT EXISTS runs(
                run_id TEXT PRIMARY KEY, chat_id TEXT, message_id TEXT, state TEXT,
                text TEXT DEFAULT '', profile TEXT, steps TEXT DEFAULT '[]',
                error TEXT, started_at REAL, ended_at REAL);
            CREATE TABLE IF NOT EXISTS event_log(
                seq INTEGER PRIMARY KEY AUTOINCREMENT, type TEXT, data TEXT, ts REAL);
            CREATE TABLE IF NOT EXISTS feedback(
                feedback_id TEXT PRIMARY KEY, message_id TEXT, run_id TEXT,
                rating INTEGER, reasons TEXT DEFAULT '[]', comment TEXT, correction TEXT,
                ts REAL, UNIQUE(message_id, run_id, rating));
            CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
            """)
            # Forward-only migration for runs.db files created before reversible chat deletion.
            chat_cols = {r["name"] for r in _conn.execute("PRAGMA table_info(chats)").fetchall()}
            if "deleted_at" not in chat_cols:
                _conn.execute("ALTER TABLE chats ADD COLUMN deleted_at REAL")
            if "delete_token" not in chat_cols:
                _conn.execute("ALTER TABLE chats ADD COLUMN delete_token TEXT")
            _conn.commit()
        return _conn


def set_db_path(path: str) -> None:
    global DB_PATH_OVERRIDE, _conn
    with _lock:
        DB_PATH_OVERRIDE = path
        _conn = None


def set_listener(fn) -> None:
    """lifecycle_api registers the live fan-out; publish() writes first, then pushes."""
    global _LISTENER
    _LISTENER = fn


# ---------------------------------------------------------------- event log (global seq)

def publish(type_: str, data: dict | None = None) -> dict:
    """Append to the durable log, then push to live subscribers. seq is global + monotonic."""
    env = {"type": str(type_), "data": data or {}, "ts": round(time.time(), 3)}
    with _lock:
        c = conn()
        cur = c.execute("INSERT INTO event_log(type, data, ts) VALUES(?,?,?)",
                        (env["type"], json.dumps(env["data"], ensure_ascii=False), env["ts"]))
        c.commit()
        env["seq"] = int(cur.lastrowid)
    fn = _LISTENER
    if fn is not None:
        try:
            fn(env)
        except Exception as exc:
            swallowed("ishti/runs.py:publish", exc)
    return env


def events_since(since_seq: int = 0, limit: int = 5000) -> list[dict]:
    with _lock:
        rows = conn().execute(
            "SELECT seq, type, data, ts FROM event_log WHERE seq > ? ORDER BY seq LIMIT ?",
            (int(max(0, since_seq)), int(max(1, limit)))).fetchall()
    return [{"seq": r["seq"], "type": r["type"], "data": json.loads(r["data"] or "{}"), "ts": r["ts"]}
            for r in rows]


def events_since_profile(profile: str | None = None, since_seq: int = 0,
                         limit: int = 5000) -> list[dict]:
    """Return global events plus chat/run events belonging to exactly one profile.

    `last_seq` remains global, so a client can advance past hidden rows without learning their
    contents and will not replay them after a profile switch.
    """
    wanted = _profiles.valid(profile or _profiles.active())
    out: list[dict] = []
    for event in events_since(since_seq, limit):
        data = event.get("data") or {}
        event_profile = data.get("profile")
        if not event_profile and isinstance(data.get("message"), dict):
            event_profile = data["message"].get("profile")
        chat_id = data.get("chat_id")
        if not event_profile and chat_id:
            chat = get_chat(str(chat_id), include_deleted=True)
            event_profile = (chat or {}).get("profile")
        run_id = data.get("run_id")
        if not event_profile and run_id:
            run = get_run(str(run_id))
            event_profile = (run or {}).get("profile")
        if not event_profile or event_profile == wanted:
            out.append(event)
    return out


def last_seq() -> int:
    with _lock:
        row = conn().execute("SELECT MAX(seq) AS m FROM event_log").fetchone()
    return int(row["m"] or 0)


# ---------------------------------------------------------------- chats & messages

def new_chat(profile: str | None = None, title: str = "New chat") -> dict:
    profile = _profiles.valid(profile or _profiles.active())
    now = time.time()
    chat_id = uuid.uuid4().hex[:12]
    with _lock:
        c = conn()
        c.execute("INSERT INTO chats(chat_id, title, profile, created_at, updated_at) VALUES(?,?,?,?,?)",
                  (chat_id, title, profile, now, now))
        c.commit()
    return {"chat_id": chat_id, "profile": profile}


def list_chats(limit: int = 200, profile: str | None = None) -> list[dict]:
    """List live chats in one profile namespace; trashed chats never appear."""
    profile = _profiles.valid(profile or _profiles.active())
    with _lock:
        rows = conn().execute(
            "SELECT chat_id, title, profile, created_at, updated_at FROM chats "
            "WHERE profile = ? AND deleted_at IS NULL ORDER BY updated_at DESC LIMIT ?",
            (profile, int(max(1, limit)))).fetchall()
    return [dict(r) for r in rows]


def get_chat(chat_id: str, *, include_deleted: bool = False) -> dict | None:
    """Internal id lookup. HTTP handlers must additionally enforce the active profile."""
    deleted = "" if include_deleted else " AND deleted_at IS NULL"
    with _lock:
        row = conn().execute("SELECT chat_id, title, profile, created_at, updated_at FROM chats "
                             f"WHERE chat_id = ?{deleted}", (str(chat_id),)).fetchone()
    return dict(row) if row else None


def get_chat_for_profile(chat_id: str, profile: str | None = None) -> dict | None:
    """Owner-facing lookup that makes a cross-profile id indistinguishable from a missing id."""
    wanted = _profiles.valid(profile or _profiles.active())
    chat = get_chat(chat_id)
    return chat if chat and chat["profile"] == wanted else None


def set_chat_title(chat_id: str, title: str, profile: str | None = None) -> dict:
    """Server-side rename. The UI still allows free renames locally (title overrides), but the
    durable title serves the auto-name: the first user message names the chat (no more rows of
    'New chat'). The literal placeholder is refused so a stale client can never erase a real name
    after an auto-title (that was the 'too many New chat rows' regression)."""
    clean = str(title or "").strip()[:120]
    if not clean:
        raise ValueError("title must not be empty")
    if clean.lower() == "new chat":
        raise ValueError("'New chat' is the auto-name placeholder and cannot be stored")
    with _lock:
        c = conn()
        wanted = _profiles.valid(profile or _profiles.active())
        row = c.execute("SELECT chat_id FROM chats WHERE chat_id = ? AND profile = ? "
                        "AND deleted_at IS NULL", (str(chat_id), wanted)).fetchone()
        if not row:
            raise ValueError("unknown chat_id")
        c.execute("UPDATE chats SET title = ? WHERE chat_id = ?", (clean, str(chat_id)))
        c.commit()
    publish("chat_renamed", {"chat_id": str(chat_id), "title": clean, "profile": wanted})
    return get_chat_for_profile(chat_id, wanted)


def trash_chat(chat_id: str, profile: str | None = None) -> dict:
    """Soft-delete one chat and return a profile-bound, single-use undo token."""
    wanted = _profiles.valid(profile or _profiles.active())
    token = uuid.uuid4().hex
    now = time.time()
    with _lock:
        c = conn()
        row = c.execute("SELECT chat_id FROM chats WHERE chat_id = ? AND profile = ? "
                        "AND deleted_at IS NULL", (str(chat_id), wanted)).fetchone()
        if not row:
            raise ValueError("unknown chat_id")
        c.execute("UPDATE chats SET deleted_at = ?, delete_token = ? WHERE chat_id = ?",
                  (now, token, str(chat_id)))
        c.commit()
    publish("chat_deleted", {"chat_id": str(chat_id), "profile": wanted})
    return {"deleted": True, "chat_id": str(chat_id), "undo_token": token,
            "undo_seconds": 8}


def restore_chat(chat_id: str, undo_token: str, profile: str | None = None,
                 max_age_s: float = 8.0) -> dict:
    """Restore a trashed chat during its UI undo window; tokens cannot cross profiles/chats."""
    wanted = _profiles.valid(profile or _profiles.active())
    token = str(undo_token or "").strip()
    now = time.time()
    with _lock:
        c = conn()
        row = c.execute("SELECT deleted_at FROM chats WHERE chat_id = ? AND profile = ? "
                        "AND delete_token = ? AND deleted_at IS NOT NULL",
                        (str(chat_id), wanted, token)).fetchone()
        if not row:
            raise ValueError("unknown or already-used undo token")
        if now - float(row["deleted_at"] or 0) > max_age_s:
            c.execute("UPDATE chats SET delete_token = NULL WHERE chat_id = ?", (str(chat_id),))
            c.commit()
            raise ValueError("undo window expired")
        c.execute("UPDATE chats SET deleted_at = NULL, delete_token = NULL, updated_at = ? "
                  "WHERE chat_id = ?", (now, str(chat_id)))
        c.commit()
    publish("chat_restored", {"chat_id": str(chat_id), "profile": wanted})
    return {"restored": True, "chat": get_chat_for_profile(chat_id, wanted)}


def chat_profile(chat_id: str) -> str:
    # A deleted chat may still have a worker finishing. Its immutable profile remains authoritative.
    chat = get_chat(chat_id, include_deleted=True)
    return _profiles.valid(chat["profile"]) if chat else _profiles.active()


def messages_after(chat_id: str, after_seq: int = 0, limit: int = 1000) -> list[dict]:
    with _lock:
        rows = conn().execute(
            "SELECT seq, message_id, role, text, run_id, client_msg_id, channel, profile, ts "
            "FROM messages WHERE chat_id = ? AND seq > ? ORDER BY seq LIMIT ?",
            (str(chat_id), int(max(0, after_seq)), int(max(1, limit)))).fetchall()
    return [dict(r) for r in rows]


def chat_title(text: str) -> str:
    """First line of the first user message, trimmed - the sidebar label (mirrored in chat-store.ts)."""
    t = " ".join(str(text).split())
    return (t[:48].rstrip() + "…") if len(t) > 48 else (t or "New chat")


def add_user_message(chat_id: str, text: str, client_msg_id: str | None = None,
                     attachments: list | None = None, channel: str = "text") -> dict:
    """Persist the user message BEFORE any generation. Idempotent on (chat_id, client_msg_id)."""
    text = str(text or "").strip()
    if not text:
        raise ValueError("empty text")
    chat = get_chat(chat_id)
    if not chat:
        raise ValueError("unknown chat_id")
    profile = chat["profile"]
    now = time.time()
    with _lock:
        c = conn()
        try:
            cur = c.execute(
                "INSERT INTO messages(message_id, chat_id, role, text, run_id, client_msg_id, "
                "channel, profile, ts) VALUES(?,?,?,?,?,?,?,?,?)",
                (uuid.uuid4().hex[:16], chat_id, "user", text, "", client_msg_id or "", channel,
                 profile, now))
        except sqlite3.IntegrityError:
            row = c.execute(
                "SELECT seq, message_id, role, text, run_id, client_msg_id, channel, profile, ts "
                "FROM messages WHERE chat_id = ? AND client_msg_id = ?",
                (chat_id, client_msg_id or "")).fetchone()
            c.commit()
            return {"message": dict(row), "duplicate": True}
        c.execute("UPDATE chats SET updated_at = ?, title = CASE WHEN title = 'New chat' THEN ? ELSE title END "
                  "WHERE chat_id = ?", (now, chat_title(text), chat_id))
        c.commit()
        msg = dict(c.execute("SELECT seq, message_id, role, text, run_id, client_msg_id, channel, "
                             "profile, ts FROM messages WHERE seq = ?",
                             (int(cur.lastrowid),)).fetchone())
    publish("message_created", {"chat_id": chat_id, "message": msg})
    return {"message": msg, "duplicate": False}


def add_assistant_message(chat_id: str, text: str, run_id: str = "",
                          channel: str = "text", profile: str | None = None) -> dict:
    now = time.time()
    with _lock:
        c = conn()
        cur = c.execute(
            "INSERT INTO messages(message_id, chat_id, role, text, run_id, client_msg_id, "
            "channel, profile, ts) VALUES(?,?,?,?,?,?,?,?,?)",
            # client_msg_id must be NULL, not '': UNIQUE(chat_id, client_msg_id) would reject the
            # second assistant message of a chat and silently drop run_finished/message_created.
            (uuid.uuid4().hex[:16], chat_id, "assistant", str(text or ""), run_id, None, channel,
             _profiles.valid(profile or chat_profile(chat_id)), now))
        c.execute("UPDATE chats SET updated_at = ?, title = CASE WHEN title = 'New chat' THEN ? ELSE title END "
                  "WHERE chat_id = ?", (now, chat_title(text), chat_id))
        c.commit()
        msg = dict(c.execute("SELECT seq, message_id, role, text, run_id, client_msg_id, channel, "
                             "profile, ts FROM messages WHERE seq = ?",
                             (int(cur.lastrowid),)).fetchone())
    publish("message_created", {"chat_id": chat_id, "message": msg})
    return msg


# ---------------------------------------------------------------- runs

def start_run(chat_id: str, message_id: str, profile: str | None = None) -> dict:
    run_id = uuid.uuid4().hex[:16]
    prof = _profiles.valid(profile or chat_profile(chat_id))
    now = time.time()
    with _lock:
        c = conn()
        c.execute("INSERT INTO runs(run_id, chat_id, message_id, state, text, profile, steps, "
                  "error, started_at, ended_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                  (run_id, chat_id, message_id, "queued", "", prof, "[]", None, now, None))
        c.execute("UPDATE messages SET run_id = ? WHERE message_id = ?", (run_id, message_id))
        c.commit()
    publish("run_started", {"run_id": run_id, "chat_id": chat_id, "message_id": message_id,
                            "profile": prof})
    return {"run_id": run_id, "chat_id": chat_id, "message_id": message_id, "state": "queued",
            "profile": prof}


def get_run(run_id: str) -> dict | None:
    with _lock:
        row = conn().execute("SELECT run_id, chat_id, message_id, state, text, profile, steps, "
                             "error, started_at, ended_at FROM runs WHERE run_id = ?",
                             (str(run_id),)).fetchone()
    if not row:
        return None
    out = dict(row)
    out["steps"] = json.loads(out.get("steps") or "[]")
    return out


def get_run_for_profile(run_id: str, profile: str | None = None) -> dict | None:
    wanted = _profiles.valid(profile or _profiles.active())
    run = get_run(run_id)
    return run if run and run["profile"] == wanted else None


def list_runs(chat_id: str | None = None, limit: int = 100,
              profile: str | None = None) -> list[dict]:
    sql = ("SELECT run_id, chat_id, message_id, state, text, profile, steps, error, "
           "started_at, ended_at FROM runs")
    args: list = []
    where: list[str] = []
    if profile is not None:
        where.append("profile = ?")
        args.append(_profiles.valid(profile))
    if chat_id:
        where.append("chat_id = ?")
        args.append(str(chat_id))
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY started_at DESC LIMIT ?"
    args.append(int(max(1, limit)))
    with _lock:
        rows = conn().execute(sql, args).fetchall()
    out = [dict(r) for r in rows]
    for run in out:
        run["steps"] = json.loads(run.get("steps") or "[]")
    return out


def run_state(run_id: str) -> str | None:
    run = get_run(run_id)
    return run["state"] if run else None


def _update_run(run_id: str, **cols) -> None:
    cols = {k: v for k, v in cols.items() if k in
            ("state", "text", "steps", "error", "ended_at")}
    if not cols:
        return
    with _lock:
        c = conn()
        sets = ", ".join(f"{k} = ?" for k in cols)
        c.execute(f"UPDATE runs SET {sets} WHERE run_id = ?", (*cols.values(), run_id))
        c.commit()


def run_delta(run_id: str, text: str) -> None:
    """Streamed partial answer; the full text lands on finish."""
    run = get_run(run_id)
    if not run or run["state"] in ("done", "failed", "cancelled"):
        return
    publish("run_delta", {"run_id": run_id, "text": str(text or "")})


def run_step(run_id: str, kind: str, label: str, n: int | None = None,
             sources: list | None = None) -> dict:
    """kind in research|tool|verify. Persisted on the run AND emitted, so replay rebuilds steps."""
    if kind not in GEN_LABELS:
        kind = "tool"
    run = get_run(run_id)
    if not run:
        return {}
    steps = list(run.get("steps") or [])
    step = {"kind": kind, "label": str(label or "")[:300], "n": int(n) if n is not None
            else GEN_LABELS[kind] + sum(1 for s in steps if s.get("kind") == kind) + 1,
            "sources": list(sources or [])[:12], "ts": round(time.time(), 3)}
    steps.append(step)
    _update_run(run_id, steps=json.dumps(steps, ensure_ascii=False))
    publish("run_step", {"run_id": run_id, **step})
    return step


def finish_run(run_id: str, text: str, *, assistant_message: bool = True) -> dict:
    """Set the final answer, persist the assistant message, emit run_finished. Exactly once."""
    run = get_run(run_id)
    if not run:
        raise ValueError("unknown run_id")
    if run["state"] in ("done", "failed", "cancelled"):
        return get_run(run_id)  # idempotent finish
    text = str(text or "")
    _update_run(run_id, state="done", text=text, ended_at=time.time())
    msg = None
    if assistant_message:
        msg = add_assistant_message(run["chat_id"], text, run_id=run_id, profile=run["profile"])
    publish("run_finished", {"run_id": run_id, "chat_id": run["chat_id"], "text": text,
                             "message_id": (msg or {}).get("message_id", "")})
    return get_run(run_id)


def fail_run(run_id: str, error: str) -> dict:
    run = get_run(run_id)
    if not run or run["state"] in ("done", "failed", "cancelled"):
        return get_run(run_id) if run else {}
    _update_run(run_id, state="failed", error=str(error or "")[:500], ended_at=time.time())
    publish("run_failed", {"run_id": run_id, "chat_id": run["chat_id"],
                           "error": str(error or "")[:500]})
    return get_run(run_id)


def cancel_run(run_id: str) -> dict:
    run = get_run(run_id)
    if not run:
        return {"cancelled": False}
    if run["state"] in ("done", "failed", "cancelled"):
        return {"cancelled": False, "state": run["state"]}
    # The killswitch is generation-based: bumping it makes every in-flight request raise Cancelled.
    # A run cancel is one request, not a global stop, so we only flip the generation when the owner
    # kills everything (kill()). For a single run we mark cancelled here and the executor's
    # terminal-state guard stops late writes.
    _update_run(run_id, state="cancelled", ended_at=time.time())
    publish("run_finished", {"run_id": run_id, "chat_id": run["chat_id"],
                             "text": "(cancelled)", "cancelled": True})
    try:
        from ishti import audit
        audit.record("run cancelled", f"run={run_id} chat={run['chat_id']}", skill="runs")
    except Exception as exc:
        swallowed("ishti/runs.py:cancel_run", exc)
    return {"cancelled": True, "run_id": run_id}


def recover_interrupted() -> list[dict]:
    """Restart recovery: runs left queued/running by a crash are marked failed with a visible,
    honest error (WP1). Never silently re-execute stale work."""
    with _lock:
        rows = conn().execute("SELECT run_id, chat_id FROM runs WHERE state IN ('queued','running')")
        stale = [dict(r) for r in rows.fetchall()]
    for r in stale:
        _update_run(r["run_id"], state="failed",
                    error="interrupted by backend restart; ask again to retry",
                    ended_at=time.time())
        publish("run_failed", {"run_id": r["run_id"], "chat_id": r["chat_id"],
                               "error": "interrupted by backend restart; ask again to retry"})
    return stale


# ---------------------------------------------------------------- feedback (WP6 inlet; full wiring later)

def add_feedback(message_id: str, run_id: str, rating: int, reasons: list | None = None,
                 comment: str = "", correction: str = "") -> dict:
    if rating not in (-1, 0, 1):
        raise ValueError("rating must be -1|0|1")
    with _lock:
        c = conn()
        try:
            fid = uuid.uuid4().hex[:16]
            c.execute("INSERT INTO feedback(feedback_id, message_id, run_id, rating, reasons, "
                      "comment, correction, ts) VALUES(?,?,?,?,?,?,?,?)",
                      (fid, str(message_id or ""), str(run_id or ""), int(rating),
                       json.dumps([str(r)[:120] for r in (reasons or [])], ensure_ascii=False),
                       str(comment or "")[:2000], str(correction or "")[:4000], time.time()))
            c.commit()
        except sqlite3.IntegrityError:
            row = c.execute("SELECT feedback_id FROM feedback WHERE message_id = ? AND run_id = ? "
                            "AND rating = ?", (str(message_id or ""), str(run_id or ""),
                                               int(rating))).fetchone()
            return {"feedback_id": row["feedback_id"], "state": "duplicate"}
    try:
        from ishti import audit
        audit.record("feedback recorded", f"rating={rating} msg={message_id} run={run_id}",
                     skill="feedback")
    except Exception as exc:
        swallowed("ishti/runs.py:add_feedback", exc)
    publish("learning_update", {"kind": "feedback", "detail": {"message_id": message_id,
                                                               "rating": rating}})
    return {"feedback_id": fid, "state": "stored"}


def feedback_stats() -> dict:
    with _lock:
        rows = conn().execute("SELECT rating, COUNT(*) AS n FROM feedback GROUP BY rating").fetchall()
    by = {int(r["rating"]): int(r["n"]) for r in rows}
    total = sum(by.values())
    return {"total": total, "pos": by.get(1, 0), "neg": by.get(-1, 0), "neutral": by.get(0, 0),
            "by_reason": {}}


# ---------------------------------------------------------------- learning mode + proposals (WP6)

MODES = ("off", "propose", "auto_t0")


def learning_mode() -> str:
    with _lock:
        row = conn().execute("SELECT value FROM meta WHERE key = 'learning_mode'").fetchone()
    return row["value"] if row else "propose"


def set_learning_mode(mode: str) -> str:
    if mode not in MODES:
        raise ValueError("mode must be off|propose|auto_t0")
    with _lock:
        c = conn()
        c.execute("INSERT INTO meta(key, value) VALUES('learning_mode', ?) "
                  "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (mode,))
        c.commit()
    publish("learning_update", {"kind": "mode", "detail": {"mode": mode}})
    try:
        from ishti import audit
        audit.record("learning mode set", f"mode={mode}", skill="learning")
    except Exception as exc:
        swallowed("ishti/runs.py:set_learning_mode", exc)
    return mode


def add_proposal(title: str, skill: str = "", origin: str = "trajectory") -> dict:
    pid = uuid.uuid4().hex[:12]
    with _lock:
        c = conn()
        c.execute("INSERT INTO meta(key, value) VALUES(?,?)",
                  (f"proposal:{pid}", json.dumps({"id": pid, "title": str(title)[:200],
                                                  "skill": str(skill)[:60], "status": "open",
                                                  "origin": str(origin)[:60], "ts": time.time()},
                                                 ensure_ascii=False)))
        c.commit()
    publish("learning_update", {"kind": "proposal", "detail": {"id": pid}})
    return {"id": pid, "title": title, "skill": skill, "status": "open", "origin": origin}


def list_proposals(status: str | None = None) -> list[dict]:
    with _lock:
        rows = conn().execute("SELECT key, value FROM meta WHERE key LIKE 'proposal:%'").fetchall()
    out = []
    for r in rows:
        try:
            p = json.loads(r["value"])
        except (ValueError, TypeError):
            continue
        if status is None or p.get("status") == status:
            out.append(p)
    out.sort(key=lambda p: p.get("ts", 0), reverse=True)
    return out


def resolve_proposal(proposal_id: str, approve: bool) -> dict:
    """approve=True is the owner's explicit T2 yes for enabling a skill with side effects;
    auto_t0 may only auto-enable read-only (T0) skills and never touches this path."""
    with _lock:
        c = conn()
        row = c.execute("SELECT value FROM meta WHERE key = ?",
                        (f"proposal:{proposal_id}",)).fetchone()
        if not row:
            raise ValueError("unknown proposal")
        p = json.loads(row["value"])
        p["status"] = "approved" if approve else "rejected"
        c.execute("UPDATE meta SET value = ? WHERE key = ?",
                  (json.dumps(p, ensure_ascii=False), f"proposal:{proposal_id}"))
        c.commit()
    try:
        from ishti import audit
        audit.record("proposal " + p["status"], f"id={proposal_id}", skill="learning")
    except Exception as exc:
        swallowed("ishti/runs.py:resolve_proposal", exc)
    publish("learning_update", {"kind": "proposal", "detail": p})
    return p


# ---------------------------------------------------------------- agent bus plumbing (WP7)

AGENT_MESSAGES: dict[str, list] = {}
AGENT_STATE: dict[str, dict] = {}


def agent_message(agent_id: str, from_: str, text: str) -> None:
    AGENT_MESSAGES.setdefault(str(agent_id), []).append(
        {"ts": round(time.time(), 3), "from": str(from_), "text": str(text)[:4000]})
    publish("agent_message", {"agent_id": str(agent_id), "from": str(from_),
                              "text": str(text)[:4000]})


def agent_status(agent_id: str, state: str, detail: str = "") -> None:
    AGENT_STATE[str(agent_id)] = {"state": str(state), "detail": str(detail)[:500],
                                  "ts": round(time.time(), 3)}
    publish("agent_status", {"agent_id": str(agent_id), "state": str(state),
                             "detail": str(detail)[:500]})


if __name__ == "__main__":
    import tempfile
    set_db_path(os.path.join(tempfile.mkdtemp(), "runs.db"))
    _profiles.set_active("personal")
    ch = new_chat()
    m = add_user_message(ch["chat_id"], "hello", client_msg_id="c1")
    assert m["duplicate"] is False and m["message"]["role"] == "user"
    dup = add_user_message(ch["chat_id"], "hello", client_msg_id="c1")
    assert dup["duplicate"] is True and dup["message"]["message_id"] == m["message"]["message_id"]
    run = start_run(ch["chat_id"], m["message"]["message_id"])
    run_step(run["run_id"], "research", "searching", sources=["https://a.example"])
    run_delta(run["run_id"], "part")
    fin = finish_run(run["run_id"], "final answer")
    assert fin["state"] == "done" and fin["text"] == "final answer"
    assert finish_run(run["run_id"], "other")["text"] == "final answer"  # idempotent finish
    evs = events_since(0)
    types = [e["type"] for e in evs]
    assert types[0] == "message_created" and "run_started" in types and types[-1] == "run_finished"
    assert all(e["seq"] == i + 1 for i, e in enumerate(evs)), "seq must be gapless+monotonic"
    assert [e for e in events_since(evs[-2]["seq"])][0]["seq"] == evs[-1]["seq"]
    r = get_run(run["run_id"])
    assert r["steps"][0]["kind"] == "research" and r["steps"][0]["sources"]
    msgs = messages_after(ch["chat_id"], 0)
    assert [x["role"] for x in msgs] == ["user", "assistant"] and msgs[1]["run_id"] == run["run_id"]
    fb = add_feedback(msgs[1]["message_id"], run["run_id"], -1, reasons=["wrong"], comment="x")
    assert add_feedback(msgs[1]["message_id"], run["run_id"], -1)["state"] == "duplicate"
    assert feedback_stats()["neg"] == 1
    assert set_learning_mode("auto_t0") == "auto_t0" and learning_mode() == "auto_t0"
    try:
        set_learning_mode("auto_t9")
        raise SystemExit("bad mode accepted")
    except ValueError as _exc:
        print(f"[self-check] bad learning mode refused as expected ({_exc})")
    p = add_proposal("csv skill", skill="csv", origin="trajectory")
    got = resolve_proposal(p["id"], approve=False)
    assert got["status"] == "rejected" and list_proposals("rejected")[0]["id"] == p["id"]
    r2 = start_run(ch["chat_id"], msgs[0]["message_id"])
    got = cancel_run(r2["run_id"])
    assert got["cancelled"] is True and get_run(r2["run_id"])["state"] == "cancelled"
    assert cancel_run(r2["run_id"])["cancelled"] is False  # cancelling twice is a no-op
    agent_message("researcher", "orchestrator", "task")
    agent_status("researcher", "running")
    assert AGENT_MESSAGES["researcher"] and AGENT_STATE["researcher"]["state"] == "running"
    set_db_path(os.path.join(tempfile.mkdtemp(), "other.db"))
    r2 = start_run("nonexistent", "nonexistent")  # crash sim: a run left queued on a fresh db
    got = recover_interrupted()
    assert [r["run_id"] for r in got] == [r2["run_id"]], "restart recovery must see stale runs"
    assert get_run(r2["run_id"])["state"] == "failed"
    assert recover_interrupted() == []  # second boot: nothing left to recover
    print("[self-check] runs OK - durable chats/messages/runs, seq log, replay, idempotency, "
          "cancel, feedback, learning mode, proposals, agent bus hooks")
