"""Background task control (D4): voice/text commands over ishti.taskmgr. The manager's
handle_command does the parsing; this skill makes it routable and adds a nicer summary.
"""
from __future__ import annotations

import re


def match(text: str) -> bool:
    t = (text or "").lower()
    return bool(re.search(r"\b(?:background\s+)?tasks?\b", t)
                and re.search(r"\b(?:running|list|show|what|which|cancel|stop|pause|resume|kill)\b", t))


def run(text: str, session) -> dict:
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
