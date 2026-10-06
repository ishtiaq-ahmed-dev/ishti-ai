"""WP7 in-process agent bus + persisted blackboard (extends ishti/agents.py C3/C4).

Layers (per the WP7 spec):
  - tool layer        -> MCP (ishti/mcp_client.py exists; unchanged)
  - in-process agents -> THIS typed bus: publish/subscribe by topic, validated envelopes,
                         loop prevention (max depth + TTL + dedupe), per-agent scopes, all traffic
                         mirrored into the durable event log (ishti/runs.agent_message/agent_status)
  - process-separate  -> A2A over HTTP on 127.0.0.1 with token auth (ishti/a2a.py, next step)

Injection defense: inter-agent payload content is data, never instructions - every envelope is
fenced through ishti.untrusted semantics by CONSUMERS, and the bus itself treats body as opaque.
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

MAX_DEPTH = 3          # an agent may trigger agents up to this many hops deep
TTL_S = 120.0          # a message older than this is dead on arrival
_body_dedupe: dict[str, float] = {}
_dedupe_lock = threading.Lock()
_lock = threading.RLock()
_subs: dict[str, list] = {}
BB_DB_OVERRIDE = None
_bb: sqlite3.Connection | None = None

TYPES = {"task", "result", "query", "input_required", "status"}


def bb_path() -> str:
    if BB_DB_OVERRIDE:
        return BB_DB_OVERRIDE
    try:
        from ishti import CONFIG
        return os.path.join(os.path.dirname(CONFIG["memory"]["db_path"]), "agent_blackboard.db")
    except Exception:
        return os.path.join("data", "agent_blackboard.db")


def bb() -> sqlite3.Connection:
    global _bb
    with _lock:
        if _bb is None:
            os.makedirs(os.path.dirname(bb_path()) or ".", exist_ok=True)
            _bb = sqlite3.connect(bb_path(), check_same_thread=False)
            _bb.row_factory = sqlite3.Row
            _bb.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS blackboard(
                key TEXT, writer TEXT, value TEXT, updated_at REAL,
                PRIMARY KEY(key, writer));
            """)
            _bb.commit()
        return _bb


def set_bb_path(path: str) -> None:
    global BB_DB_OVERRIDE, _bb
    with _lock:
        BB_DB_OVERRIDE = path
        _bb = None


# ---------------------------------------------------------------- blackboard (persisted)

def bb_put(key: str, value, writer: str) -> None:
    if not key or not writer:
        raise ValueError("key and writer are required")
    with _lock:
        c = bb()
        c.execute("INSERT INTO blackboard(key, writer, value, updated_at) VALUES(?,?,?,?) "
                  "ON CONFLICT(key, writer) DO UPDATE SET value = excluded.value, "
                  "updated_at = excluded.updated_at",
                  (str(key)[:200], str(writer)[:80],
                   json.dumps(value, ensure_ascii=False, default=str)[:20000], time.time()))
        c.commit()


def bb_get(key: str, default=None):
    with _lock:
        rows = bb().execute("SELECT writer, value, updated_at FROM blackboard WHERE key = ? "
                            "ORDER BY updated_at DESC", (str(key)[:200],)).fetchall()
    if not rows:
        return default
    return [{"writer": r["writer"], "value": json.loads(r["value"]), "updated_at": r["updated_at"]}
            for r in rows]


def bb_forget_everything() -> int:
    """Owner-invoked wipe of the shared blackboard (never user/CRM data - those are elsewhere)."""
    with _lock:
        n = bb().execute("SELECT COUNT(*) AS n FROM blackboard").fetchone()["n"]
        bb().execute("DELETE FROM blackboard")
        bb().commit()
    return int(n)


# ---------------------------------------------------------------- typed bus

def _allow_scope(sender: str, topic: str, scopes: dict) -> bool:
    """scopes: {agent_id: [allowed topic prefixes]}. Absent agent = unrestricted (registry fills
    scopes from the skill tiers: T0-only agents cannot publish outside their topics)."""
    allowed = (scopes or {}).get(sender)
    return True if allowed is None else any(topic.startswith(p) for p in allowed)


