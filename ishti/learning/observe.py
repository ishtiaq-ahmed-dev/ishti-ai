"""Turn observation and multi-step task logging (docs/LEARNING_INTERFACE.md section 2).

Records interaction and task events into SQLite (data/learning.db), fail-soft, keeping
memory (volatile) separated from learning history.
"""
import os
import sys
import threading
from typing import Any, Dict, List, Optional
from ishti.learning import store

try:  # best-effort failure log (never breaks the module if logging is unavailable)
    from ishti.core.logutil import swallowed
except Exception:  # pragma: no cover
    def swallowed(*_a, **_k):
        return None


_LOCAL = threading.local()

def begin(text: str, sid: str = "", channel: str = "text", dec: Optional[Dict[str, Any]] = None,
          memories: Optional[Any] = None, task_id: Optional[str] = None,
          step_no: Optional[int] = None, domain: str = "", message_id: str = "") -> str:
    """Record the start of an interaction turn; returns interaction_id."""
    iid = store.uid("it_")
    now = store.now()
    d = dec or {}
    skill = d.get("skill") or ""
    try:
        store.add("interactions", id=iid, ts=now, session_id=str(sid or ""),
                  channel=str(channel or "text"), request=str(text or "")[:1000],
                  skill=skill, decision=store.dump(d),
                  memories=store.dump(memories) if memories is not None else "",
                  considered=store.dump(d.get("scores") or []), result="", status="running",
                  reward=0.0, task_id=str(task_id or ""),
                  step_no=int(step_no) if step_no is not None else 0,
                  domain=str(domain or ""), source=str(d.get("source") or "routing"),
                  message_id=str(message_id or ""))
    except Exception as _exc:
        swallowed('ishti/learning/observe.py:begin', _exc)
    _LOCAL.last_interaction_id = iid
    return iid

def end(iid: str, reply: Optional[str] = None, skill: Optional[str] = None,
        status: str = "ok", result: Optional[Any] = None,
        reward: Optional[float] = None) -> Dict[str, Any]:
    """Close an interaction turn with final reply/status/reward."""
    if not iid:
        return {}
    fields = {"status": str(status or "ok")}
    if reply is not None:
        fields["result"] = str(reply)[:4000]
    elif result is not None:
        fields["result"] = store.dump(result)[:4000]
    if skill:
        fields["skill"] = str(skill)
    if reward is not None:
        fields["reward"] = float(reward)
    try:
        set_clauses = [f"{k}=?" for k in fields.keys()]
        args = list(fields.values()) + [iid]
        store.write(f"UPDATE interactions SET {', '.join(set_clauses)} WHERE id=?", tuple(args))
        row = store.row("SELECT * FROM interactions WHERE id=?", (iid,))
        return dict(row) if row else {}
    except Exception:
        return {}


def task_begin(goal: str, domain: str = "", source: str = "") -> str:
    """Start tracking a multi-step workflow/task."""
    tid = store.uid("tsk_")
    now = store.now()
    try:
        store.add("tasks", task_id=tid, ts=now, goal=str(goal or "")[:500],
                  steps="[]", final_status="running", correction="",
                  lessons="[]", reward=0.0, domain=str(domain or ""),
                  source=str(source or ""))
    except Exception as _exc:
        swallowed('ishti/learning/observe.py:task_begin', _exc)
    return tid

def task_step(task_id: str, step: int, tool: str, result: Any, success: bool, note: str = "") -> None:
    """Append a step execution result to the task."""
    if not task_id:
        return
    try:
        row = store.row("SELECT steps FROM tasks WHERE task_id=?", (task_id,))
        steps = store.js(row["steps"], []) if row else []
        steps.append({
            "step": int(step), "tool": str(tool), "success": bool(success),
            "result": str(result)[:500], "note": str(note)[:200], "ts": store.now()
        })
        store.write("UPDATE tasks SET steps=? WHERE task_id=?", (store.dump(steps), task_id))
    except Exception as _exc:
        swallowed('ishti/learning/observe.py:task_step', _exc)

def task_end(task_id: str, status: str, correction: str = "",
             lessons: Optional[List[str]] = None, reward: Optional[float] = None) -> None:
    """Finish a multi-step task with status/correction/lessons."""
    if not task_id:
        return
    try:
        fields = {"final_status": str(status or "ok")}
        if correction:
            fields["correction"] = str(correction)[:500]
        if lessons is not None:
            fields["lessons"] = store.dump(lessons)
        if reward is not None:
            fields["reward"] = float(reward)
        set_clauses = [f"{k}=?" for k in fields.keys()]
        args = list(fields.values()) + [task_id]
        store.write(f"UPDATE tasks SET {', '.join(set_clauses)} WHERE task_id=?", tuple(args))
    except Exception as _exc:
        swallowed('ishti/learning/observe.py:task_end', _exc)

def recent(limit: int = 50) -> List[Dict[str, Any]]:
    try:
        rows = store.rows("SELECT * FROM interactions ORDER BY ts DESC LIMIT ?", (int(limit),))
        return [dict(r) for r in rows]
    except Exception:
        return []

def get(iid: str) -> Optional[Dict[str, Any]]:
    if not iid:
        return None
    try:
        row = store.row("SELECT * FROM interactions WHERE id=?", (iid,))
        return dict(row) if row else None
    except Exception:
        return None

if __name__ == "__main__":
    import tempfile
    tmp = tempfile.mkdtemp()
    store.set_db_path(os.path.join(tmp, "obs.db"))
    
    iid = begin("Open chrome", sid="s1", dec={"skill": "apps", "confidence": 0.9})
    assert iid.startswith("it_")
    res = end(iid, reply="Opened Chrome.", skill="apps", status="ok", reward=1.0)
    assert res["status"] == "ok" and res["reward"] == 1.0 and "Chrome" in res["result"]

    tid = task_begin("Organize downloads", domain="files")
    task_step(tid, 1, "find_files", "Found 5 files", True)
    task_step(tid, 2, "move_files", "Moved to folder", True)
    task_end(tid, "success", reward=1.0)
    
    row = store.row("SELECT * FROM tasks WHERE task_id=?", (tid,))
    assert row["final_status"] == "success"
    steps = store.js(row["steps"], [])
    assert len(steps) == 2 and steps[0]["tool"] == "find_files"

    assert len(recent()) == 1
    assert get(iid)["id"] == iid
    print("[self-check] learning.observe OK - begin/end interaction, task tracking, step logging")

def turn(text: str, reply: str = "", skill: str = "", sid: str = "",
         channel: str = "text", dec: Optional[Dict[str, Any]] = None,
         status: str = "ok", reward: float = 0.0, domain: str = "", message_id: str = "") -> str:
    """One-shot interaction logger convenience for dispatcher."""
    iid = begin(text=text, sid=sid, channel=channel, dec=dec or {"skill": skill}, domain=domain,
                message_id=message_id)
    end(iid, reply=reply, skill=skill, status=status, reward=reward)
    return iid
