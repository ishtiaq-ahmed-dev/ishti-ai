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
import re

_ENV = "ISHTI_TEST_MODE"

# A request that changes the world (send/delete/pay/post/...) must be confirmed by the owner before it
# runs. Under test mode the skill never executes, so the gate is answered by a real confirmation
# question instead of the old opaque "never touch the real world" line - the owner is asked, exactly
# as they would be in a live session, and a following "yes" is stopped by blocked() at the last step.
_ACTION = re.compile(
    r"\b(?:send|reply|forward|delete|remove|erase|purge|post|publish|comment|react|mark|pay|buy|order|"
    r"purchase|upload|install|uninstall|move|rename|call|message|text|email|trade|sell|withdraw|book|"
    r"schedule|back\s*up|clean\s*up)\b", re.I)


def gated_reply(skill: str, gate: str, tier: str = "T2") -> dict:
    """The reply for a T2 request under test mode. The skill is NOT run (no real side effect), but the
    answer is honest and shaped as the owner expects: a world-changing action returns the confirmation
    question, anything else returns a plain dry-run note."""
    from ishti import audit
    audit.record(f"TEST-MODE DRY {tier} {skill}: {(gate or '')[:120]}", "", skill=skill,
                 initiator="test-harness", tier=tier, confirmed=None, request="[test-mode]")
    req = re.sub(r"^run\s+", "", (gate or "").strip()).strip(" '\"")
    if _ACTION.search(req):
        return {"reply": f"Shall I go ahead and {req[:130]}? Confirm with yes or no and I'll act.",
                "sources": [], "test_mode_dry": True, "confirm_required": True}
    return {"reply": f"{req[:130]} - in a live session I'd handle this now. "
                     f"(dry run: test mode is on, so I didn't touch the real app or data.)",
            "sources": [], "test_mode_dry": True}

# Never executed under test mode, whatever a skill's code path claims. Matched case-insensitively
# against the action description AND (best effort) audited by the dispatcher wrapper.
FORBIDDEN = ("send", "reply", "react", "delete", "remove", "erase", "pay", "buy", "order", "purchase",
             "post", "publish", "submit", "upload", "transfer", "forward", "rename", "move", "install",
             "uninstall", "kill", "shutdown", "restart", "trade", "sell", "withdraw", "email", "message")


def enabled() -> bool:
    """True only when the env var is exactly '1' (opt-in, never on by default)."""
    return os.environ.get(_ENV) == "1"


def scratch_dir() -> str:
    """The one writable data dir in scratch mode, or "" when scratch is off.

    Tests get their own per-pid dir (``_work/scratch/<pid>/data``); a harness that needs several
    processes (server + seeder + verifier) to share ONE store sets ISHTI_SCRATCH_DIR, so every
    process lands in the same place. Creating the dir is part of the contract: a caller about to
    bind a store path must be able to rely on the parent existing."""
    # ISHTI_SCRATCH alone opts in: the pytest suite needs scratch stores WITHOUT test mode (test mode turns T2 actions
    # into dry runs, which the tests exercise for real) - it wrote ~2,300 rows into the owner's audit log per day of runs
    if os.environ.get("ISHTI_SCRATCH") != "1":
        return ""
    pinned = os.environ.get("ISHTI_SCRATCH_DIR")
    if pinned:
        folder = os.path.abspath(pinned)
    else:
        from ishti import ROOT
        folder = os.path.join(ROOT, "_work", "scratch", str(os.getpid()), "data")
    os.makedirs(folder, exist_ok=True)
    return folder


def scratch_path(default_path: str) -> str:
    """default_path, or its scratch-dir twin when scratch mode is on (same basename)."""
    folder = scratch_dir()
    if not folder:
        return default_path
    return os.path.join(folder, os.path.basename(default_path))


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