def publish(topic: str, body: dict, *, sender: str, type_: str = "task",
            depth: int = 0, run_id: str = "", scopes: dict | None = None) -> dict:
    """Typed envelope: {id, ts, topic, type, sender, depth, run_id, body}. body is OPAQUE DATA.
    Loop prevention: depth < MAX_DEPTH, fresh (TTL at consume), body-hash dedupe window."""
    if type_ not in TYPES:
        raise ValueError(f"type must be one of {sorted(TYPES)}")
    if depth >= MAX_DEPTH:
        return {"ok": False, "reason": "max_depth_reached"}
    body_key = f"{sender}->{topic}:{json.dumps(body, sort_keys=True, ensure_ascii=False)[:4000]}"
    now = time.time()
    with _dedupe_lock:
        if body_key in _body_dedupe and now - _body_dedupe[body_key] < TTL_S:
            return {"ok": False, "reason": "duplicate_suppressed"}
        _body_dedupe[body_key] = now
    if scopes is not None and not _allow_scope(sender, topic, scopes):
        from ishti import audit
        audit.record("agent bus publish refused", f"scope: {sender} -> {topic}", skill="agent_bus")
        return {"ok": False, "reason": "scope_refused"}
    env = {"id": uuid.uuid4().hex[:16], "ts": round(now, 3), "topic": str(topic)[:200],
           "type": type_, "sender": str(sender)[:80], "depth": int(depth),
           "run_id": str(run_id)[:40], "body": body or {}}
    with _lock:
        subs = list(_subs.get(env["topic"], [])) + list(_subs.get("*", []))
    for fn in subs:
        try:
            fn(env)
        except Exception as exc:
            swallowed("ishti/agent_bus.py:publish", exc)
    try:
        from ishti import runs
        runs.agent_message(env["sender"], "bus", f"[{env['type']}] {env['topic']}: "
                                                  f"{json.dumps(env['body'], ensure_ascii=False)[:300]}")
    except Exception as exc:
        swallowed("ishti/agent_bus.py:publish->runs", exc)
    return {"ok": True, "id": env["id"]}


def subscribe(topic: str, fn) -> None:
    with _lock:
        _subs.setdefault(str(topic)[:200], []).append(fn)


def unsubscribe(topic: str, fn) -> None:
    with _lock:
        fns = _subs.get(str(topic)[:200], [])
        if fn in fns:
            fns.remove(fn)


def deliver(env: dict) -> dict:
    """Consume-side guard: TTL expiry + depth re-check. Returns {delivered, reason?}."""
    if time.time() - float(env.get("ts") or 0) > TTL_S:
        return {"delivered": False, "reason": "expired"}
    if int(env.get("depth") or 0) >= MAX_DEPTH:
        return {"delivered": False, "reason": "max_depth_reached"}
    return {"delivered": True}


def request_input(agent_id: str, question: str, run_id: str = "") -> dict:
    """INPUT_REQUIRED: surfaced to the owner via the durable event log; the run pauses."""
    from ishti import runs
    runs.agent_status(agent_id, "input_required", str(question)[:400])
    return runs.publish("input_required", {"agent_id": agent_id, "question": str(question)[:500],
                                           "run_id": run_id})


if __name__ == "__main__":
    import tempfile
    set_bb_path(os.path.join(tempfile.mkdtemp(), "bb.db"))
    bb_put("research:urls", ["https://a"], writer="researcher")
    rows = bb_get("research:urls")
    assert rows[0]["writer"] == "researcher" and rows[0]["value"] == ["https://a"]
    assert bb_get("missing", []) == []
    got = []
    subscribe("research.done", got.append)
    out = publish("research.done", {"urls": 2}, sender="researcher", type_="result")
    assert out["ok"] is True and len(got) == 1 and got[0]["body"] == {"urls": 2}
    dup = publish("research.done", {"urls": 2}, sender="researcher", type_="result")
    assert dup["ok"] is False and dup["reason"] == "duplicate_suppressed"
    deep = publish("t", {}, sender="a", depth=MAX_DEPTH)
    assert deep["ok"] is False and deep["reason"] == "max_depth_reached"
    refused = publish("secret.topic", {}, sender="locked", type_="status",
                      scopes={"locked": ["files."]})
    assert refused["ok"] is False and refused["reason"] == "scope_refused"
    assert deliver({"ts": time.time() - TTL_S - 1, "depth": 0})["reason"] == "expired"
    assert deliver({"ts": time.time(), "depth": 0})["delivered"] is True
    assert bb_forget_everything() == 1
    print("[self-check] agent_bus OK - typed envelopes, dedupe, depth/TTL loop prevention, "
          "scopes, persisted blackboard, durable event mirror")
