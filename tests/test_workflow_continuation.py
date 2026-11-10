"""Real durable journal with deterministic tools; no accounts or model services."""
import pytest

from ishti import workflows


@pytest.fixture
def session(tmp_path, monkeypatch):
    monkeypatch.setattr(workflows, "_path", lambda: str(tmp_path / "workflows.db"))
    return {"id": "workflow-test", "incognito": False}


def tool(skills, name, text, session):
    return {"reply": text, "sources": [], "verified": True}


def test_remaining_steps_survive_a_fresh_session(session):
    calls = [{"tool": "calc", "input": str(i)} for i in range(5)]
    plan = workflows.new(session, calls)
    out = workflows.execute(session, plan, tool, {})  # all steps in one turn: no "continue workflow" ritual
    assert out["remaining_steps"] == 0 and out["workflow_state"] == "completed" and len(out["tools"]) == 5
    assert out["reply"].splitlines()[0] == "0"
    restored = {"id": session["id"]}  # a fresh session sees the finished plan, not a re-run
    assert workflows.load(restored)["state"] == "completed"


def test_a_failed_step_does_not_drop_independent_ones(session):
    calls = [{"tool": "calc", "input": "1"}, {"tool": "search", "input": "x"}, {"tool": "calc", "input": "3"}]
    def flaky(skills, name, text, s):
        return {"reply": "search backend unreachable", "status": "failed"} if name == "search" else tool(skills, name, text, s)
    out = workflows.execute(session, workflows.new(session, calls), flaky, {})
    assert out["workflow_state"] == "completed" and out["status"] == "partial"
    assert "3" in out["reply"] and "didn't work: search" in out["reply"]
    dependent = [{"tool": "search", "input": "x"}, {"tool": "calc", "input": "{{step:1}}"}]
    out = workflows.execute(session, workflows.new(session, dependent), flaky, {})
    assert out["workflow_state"] == "blocked"  # the next step needs the failed one's output


def test_approval_retains_completed_results_and_resumes_remaining(session):
    calls = [{"tool": "calc", "input": "4"}, {"tool": "send", "input": "{{step:1}}"},
             {"tool": "notes", "input": "done"}]
    def approving(skills, name, text, s):
        return {"reply": "Approve send?", "confirm_required": True} if name == "send" else tool(skills, name, text, s)
    out = workflows.execute(session, workflows.new(session, calls), approving, {})
    assert out["confirm_required"] and out["reply"].startswith("4")
    workflows.before_approval(session, "send", True)
    assert workflows.load({"id": session["id"]})["state"] == "executing"
    out = workflows.after_approval(session, "send", {"reply": "Sent", "verified": True}, True, tool, {})
    assert out["workflow_state"] == "completed" and len(out["tools"]) == 3


def test_crash_during_action_is_unknown_and_never_replayed(session):
    plan = workflows.new(session, [{"tool": "send", "input": "hello"}])
    plan["state"] = "executing"
    workflows.save(session, plan)
    seen = []
    restored = {"id": session["id"]}
    out = workflows.execute(restored, workflows.load(restored), lambda *a: seen.append(a), {})
    assert out["workflow_state"] == "unknown" and seen == []


def test_second_continue_closes_unknown_instead_of_walling(session):
    """'continue workflow' after a crash must resolve the unknown state (never replay the step),
    not return the same reconcile message forever - that used to wall the chat."""
    plan = workflows.new(session, [{"tool": "send", "input": "hello"}])
    plan["state"] = "executing"
    workflows.save(session, plan)
    restored = {"id": session["id"]}
    def never(*a):
        pytest.fail("an interrupted step must never be replayed")
    assert workflows.execute(restored, workflows.load(restored), never, {})["workflow_state"] == "unknown"
    out = workflows.execute(restored, workflows.load(restored), never, {})
    assert out["workflow_state"] == "cancelled" and "not repeated" in out["reply"]
    # terminal now: a fresh plan is allowed and replaces the closed journal
    plan2 = workflows.new(restored, [{"tool": "calc", "input": "1"}])
    assert workflows.load(restored)["id"] == plan2["id"]


def test_continue_after_blocked_step_never_replays_it(session):
    """A step that stopped unconfirmed (blocked) may have taken effect; an explicit
    'continue workflow' must close the plan instead of re-running the step."""
    def unconfirmed(skills, name, text, s):
        return {"reply": "send unconfirmed", "verified": False}
    out = workflows.execute(session, workflows.new(session, [{"tool": "send", "input": "hello"}]), unconfirmed, {})
    assert out["workflow_state"] == "blocked"
    restored = {"id": session["id"]}
    out = workflows.execute(restored, workflows.load(restored),
                            lambda *a: pytest.fail("a blocked step must never be replayed"), {})
    assert out["workflow_state"] == "cancelled" and "not repeated" in out["reply"]


def test_denial_cancels_remainder_without_claiming_prior_work_undone(session):
    plan = workflows.new(session, [{"tool": "send", "input": "x"}, {"tool": "notes", "input": "y"}])
    plan["state"] = "approval"
    workflows.save(session, plan)
    out = workflows.after_approval(session, "send", {"reply": "Declined"}, False,
                                   lambda *a: pytest.fail("must not execute"), {})
    assert out["workflow_state"] == "cancelled"


def test_dependency_uses_actual_prior_result(session):
    seen = []
    def capture(skills, name, text, s):
        seen.append(text)
        return {"reply": "actual fresh result" if name == "search" else text}
    calls = [{"tool": "search", "input": "question"}, {"tool": "write", "input": "Summarize {{step:1}}"}]
    out = workflows.execute(session, workflows.new(session, calls), capture, {})
    assert "actual fresh result" in seen[1] and out["workflow_state"] == "completed"


def test_failed_dependency_blocks_later_side_effects(session):
    seen = []
    def fail(skills, name, text, s):
        seen.append(name)
        return {"reply": "failed", "verified": False}
    calls = [{"tool": "read", "input": "x"}, {"tool": "send", "input": "{{step:1}}"}]
    out = workflows.execute(session, workflows.new(session, calls), fail, {})
    assert seen == ["read"] and out["workflow_state"] == "blocked"


def test_tool_without_verification_is_not_declared_verified(session):
    calls = [{"tool": "legacy", "input": "request"}]
    out = workflows.execute(session, workflows.new(session, calls),
                            lambda *a: {"reply": "unverified legacy output"}, {})
    assert out["workflow_state"] == "completed" and out["verified"] is None


def test_incognito_workflow_never_creates_database(session, tmp_path):
    session["incognito"] = True
    workflows.new(session, [{"tool": "calc", "input": "x"}])
    assert not (tmp_path / "workflows.db").exists()


def test_workflow_journal_is_sealed_and_tampering_fails_closed(session, tmp_path):
    import sqlite3
    workflows.new(session, [{"tool": "send", "input": "sensitive recipient and content"}])
    with sqlite3.connect(tmp_path/"workflows.db") as db:
        payload = db.execute("SELECT payload FROM plans").fetchone()[0]
        assert payload.startswith("sealed1:") and "sensitive recipient" not in payload
        db.execute("UPDATE plans SET payload=?", ("sealed1:corrupted",))
    with pytest.raises(RuntimeError, match="authenticated/decrypted"):
        workflows.load({"id": session["id"]})
    with sqlite3.connect(tmp_path/"workflows.db") as db:
        db.execute("UPDATE plans SET payload=?", ('{"state":"ready","steps":[]}',))
    with pytest.raises(RuntimeError, match="Unsealed workflow record rejected"):
        workflows.load({"id": session["id"]})
