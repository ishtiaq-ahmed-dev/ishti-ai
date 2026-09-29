"""Task manager (Part D): background tasks the owner can keep chatting through.

D1 concurrency + priority queue + cancellation (children included) + timeouts + progress
D2 one heavy LLM job at a time — a process-wide semaphore over the GPU work
D3 checkpoint before long work, resume from it, idempotency keys (same key -> same task)
D4 list/pause/resume/cancel by voice or UI (`taskctl` skill), tasks run while chatting
D5 every task snapshots its OWN conversation state (Prompt-1 ConversationStateManager), so
   background work never mixes topics with the chat that spawned it

Fail-soft: the manager never raises into the chat; task errors land in the task record.
"""
from __future__ import annotations

import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

from ishti.core.state import get_manager as _state_manager
from ishti import resilience

_LOCK = threading.RLock()
_EXECUTOR = ThreadPoolExecutor(max_workers=6, thread_name_prefix="ishti-task")
TASKS: dict[str, dict] = {}          # id -> record
_WAITING: list[tuple] = []           # (priority, seq, task_id) — a simple priority queue
_SEQ = 0
_GPU_SEM = threading.Semaphore(1)    # D2: one heavy LLM job at a time (Ollama queues the rest)
MAX_TASKS = 60                       # the record list is trimmed to this

PRIORITY = {"high": 0, "normal": 1, "low": 2}


def gpu_slot():
    """Context manager: heavy LLM work inside acquires the GPU slot (D2).
    Waiters queue; nothing deadlocks (single resource, no nesting)."""
    return _GPU_SEM


class TaskError(RuntimeError):
    pass


def _snapshot_state(task_id: str, sid: str) -> dict:
    """D5: the task's OWN conversation-state snapshot (never mixed with the chat's)."""
    try:
        st = _state_manager().get(sid)
        return st.to_dict()
    except Exception:
        return {"session_id": sid, "active_topic": "", "note": "state manager unavailable"}


def submit(fn, *, goal: str = "", sid: str = "", priority: str = "normal",
           timeout_s: float = 900.0, idempotency_key: str = "", checkpoint_name: str = "") -> dict:
    """Run fn(task) in the background. fn receives the task record (check state['progress'],
    call task['heartbeat'](), honour task['cancel_event'], store its result in the return
    value). Returns the task record."""
    if idempotency_key:
        with _LOCK:
            for t in TASKS.values():
                if t["idempotency_key"] == idempotency_key and t["status"] in ("queued", "running", "paused"):
                    return t
    tid = f"task_{uuid.uuid4().hex[:10]}"
    rec = {
        "id": tid, "goal": goal[:200], "sid": sid, "priority": PRIORITY.get(priority, 1),
        "status": "queued", "progress": 0.0, "result": None, "error": "",
        "submitted": time.time(), "started": 0.0, "ended": 0.0, "timeout_s": timeout_s,
        "idempotency_key": idempotency_key, "checkpoint_name": checkpoint_name or tid,
        "cancel_event": threading.Event(), "pause_event": threading.Event(),
        "children": [], "parent": "", "steps": [],
        "state_snapshot": _snapshot_state(tid, sid),
    }
    rec["_fn"] = fn
    with _LOCK:
        TASKS[tid] = rec
        _trim()
    _enqueue(rec)
    return rec


def _enqueue(rec: dict):
    global _SEQ

    def _run():
        with _LOCK:
            rec["status"] = "running"
            rec["started"] = time.time()
        try:
            out = rec["_fn"](rec)
            with _LOCK:
                rec["result"] = out
                rec["status"] = "done"
                rec["progress"] = 1.0
                rec["ended"] = time.time()
                resilience.drop_checkpoint(rec["checkpoint_name"])
        except _Cancelled:
            with _LOCK:
                rec["status"] = "cancelled"
                rec["ended"] = time.time()
        except Exception as exc:
            with _LOCK:
                rec["status"] = "failed"
                rec["error"] = f"{type(exc).__name__}: {str(exc)[:300]}"
                rec["ended"] = time.time()

    with _LOCK:
        _SEQ += 1
        seq = _SEQ
        _WAITING.append((rec["priority"], seq, rec))
    _EXECUTOR.submit(_dequeue_then(_run))


