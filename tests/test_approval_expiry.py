"""An approval left waiting is not consent later: a "yes" after dispatcher.APPROVAL_TTL_S never runs the old action,
while a timely yes still does. Offline; ISHTI_INCOGNITO=1 so no conversation, memory or audit row is written to the
owner's data; the fake action is a lambda (no model call: "yes" is consumed by the gate)."""
import time

from ishti import dispatcher, test_mode
from ishti.skills import ask_first


def test_stale_yes_is_refused_and_fresh_yes_runs(monkeypatch):
    monkeypatch.setenv("ISHTI_INCOGNITO", "1")
    for name in ("_remember", "_learn", "_learn_turn", "_memory_extract", "_ftlog"):
        monkeypatch.setattr(dispatcher, name, lambda *a, **k: None)
    ran, reached = [], []
    sid = f"expiry_{time.time_ns()}"
    dispatcher.handle("hello", session_id=sid)  # creates the session (answered by the greeting reflex)
    session = dispatcher.SESSIONS[sid]

    ask_first(session, "calc", "Run the fake action", lambda: ran.append(1) or "done")
    session["pending"]["at"] = time.time() - dispatcher.APPROVAL_TTL_S - 5
    out = dispatcher.handle("yes", session_id=sid)
    assert "expired" in out["final_answer"] and ran == []
    assert session.get("pending") is None

    # a timely yes reaches the action (under ISHTI_TEST_MODE it is stopped at that last step by test_mode.blocked)
    real_blocked = test_mode.blocked
    monkeypatch.setattr(test_mode, "blocked", lambda skill, gate, action, tier: reached.append(skill)
                        or real_blocked(skill, gate, action, tier))
    ask_first(session, "calc", "Run the fake action", lambda: ran.append(1) or "done")
    out = dispatcher.handle("yes", session_id=sid)
    assert "expired" not in out["final_answer"]
    assert ran == [1] or reached == ["calc"]
