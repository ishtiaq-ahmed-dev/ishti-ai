"""ISHTI_TEST_MODE=1 - the global safety switch for automated testing (QA pass 2026-09-30).

Rule (QA prompt 1): automated tests must never send a real message, place a real trade, make a real
purchase, delete/rename real user files, upload real data, or contact real people. This switch

(a) BLOCKS every T2 side effect at the LAST step - the moment after the owner's "yes" and before any
    skill action() runs. Blocking here (not at routing) means the whole real path is exercised:
    routing, tier lookup, gate question, confirmation - only the side effect itself is stopped.
(b) RECORDS what WOULD have happened in the audit log as ``TEST-MODE BLOCK <tier> <skill>: <gate>``,
    so the harness can assert both "the action was reached" and "nothing real happened".
(c) Lets the harness answer T2 confirmations with a scripted queue: push_scripted("yes") /
    push_scripted("no") - the dispatcher consumes them instead of a human reply.

Design: env-gated, default OFF, zero behaviour change when off (production path untouched). One
function - guarded() - is the only thing skills/dispatcher need to call.
"""
import os

_ENV = "ISHTI_TEST_MODE"

# Never executed under test mode, whatever a skill's code path claims. Matched case-insensitively
# against the action description AND (best effort) audited by the dispatcher wrapper.
FORBIDDEN = ("send", "reply", "react", "delete", "remove", "erase", "pay", "buy", "order", "purchase",
             "post", "publish", "submit", "upload", "transfer", "forward", "rename", "move", "install",
             "uninstall", "kill", "shutdown", "restart", "trade", "sell", "withdraw", "email", "message")


def enabled() -> bool:
    """True only when the env var is exactly '1' (opt-in, never on by default)."""
    return os.environ.get(_ENV) == "1"


def blocked_action_desc(action) -> str:
    try:
        import inspect
        return " ".join(inspect.getdoc(action) or inspect.getsource(action)[:160].split())
    except Exception:
        return repr(action)


def blocked(skill: str, gate: str, action=None, tier: str = "T2") -> dict:
    """The block record: what WOULD have happened. Called at the last step instead of action()."""
    desc = blocked_action_desc(action) if action is not None else ""
    from ishti import audit
    audit.record(f"TEST-MODE BLOCK {tier} {skill}: {(gate or '')[:120]}", desc[:200], skill=skill,
                 initiator="test-harness", tier=tier, confirmed=None, request="[test-mode]")
    return {"reply": f"[test-mode] Confirmed - but automated tests never touch the real world, so the "
                     f"{tier} action of '{skill}' was stopped at the last step and only recorded: "
                     f"{(gate or '')[:140]}",
            "sources": [], "test_mode_blocked": True}


def guarded(skill: str, gate: str, action, tier: str = "T2") -> dict:
    """Call at the last step of a T2 flow: under test mode -> blocked+recorded; otherwise -> action()."""
    if enabled():
        return blocked(skill, gate, action, tier)
    return action()


def scripted_answers() -> list:
    """The harness's scripted yes/no queue. Only consulted when test mode is on."""
    return _scripted


_scripted: list = []


def push_scripted(answer: str) -> None:
    """Queue a scripted confirmation: push_scripted('yes') / push_scripted('no'). FIFO."""
    _scripted.append("yes" if str(answer).lower().startswith("y") else "no")


def pop_scripted(default: str = "no") -> str:
    if not enabled():
        return default
    return _scripted.pop(0) if _scripted else default


def reset() -> None:
    _scripted.clear()


if __name__ == "__main__":
    # self-check: blocked() records to the audit log and returns the marker; scripted queue FIFO
    os.environ[_ENV] = "1"
    from ishti import audit
    before_rows = audit.query(since=0, limit=10_000)
    out = blocked("zap_send", "send message to Ali", lambda: "REAL SEND (must never happen)")
    assert out["test_mode_blocked"] and "stopped at the last step" in out["reply"]
    after_rows = audit.query(since=0, limit=10_000)
    new = [r for r in after_rows if r not in before_rows]     # my row, wherever it sorted
    assert new and any("TEST-MODE BLOCK" in (r[3] or "") for r in new), new[:3]
    reset()
    push_scripted("yes"); push_scripted("no")
    assert pop_scripted() == "yes" and pop_scripted() == "no" and pop_scripted() == "no"
    os.environ.pop(_ENV)
    assert not enabled() and pop_scripted("yes") == "yes"   # off: queue never consulted
    calls = []
    r = guarded("x", "gate", lambda: calls.append(1) or {"reply": "ran"})
    assert calls == [1] and r["reply"] == "ran"             # off: guarded() just runs the action
    print("[self-check] test_mode OK - T2 blocked+recorded at the last step, scripted queue FIFO, "
          "zero behaviour change when off")