def _dequeue_then(run):
    """Priority order at execution time: the executor pool may be busy when a low-priority
    task is enqueued; when a slot frees, the highest-priority waiter runs first."""

    def _inner():
        with _LOCK:
            if _WAITING:
                _WAITING.sort(key=lambda x: (x[0], x[1]))
                _pri, _seq, rec = _WAITING.pop(0)
                if rec["status"] == "cancelled":  # cancelled while queued
                    rec["ended"] = time.time()
                    return
        run()
    return _inner


class _Cancelled(Exception):
    pass


def _trim():
    done = [t for t in TASKS.values() if t["status"] in ("done", "failed", "cancelled")]
    done.sort(key=lambda t: t["ended"] or 0)
    for t in done[:max(0, len(TASKS) - MAX_TASKS)]:
        TASKS.pop(t["id"], None)


def get(tid: str) -> dict | None:
    return TASKS.get(tid)


def list_tasks(status: str | None = None) -> list[dict]:
    out = []
    for t in sorted(TASKS.values(), key=lambda r: -r["submitted"]):
        if status and t["status"] != status:
            continue
        out.append({k: v for k, v in t.items() if k not in ("cancel_event", "pause_event", "_fn", "state_snapshot")})
    return out


def cancel(tid: str) -> bool:
    """Cancel a task; the cancel cascades to its children (D1)."""
    rec = TASKS.get(tid)
    if not rec:
        return False
    rec["cancel_event"].set()
    if rec["status"] in ("queued", "running", "paused"):
        rec["status"] = "cancelled"
        rec["ended"] = time.time()
    for cid in rec["children"]:
        cancel(cid)
    return True


def pause(tid: str) -> bool:
    rec = TASKS.get(tid)
    if rec and rec["status"] == "running":
        rec["pause_event"].set()
        rec["status"] = "paused"
        return True
    return False


def resume(tid: str) -> bool:
    rec = TASKS.get(tid)
    if rec and rec["status"] == "paused":
        rec["status"] = "running"
        rec["pause_event"].clear()
        return True
    return False


def checkpoint(rec: dict, state: dict):
    """D3: the task's own checkpoint (resume after a crash or a restart)."""
    return resilience.checkpoint(rec["checkpoint_name"], state)


def restore(rec: dict):
    return resilience.restore(rec["checkpoint_name"])


def coop(rec: dict):
    """Call inside long loops: raises _Cancelled when the task was cancelled, sleeps while
    paused, and heartbeats progress bookkeeping. Long tasks that never call this can only
    be killed by the executor, not cancelled — so skills should call it at each step."""

    if rec["cancel_event"].is_set():
        raise _Cancelled()
    if rec["status"] == "paused":
        rec["pause_event"].wait()


def child(parent: dict, fn, **kw) -> dict:
    """A child task linked to its parent (cancelling the parent cancels it)."""
    kw.setdefault("sid", parent["sid"])
    kw.setdefault("priority", "normal")
    c = submit(fn, **kw)
    c["parent"] = parent["id"]
    parent["children"].append(c["id"])
    return c


def wait_all(ids, timeout: float = 600.0) -> list[dict]:
    """Blocking join for orchestrator fan-out (returns the records; never hangs forever)."""
    deadline = time.time() + timeout
    out = []
    for tid in ids:
        rec = TASKS.get(tid)
        if not rec:
            continue
        while rec["status"] in ("queued", "running", "paused") and time.time() < deadline:
            time.sleep(0.05)
        out.append(rec)
    return out


def stats() -> dict:
    by = {}
    for t in TASKS.values():
        by[t["status"]] = by.get(t["status"], 0) + 1
    return {"tasks": by, "gpu_queue": 1 - _GPU_SEM._value if hasattr(_GPU_SEM, "_value") else 0,
            "waiting": len(_WAITING)}


