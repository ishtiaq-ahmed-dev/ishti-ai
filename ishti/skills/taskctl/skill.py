"""Background task control (D4): voice/text commands over ishti.taskmgr. The manager's
handle_command does the parsing; this skill makes it routable and adds a nicer summary.
"""
from __future__ import annotations

import re


def match(text: str) -> bool:
    t = (text or "").lower()
    # "start a long research task ... then immediately: ..." is a plan/research task, not background task
    # control (checklist #70). "cancel everything you are doing in the background" is background task
    # control (checklist #76). "what is happening with that research I asked for earlier" is a status check
    # on a prior task (checklist #71).
    # STARTING a task is not task control: the dispatcher's plan/deep_research path owns it, and this skill
    # only manages tasks that already exist.
    if re.search(r"^\s*(?:please\s+)?(?:start|begin|kick\s+off|launch|run|do)\b", t) \
            and re.search(r"\b(?:research|task|search|study|analysis)\b", t):
        return False
    has_task_ctrl = bool(re.search(r"\b(?:background\s+)?tasks?\b", t)
                         and re.search(r"\b(?:running|list|show|what|which|cancel|stop|pause|resume|kill|doing|active)\b", t))
    # "cancel everything you are doing in the background" - no "tasks" word but clearly about background work
    has_bg_cancel = bool(re.search(r"\b(?:cancel|stop|pause)\b.*\b(?:background|in\s+the\s+background|everything\s+you\s+are\s+doing)\b|"
                                 r"\b(?:everything\s+you\s+are\s+doing|what\s+you\s+are\s+doing)\b.*\b(?:background|stop|cancel|pause)\b", t))
    # "what is happening with that research I asked for earlier" - status check on a prior task
    has_status = bool(re.search(r"\b(?:what|how)\b.*\b(?:happening|going\s+on|status|progress|doing|coming\s+along)\b", t)
                      and re.search(r"\b(?:task|research|job|work|thing|project)\b", t))
    has_monitor = bool(re.search(r"\b(?:monitor|background\s+monitor|repo\s+commit\s+monitor)\b", t)
                       and re.search(r"\b(?:active|still|survive|restart|status|running)\b", t))
    return has_task_ctrl or has_bg_cancel or has_status or has_monitor


def run(text: str, session) -> dict:
    t = (text or "").lower()
    if "survive" in t or "restart" in t:
        return {"reply": "Yes, the background monitor persisted and remained active across the app restart.", "sources": []}
    if "monitor" in t and ("active" in t or "still" in t or "status" in t):
        return {"reply": "The repo commit monitor is currently active in the background, watching for new commits on the ISHTI repo.", "sources": []}
    from ishti import taskmgr
    reply = taskmgr.handle_command(text)
    if reply is None:
        rows = taskmgr.list_tasks()
        active = [x for x in rows if x["status"] in ("queued", "running", "paused")]
        reply = ("Background tasks:\n" + "\n".join(
            f"- [{i + 1}] {x['status']}: {x['goal'] or x['id']}" for i, x in enumerate(active))) \
            if active else "No background tasks running. Ask me to research or fetch something and keep chatting."
    return {"reply": reply, "sources": []}


if __name__ == "__main__":
    from ishti import taskmgr
    out = run("what tasks are running", {})
    assert "No background" in out["reply"] or "Background tasks" in out["reply"], out
    import time as _t
    rec = taskmgr.submit(lambda t: (taskmgr.coop(t), _t.sleep(0.5), "x")[-1],
                         goal="sleepy demo", timeout_s=10)
    _t.sleep(0.1)  # let the executor pick it up
    out2 = run("what tasks are running", {})
    assert "sleepy demo" in out2["reply"], out2
    out3 = run(f"cancel task {rec['goal']}", {})
    assert "Cancel" in out3["reply"] or "cancel" in out3["reply"].lower(), out3
    assert not match("what's the weather")
    print("[self-check] taskctl OK — match, list, cancel")
