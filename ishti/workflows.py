"""Bounded durable continuation for model-selected tool batches.

The journal stores steps/results, never callbacks or approval authority. A crash
while a step is executing becomes unknown; it must not be automatically replayed.
"""
from contextlib import closing
import json
import os
import re
import sqlite3
import threading
import time
import uuid

LOCK = threading.RLock()
MAX_STEPS = 12
PER_TURN = 3


def _path():
    from ishti import ROOT
    from ishti.test_mode import scratch_path
    return scratch_path(os.path.join(ROOT, "data", "workflows.db"))


def _db():
    path = _path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    db = sqlite3.connect(path, timeout=10)
    db.execute("CREATE TABLE IF NOT EXISTS plans(session TEXT PRIMARY KEY, payload TEXT, updated REAL)")
    return db


def save(session, plan):
    from ishti.core import incognito
    from ishti import secure_store
    session["_workflow"] = plan
    if incognito.enabled(session) or not session.get("id"):
        return
    payload = secure_store.seal_text(json.dumps(plan, ensure_ascii=False))
    with LOCK, closing(_db()) as db, db:
        db.execute("INSERT INTO plans VALUES(?,?,?) ON CONFLICT(session) DO UPDATE SET payload=excluded.payload, updated=excluded.updated",
                   (session["id"], payload, time.time()))
        db.execute("DELETE FROM plans WHERE updated < ?", (time.time() - 30*86400,))


def load(session):
    from ishti.core import incognito
    from ishti import secure_store
    if session.get("_workflow"):
        return session["_workflow"]
    if incognito.enabled(session) or not session.get("id"):
        return None
    with LOCK, closing(_db()) as db, db:
        row = db.execute("SELECT payload FROM plans WHERE session=?", (session["id"],)).fetchone()
    return json.loads(secure_store.open_text(row[0])) if row else None


def new(session, calls):
    plan = {"id": uuid.uuid4().hex, "state": "ready", "next": 0,
            "steps": [{**c, "state": "pending"} for c in calls], "results": []}
    save(session, plan)
    return plan


def inputs(plan, step):
    """Explicit {{step:1}} binding; only previously completed results may bind."""
    def replace(match):
        index = int(match[1]) - 1
        if index < 0 or index >= plan["next"] or index >= len(plan["results"]):
            raise ValueError("A dependent step references a result that is not complete.")
        from ishti.untrusted import fence
        return fence(plan["results"][index].get("reply", "")[:8000])
    return re.sub(r"\{\{step:(\d+)\}\}", replace, step["input"])


def result(plan, extra=None):
    replies = [r.get("reply", "") for r in plan["results"] if r.get("reply")]
    if extra and extra.get("reply"):
        replies.append(extra["reply"])
    remaining = len(plan["steps"]) - plan["next"]
    if plan["state"] == "paused":
        replies.append(f"{remaining} step(s) remain. Say 'continue workflow' to continue.")
    elif plan["state"] == "unknown":
        replies.append("The interrupted step may have taken effect. Reconcile its outcome before retrying; it was not repeated.")
    elif plan["state"] == "cancelled":
        replies.append("The remaining workflow steps were cancelled.")
    sources = [s for r in plan["results"] for s in r.get("sources", [])]
    return {**(extra or {}), "reply": "\n\n".join(replies), "sources": sources,
            "mode": "BRAIN", "workflow_id": plan["id"], "workflow_state": plan["state"],
            "status": "succeeded" if plan["state"] == "completed" else "partial",
            "remaining_steps": remaining,
            "verified": (False if plan["state"] != "completed" or any(r.get("verified") is False for r in plan["results"])
                         else True if plan["results"] and all(r.get("verified") is True for r in plan["results"]) else None),
            "tools": [{"tool": s["tool"], "input": s["input"], "result": r.get("reply", "")[:1500]}
                      for s, r in zip(plan["steps"], plan["results"])]}


def execute(session, plan, run_tool, skills):
    from ishti import killswitch
    with LOCK:
        if plan["state"] in ("executing", "unknown", "blocked"):
            if plan["state"] == "executing":
                # first sighting after a crash: mark, never replay (module doc)
                plan["state"] = "unknown"
                save(session, plan)
                return result(plan)
            # unknown: crash already reconciled. blocked: the last step stopped unconfirmed, so it
            # may have taken effect. Either way the owner's explicit "continue workflow" must not
            # re-run the step - close the plan instead of replaying it or walling forever.
            plan["state"] = "cancelled"
            save(session, plan)
            return result(plan, {"reply": "The interrupted step is not repeated; the workflow was closed. "
                                          "Tell me what to redo and I will start fresh."})
        for _ in range(PER_TURN):
            if plan["next"] >= len(plan["steps"]):
                plan["state"] = "completed"
                save(session, plan)
                return result(plan)
            killswitch.check()
            from ishti import calltrace
            calltrace.remaining()
            step = plan["steps"][plan["next"]]
            try:
                text = inputs(plan, step)
            except ValueError as exc:
                plan["state"] = "blocked"
                save(session, plan)
                return result(plan, {"reply": str(exc), "execution_blocked": True})
            plan["state"] = "executing"
            save(session, plan)  # durable BEFORE dispatch, so a crash cannot auto-replay
            out = run_tool(skills, step["tool"], text, session)
            if not isinstance(out, dict):
                out = {"reply": "The tool returned an invalid result.", "verified": False, "status": "failed"}
            if out.get("confirm_required"):
                plan["state"] = "approval"
                save(session, plan)
                return result(plan, out)
            if (out.get("execution_blocked") or out.get("needs_input") or out.get("verified") is False
                    or out.get("test_mode_blocked") or out.get("status") in ("failed", "partial", "needs_input", "unknown")):
                plan["state"] = "blocked"
                save(session, plan)
                return result(plan, out)
            plan["results"].append({k: out[k] for k in ("reply", "sources", "verified", "status") if k in out})
            step["state"] = "completed"
            plan["next"] += 1
            plan["state"] = "ready"
            save(session, plan)
        plan["state"] = "completed" if plan["next"] == len(plan["steps"]) else "paused"
        save(session, plan)
        return result(plan)


def before_approval(session, skill, accepted):
    plan = load(session)
    if (accepted and plan and plan["state"] == "approval"
            and plan["steps"][plan["next"]]["tool"] == skill):
        plan["state"] = "executing"
        save(session, plan)


def after_approval(session, skill, out, accepted, run_tool, skills):
    plan = load(session)
    if not plan or plan["state"] not in ("approval", "executing") or plan["steps"][plan["next"]]["tool"] != skill:
        return out
    if not accepted:
        plan["state"] = "cancelled"
        save(session, plan)
        return result(plan, out)
    if out.get("confirm_required"):
        plan["state"] = "approval"
        save(session, plan)
        return result(plan, out)  # skill-specific second confirmation is still required
    if (out.get("test_mode_blocked") or out.get("execution_blocked") or out.get("verified") is False
            or out.get("needs_input") or out.get("status") in ("failed", "partial", "unknown", "needs_input")):
        plan["state"] = "blocked"
        save(session, plan)
        return result(plan, out)
    plan["results"].append({k: out[k] for k in ("reply", "sources", "verified", "status") if k in out})
    plan["steps"][plan["next"]]["state"] = "completed"
    plan["next"] += 1
    plan["state"] = "ready"
    save(session, plan)
    return execute(session, plan, run_tool, skills)