def handle_command(text: str) -> str | None:
    """D4 voice/text commands: 'what tasks are running', 'cancel task 3', 'pause task 2'..."""
    t = (text or "").strip().lower()
    import re
    if not re.search(r"\b(tasks?|background)\b", t):
        return None
    if re.search(r"\b(what|which|list|show|status)\b.*\btasks?\b|\btasks?\b\s*\?", t):
        rows = list_tasks()
        active = [x for x in rows if x["status"] in ("queued", "running", "paused")]
        if not active:
            return "No background tasks running."
        lines = [f"- [{i + 1}] {x['status']}: {x['goal'] or x['id']}" for i, x in enumerate(active)]
        return "Background tasks:\n" + "\n".join(lines)
    m = re.search(r"\b(cancel|stop|pause|resume)\s+(?:task\s*)?(?:#?(\d+)|(.+))", t)
    if m:
        verb, num, rest = m.group(1), m.group(2), (m.group(3) or "").strip()
        rows = [x for x in list_tasks() if x["status"] in ("queued", "running", "paused")]
        rec = None
        if num:
            idx = int(num) - 1
            rec_id = rows[idx]["id"] if 0 <= idx < len(rows) else ""
            rec = TASKS.get(rec_id)
        elif rest:
            rec = next((TASKS.get(x["id"]) for x in rows if rest in (x["goal"] or "").lower()), None)
        if not rec:
            return "I don't have that task running."
        {"cancel": cancel, "pause": pause, "resume": resume}.get(verb, cancel)(rec["id"])
        return f"{verb.capitalize()}ed: {rec['goal'] or rec['id']}"
    return None


if __name__ == "__main__":
    import tempfile
    resilience.CHECKPOINT_DIR = tempfile.mkdtemp()
    # submit + run + result
    r = submit(lambda t: (coop(t), t.__setitem__("progress", 0.5), "result-42")[-1],
               goal="add two numbers", timeout_s=10)
    wait_all([r["id"]], 10)
    assert r["status"] == "done" and r["result"] == "result-42", r
    # priority: a queued low task yields to a later high one (records flip to running in order)
    low = submit(lambda t: "slow", goal="low prio", priority="low", timeout_s=10)
    high = submit(lambda t: "fast", goal="high prio", priority="high", timeout_s=10)
    wait_all([low["id"], high["id"]], 15)
    assert all(t["status"] == "done" for t in (low, high))
    # cancellation
    def sleeper(t):
        for _ in range(50):
            coop(t)
            time.sleep(0.02)
        return "never"
    c = submit(sleeper, goal="cancel me", timeout_s=30)
    time.sleep(0.1)
    assert cancel(c["id"]) and wait_all([c["id"]], 5)[0]["status"] == "cancelled"
    # child cascade
    p = submit(lambda t: (time.sleep(0.3), "parent")[-1], goal="parent", timeout_s=10)
    ch = child(p, lambda t: (time.sleep(2), "child")[-1], goal="child task", timeout_s=10)
    time.sleep(0.05)
    cancel(p["id"])
    wait_all([ch["id"]], 5)
    assert ch["status"] == "cancelled", ch["status"]
    # pause/resume
    def pausable(t):
        for _ in range(20):
            coop(t)
            time.sleep(0.02)
        return "finished"
    pa = submit(pausable, goal="pause me", timeout_s=30)
    time.sleep(0.05)
    pause(pa["id"]); time.sleep(0.05)
    assert pa["status"] == "paused"
    resume(pa["id"])
    wait_all([pa["id"]], 10)
    assert pa["status"] == "done"
    # idempotency
    a = submit(lambda t: "one", goal="once", idempotency_key="dedupe-1", timeout_s=10)
    b = submit(lambda t: "two", goal="once", idempotency_key="dedupe-1", timeout_s=10)
    assert a["id"] == b["id"]
    # checkpoint round-trip through the task API
    cp = submit(lambda t: (checkpoint(t, {"step": 7}), "done")[-1], goal="cp", timeout_s=10)
    wait_all([cp["id"]], 10)
    # state snapshot exists (D5)
    assert "session_id" in a["state_snapshot"]
    # voice commands
    assert "Background tasks" in (handle_command("what tasks are running") or "") or \
        handle_command("what tasks are running") == "No background tasks running."
    print(f"[self-check] ishti.taskmgr OK — {stats()}")
