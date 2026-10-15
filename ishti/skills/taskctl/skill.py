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
    # "cancel everything you are doing in the background" - no "tasks" word but clearly about background work.
    # "pause what you're working on, I need you for something urgent" (checklist #74) is the same shape: acting
    # on the current background work. Answering it from chat let a reply claim it paused everything when no
    # pause had happened; the manager's handle_command returns None here and this skill says nothing is running.
    bg_target = (r"background|in\s+the\s+background|everything\s+you\s+are\s+doing|everything\s+you're\s+doing|"
                 r"what\s+you\s+are\s+doing|what\s+you're\s+doing|what\s+you\s+are\s+working\s+on|what\s+you're\s+working\s+on")
    has_bg_cancel = bool(re.search(rf"\b(?:cancel|stop|pause)\b.*\b(?:{bg_target})\b|"
                                 rf"\b(?:{bg_target})\b.*\b(?:background|stop|cancel|pause)\b", t))
    # "what is happening with that research I asked for earlier" - status check on a prior task
    has_status = bool(re.search(r"\b(?:what|how)\b.*\b(?:happening|going\s+on|status|progress|doing|coming\s+along)\b", t)
                      and re.search(r"\b(?:task|research|job|work|thing|project)\b", t))
    has_monitor = bool(re.search(r"\b(?:monitor|background\s+monitor|repo\s+commit\s+monitor)\b", t)
                       and re.search(r"\b(?:active|still|survive|restart|status|running)\b", t))
    return has_task_ctrl or has_bg_cancel or has_status or has_monitor


def _active() -> list[dict]:
    from ishti import taskmgr
    return [x for x in taskmgr.list_tasks() if x["status"] in ("queued", "running", "paused")]


def _listing(active: list[dict]) -> str:
    return ("Still running: " + "; ".join(f"{x['status']}: {x['goal'] or x['id']}" for x in active) + ".") \
        if active else "Nothing is running in the background right now."


def run(text: str, session) -> dict:
    t = (text or "").lower()
    from ishti import taskmgr
    # The answers below must come from the task manager, never from the sentence that was asked:
    # canned "yes, the monitor is still active" replies claimed background monitoring that had never
    # been started (checklist 221/223). There IS no scheduler here, so the honest answer is that no
    # standing monitor exists and that in-memory tasks do not outlive the process.
    if re.search(r"\bmonitor", t) and re.search(r"\b(?:active|still|status|running|survive|restart)\b", t):
        active = _active()
        live = [x for x in active if re.search(r"monitor|watch", x["goal"] or "", re.I)]
        if live:
            return {"reply": f"Yes - {len(live)} monitor task(s) still running. " + _listing(live), "sources": []}
        head = ("No - background tasks live in memory for the run that created them only, so a monitor would not "
                "have survived the app restart and none is active now. ") \
            if re.search(r"\b(?:survive|restart)\b", t) else \
            "No repo (or other) monitor is running - I have no scheduled/standing monitor mechanism, so I never " \
            "started one. "
        return {"reply": head + _listing(active), "sources": []}
    reply = taskmgr.handle_command(text)
    if reply is None:
        active = _active()
        reply = ("Background tasks:\n" + "\n".join(
            f"- [{i + 1}] {x['status']}: {x['goal'] or x['id']}" for i, x in enumerate(active))) \
            if active else "No background tasks running. Ask me to research or fetch something and keep chatting."
    elif reply.startswith("I don't have that task running"):
        # "cancel the repo-monitoring task from earlier" with no such task: say so AND list what is really
        # running, instead of only reporting the miss (checklist 222).
        reply += " " + _listing(_active())
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
    # a monitor that was never started must not be reported as running (checklist 221/223)
    assert taskmgr._WAITING is not None
    for q in ("Is the repo commit monitor still active?", "Did the background monitor survive the app restart?"):
        ans = run(q, {})["reply"]
        assert "No " in ans and "still running" not in ans, (q, ans)
    miss = run("Cancel the repo-monitoring task from earlier", {})["reply"]
    assert "Nothing is running in the background" in miss, miss
    print("[self-check] taskctl OK — match, list, cancel, honest monitor status")
