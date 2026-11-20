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
PER_TURN = MAX_STEPS


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
    failed = plan.get("failed") or []
    if failed:
        replies.append(f"{len(failed)} of {len(plan['steps'])} step(s) didn't work: "
                       + ", ".join(plan["steps"][i]["tool"] for i in failed) + " - the others are done.")
    sources = [s for r in plan["results"] for s in r.get("sources", [])]
    return {**(extra or {}), "reply": "\n\n".join(replies), "sources": sources,
            "mode": "BRAIN", "workflow_id": plan["id"], "workflow_state": plan["state"],
            "status": "succeeded" if plan["state"] == "completed" and not failed else "partial",
            "remaining_steps": remaining,
            "verified": (False if plan["state"] != "completed" or any(r.get("verified") is False for r in plan["results"])
                         else True if plan["results"] and all(r.get("verified") is True for r in plan["results"]) else None),
            "tools": [{"tool": s["tool"], "input": s["input"], "result": r.get("reply", "")[:1500]}
                      for s, r in zip(plan["steps"], plan["results"])]}


PARALLEL_MAX = 4  # read-only steps run at once ("weather in Lahore, Karachi and Dubai" takes one step's time)


def _parallel_batch(plan, skills):
    """The upcoming run of read-only (T0) steps that use no earlier result: safe to run at the same time."""
    batch = []
    for i in range(plan["next"], min(len(plan["steps"]), plan["next"] + PARALLEL_MAX)):
        step, skill = plan["steps"][i], skills.get(plan["steps"][i]["tool"])
        if skill is None or skill.manifest.get("tier") != "T0" or "{{step:" in step["input"]:
            break
        batch.append(i)
    return batch


def _prefetch(batch, plan, run_tool, skills, session):
    """{step index: output} for a batch, run concurrently. The executor below consumes them in order, so journaling,
    failure handling and the reply are exactly as if they had run one by one."""
    from concurrent.futures import ThreadPoolExecutor
    from ishti import calltrace
    with ThreadPoolExecutor(max_workers=len(batch), thread_name_prefix="ishti-step") as pool:
        futures = {i: pool.submit(calltrace.carry(run_tool), skills, plan["steps"][i]["tool"],
                                  plan["steps"][i]["input"], session) for i in batch}
    out = {}
    for i, future in futures.items():
        try:
            out[i] = future.result()
        except Exception as exc:
            if type(exc).__name__ == "Cancelled":  # the kill switch stops the whole plan, not one step
                raise
            out[i] = {"reply": f"The {plan['steps'][i]['tool']} step failed ({type(exc).__name__}).",
                      "verified": False, "status": "failed"}
    return out


def execute(session, plan, run_tool, skills):
    from ishti import killswitch
    prefetched = {}
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
            skill = skills.get(step["tool"])
            tier = skill.manifest.get("tier", "T1") if skill is not None else "T2"
            if "{{step:" in step["input"] and tier == "T1":
                # Another step's output (a web page, a file, a message) is untrusted data. A state-changing T1 tool fed
                # with it asks first (OWASP LLM01/ASI01); T2 tools ask anyway, T0 tools only read.
                from ishti.skills import ask_first
                plan["state"] = "approval"
                save(session, plan)
                out = ask_first(session, step["tool"],
                                f"Use the previous step's result as input to {step['tool']}?\n\n{text[:600]}\n\nGo ahead",
                                lambda tool=step["tool"], bound=text: run_tool(skills, tool, bound, session))
                return result(plan, {**out, "confirm_required": True})
            plan["state"] = "executing"
            save(session, plan)  # durable BEFORE dispatch, so a crash cannot auto-replay
            if plan["next"] not in prefetched and len(batch := _parallel_batch(plan, skills)) > 1:
                prefetched.update(_prefetch(batch, plan, run_tool, skills, session))
            out = (prefetched.pop(plan["next"]) if plan["next"] in prefetched
                   else run_tool(skills, step["tool"], text, session))
            if not isinstance(out, dict):
                out = {"reply": "The tool returned an invalid result.", "verified": False, "status": "failed"}
            if out.get("confirm_required"):
                plan["state"] = "approval"
                save(session, plan)
                return result(plan, out)
            if out.get("declined") and len(plan["steps"]) == 1:  # nothing ran: close it; the caller re-routes the request
                plan["state"] = "cancelled"
                save(session, plan)
                return {**result(plan, out), "declined": True}
            if out.get("declined"):  # a step whose skill passed: skipped silently ("The shopping skill had nothing to
                # do for this." was glued onto the real answer)
                plan["results"].append({"reply": "", "status": "skipped"})
                step["state"] = "skipped"
                plan["next"] += 1
                plan["state"] = "ready"
                save(session, plan)
                continue
            if (out.get("execution_blocked") or out.get("needs_input") or out.get("verified") is False
                    or out.get("test_mode_blocked") or out.get("status") in ("failed", "partial", "needs_input", "unknown")):
                ref = f"{{{{step:{plan['next'] + 1}}}}}"
                # only a definite failure (nothing took effect) is skipped past; an unconfirmed or unknown outcome may
                # have happened, so it still stops the plan and is reported as uncertain
                if out.get("status") != "failed" or any(ref in later["input"] for later in plan["steps"][plan["next"] + 1:]):
                    plan["state"] = "blocked"  # a later step needs this one's output (or the owner must answer)
                    save(session, plan)
                    return result(plan, out)
                # nothing depends on it: record the failure and carry on (weather ok, search failed, calc still runs)
                plan["results"].append({"reply": out.get("reply", "That step failed."), "verified": False,
                                        "status": "failed"})
                plan.setdefault("failed", []).append(plan["next"])
                step["state"] = "failed"
                plan["next"] += 1
                plan["state"] = "ready"
                save(session, plan)
                continue
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
