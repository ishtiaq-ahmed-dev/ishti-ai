"""Workflow mining and procedure caching (spec 19).

Mines recurring successful multi-step task sequences into proposed reusable workflows.
"""
import hashlib
import os
from typing import Any, Dict, List, Optional
from ishti.learning import store

def _sig(steps: List[str]) -> str:
    clean = [s.strip().lower() for s in steps if s.strip()]
    return hashlib.sha256("->".join(clean).encode("utf-8")).hexdigest()[:16]

def record(task_id: str) -> Optional[Dict[str, Any]]:
    """Record a completed task's sequence into workflows if successful."""
    if not task_id:
        return None
    row = store.row("SELECT * FROM tasks WHERE task_id=?", (task_id,))
    if not row or row["final_status"] != "success":
        return None
    raw_steps = store.js(row["steps"], [])
    tools = [s.get("tool") for s in raw_steps if isinstance(s, dict) and s.get("tool") and s.get("success")]
    if len(tools) < 2:
        return None
    
    sig = _sig(tools)
    now = store.now()
    existing = store.row("SELECT * FROM workflows WHERE signature=?", (sig,))
    if existing:
        cnt = int(existing["count"] or 1) + 1
        conf = min(1.0, 0.4 + (cnt * 0.2))
        store.write("UPDATE workflows SET count=?, last_seen=?, confidence=? WHERE signature=?",
                    (cnt, now, conf, sig))
    else:
        name = row["goal"] or f"Workflow for {tools[0]} -> {tools[-1]}"
        store.add("workflows", signature=sig, name=name[:200], steps=store.dump(tools),
                  count=1, first_seen=now, last_seen=now, status="active", confidence=0.6)
    r = store.row("SELECT * FROM workflows WHERE signature=?", (sig,))
    return dict(r) if r else None

def scan(min_count: Optional[int] = None) -> List[Dict[str, Any]]:
    """Scan workflows that meet the minimum run count threshold."""
    need = int(min_count or store.evidence().get("workflow", 3))
    rows = store.rows("SELECT * FROM workflows WHERE count >= ? AND status='active'", (need,))
    out = []
    for r in rows:
        d = dict(r)
        d["steps"] = store.js(d.get("steps"), [])
        out.append(d)
    return out

def suggest() -> Optional[str]:
    """Suggest an active workflow if eligible."""
    cands = scan()
    if cands:
        return f"I can automate this procedure: {cands[0]['name']} ({' -> '.join(cands[0]['steps'])})"
    return None

def list_workflows() -> List[Dict[str, Any]]:
    rows = store.rows("SELECT * FROM workflows ORDER BY count DESC, last_seen DESC")
    out = []
    for r in rows:
        d = dict(r)
        d["steps"] = store.js(d.get("steps"), [])
        out.append(d)
    return out

def set_status(workflow_id: int, status: str) -> Dict[str, Any]:
    st = "retired" if status == "retired" else "active"
    store.write("UPDATE workflows SET status=? WHERE id=?", (st, int(workflow_id)))
    row = store.row("SELECT * FROM workflows WHERE id=?", (int(workflow_id),))
    return dict(row) if row else {}

if __name__ == "__main__":
    import tempfile
    tmp = tempfile.mkdtemp()
    store.set_db_path(os.path.join(tmp, "wf.db"))
    
    # Fake successful tasks
    for i in range(3):
        tid = f"t_{i}"
        store.add("tasks", task_id=tid, ts=store.now(), goal="sync downloads to backup",
                  steps=store.dump([{"tool": "find", "success": True}, {"tool": "copy", "success": True}]),
                  final_status="success", correction="", lessons="[]", reward=1.0, domain="", source="")
        record(tid)
        
    cands = scan(min_count=3)
    assert len(cands) == 1 and cands[0]["count"] == 3
    assert cands[0]["steps"] == ["find", "copy"]
    
    set_status(cands[0]["id"], "retired")
    assert len(scan(min_count=3)) == 0
    
    print("[self-check] learning.workflows OK - signature hashing, frequency counting, status toggle")
