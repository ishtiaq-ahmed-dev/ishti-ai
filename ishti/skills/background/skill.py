"""Background work: "research the best laptops under 200k in the background", "in the background: summarize my
unread email", "compare these three phones and let me know when it's done".

The request runs as a background task (ishti.taskmgr) in its own session, like a scheduled job, so the chat stays free
and nothing waits on it. The answer lands in the bell (and the paired phone channel); if the work needs a yes, the
question waits in the bell with Yes/No - the same approval rules, nothing is pre-approved. "list background tasks" /
"cancel ..." stay with the taskctl skill."""
import re
import uuid

I = re.I
BACKGROUND = re.compile(
    r"^\s*(?:please\s+)?(?:in\s+the\s+background\s*[,:\-]?\s*(?P<a>\S.+?)"
    r"|(?:can\s+you\s+)?(?P<b>\S.+?)\s*[,.]?\s*(?:in\s+the\s+background(?:\s+and\s+(?:let\s+me\s+know|tell\s+me|notify\s+me|ping\s+me)"
    r"(?:\s+when\s+(?:it'?s\s+|you'?re\s+)?(?:done|finished|ready))?)?"
    r"|(?:and\s+)?(?:let\s+me\s+know|tell\s+me|notify\s+me|ping\s+me)\s+when\s+(?:it'?s\s+|you'?re\s+)?(?:done|finished|ready)))"
    r"\s*[.!]?\s*$", I)
# questions about or control of background work belong to taskctl
NOT_A_JOB = re.compile(r"^\s*(?:what|which|how|is|are|show|list|cancel|stop|pause|resume|kill|check\s+on)\b", I)


def request(text: str) -> str:
    m = BACKGROUND.match(text.strip())
    if not m or NOT_A_JOB.match(text):
        return ""
    return (m["a"] or m["b"] or "").strip(" ,.")


def match(text):
    return len(request(text).split()) >= 2


def _job(inner: str, sid: str, handle):
    from ishti import notifications

    def job(task):
        try:
            out = handle(inner, session_id=sid, channel="scheduled")
            reply = str((out or {}).get("final_answer") or "") if isinstance(out, dict) else str(out or "")
        except Exception as exc:
            notifications.add("error", f"Background task failed: {inner[:100]}", f"{type(exc).__name__}: {exc}"[:300])
            raise
        if re.search(r"\(yes/no\)\s*$", reply):
            from ishti.dispatcher import SESSIONS
            pending = (SESSIONS.get(sid) or {}).get("pending") or {}
            notifications.add("alert", f"Waiting for your yes: {reply[:120]}", body=reply[:2000],
                              meta={"session": sid, "answerable": True, "pending_at": pending.get("at")})
        else:
            notifications.add("job", f"Done in the background: {inner[:80]}", body=reply[:2000])
            try:
                from ishti.channels import service
                service.get().push("background", f"Done: {inner[:80]}\n\n{reply[:1500]}")
            except Exception as exc:
                from ishti.core.logutil import swallowed
                swallowed("ishti/skills/background/skill.py:push", exc)
        return reply
    return job


def run(text, session, handle=None):
    inner = request(text)
    if not inner:
        return None
    if handle is None:
        from ishti import dispatcher
        handle = dispatcher.handle
    from ishti import taskmgr
    sid = "bg-" + uuid.uuid4().hex[:10]  # its own session: its yes/no question is answered from the bell
    rec = taskmgr.submit(_job(inner, sid, handle), goal=inner, sid=sid)
    return {"reply": f"Started in the background: \"{inner[:120]}\". The answer will be in the bell when it's done "
                     f"(task {rec['id']}). Say \"list background tasks\" to check on it.", "sources": []}


if __name__ == "__main__":
    assert request("research laptops under 200k in the background") == "research laptops under 200k"
    assert request("in the background: summarize my unread email") == "summarize my unread email"
    assert request("compare these phones and let me know when it's done") == "compare these phones"
    assert not request("cancel everything you are doing in the background") and not request("what is running in the background")
    print("background self-check ok")
