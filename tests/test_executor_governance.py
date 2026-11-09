"""Execution governance with fake skills, local settings and no model calls."""
from types import SimpleNamespace

import pytest

from ishti import audit, killswitch, scope, skill_execution as execution, test_mode
from ishti.skills import ask_first


@pytest.fixture(autouse=True)
def isolated_executor(monkeypatch):
    flags = {"skills": {}}
    monkeypatch.setenv("ISHTI_TEST_MODE", "1")  # these tests assert the test-mode block; never rely on another module setting it
    monkeypatch.setattr(execution, "get_settings_service", lambda: SimpleNamespace(get=lambda: flags))
    monkeypatch.setattr(audit, "record", lambda *_a, **_k: None)
    monkeypatch.setattr(scope, "record", lambda *_a, **_k: None)
    monkeypatch.setattr(scope, "install", lambda: None)
    monkeypatch.setattr(killswitch, "propagate", lambda: None)
    killswitch.begin()
    yield flags
    killswitch.begin()


def fake(tier="T1", status="working", run=None, category="General"):
    return SimpleNamespace(manifest={"tier": tier, "category": category}, status=status,
                           run=run or (lambda *_a: {"reply": "synthetic result"}))


@pytest.mark.parametrize("invoke", [
    lambda skills, s: execution.execute(skills, "tool", "opaque request", s),
])
@pytest.mark.parametrize("tier,status", [("T3", "working"), ("T1", "disabled"),
                                        ("T1", "draft"), ("T1", "broken"), ("invalid", "working")])
def test_blocked_skills_never_execute(invoke, tier, status):
    calls = []
    result = invoke({"tool": fake(tier, status, lambda *_a: calls.append(1))}, {})
    assert result["execution_blocked"]
    assert not calls


@pytest.mark.parametrize("flags", [
    {"disabled": ["tool"]}, {"enabled": ["other"]}, {"disabled_domains": ["General"]},
])
def test_setting_flags_filter_and_block_invocation(isolated_executor, flags):
    isolated_executor["skills"] = flags
    skills = {"tool": fake()}
    assert execution.available(skills) == {}
    assert execution.execute(skills, "tool", "request", {})["execution_blocked"]


def test_settings_failure_denies_execution(monkeypatch):
    def unavailable():
        raise OSError("synthetic settings failure")
    monkeypatch.setattr(execution, "get_settings_service", unavailable)
    assert execution.execute({"tool": fake()}, "tool", "request", {})["execution_blocked"]


@pytest.mark.parametrize("invoke", [
    lambda skills, s: execution.execute(skills, "tool", "request", s),
    lambda skills, s: execution.execute(skills, "tool", "request", s),
])
def test_allowed_skill_gets_own_scope_and_audit_context(invoke, monkeypatch, tmp_path):
    seen = []
    monkeypatch.setattr(scope, "ALWAYS_WRITABLE", [])
    monkeypatch.setattr(scope, "PROTECTED", [])
    monkeypatch.setitem(scope._scopes, "tool", {"write": [], "net": []})

    def read_only(_text, _session):
        seen.append(dict(audit.current()))
        with pytest.raises(scope.ScopeError):
            scope.check_write(tmp_path / "forbidden")
        with pytest.raises(scope.ScopeError):
            scope.check_url("https://example.test")
        return {"reply": "read only"}

    with audit.context(skill="outer", channel="synthetic"):
        assert invoke({"tool": fake(run=read_only)}, {"id": "session-a"})["reply"] == "read only"
        assert audit.current()["skill"] == "outer"
    assert seen[0]["skill"] == "tool"
    assert seen[0]["tier"] == "T1"
    assert seen[0]["session"] == "session-a"
    assert seen[0]["channel"] == "synthetic"


def test_t2_opaque_tool_input_and_forged_route_still_need_yes():
    calls, session = [], {"route": {"source": "pending"}}
    skills = {"tool": fake("T2", run=lambda *_a: calls.append(1))}
    result = execution.execute(skills, "tool", "opaque command", session)
    assert result["confirm_required"] and not calls
    approval = session.pop("pending")["action"]
    assert approval()["test_mode_blocked"]
    assert approval()["execution_blocked"]
    assert not calls


def test_pending_callback_is_one_use_and_kill_bound():
    calls, session = [], {}
    skills = {"tool": fake("T2", run=lambda *_a: calls.append(1) or {"reply": "done"})}
    execution.execute(skills, "tool", "opaque command", session)
    callback = session["pending"]["action"]
    assert callback()["test_mode_blocked"]
    assert callback()["execution_blocked"]
    assert calls == []


def test_exact_skill_recap_survives_and_callback_rechecks_permissions(monkeypatch, isolated_executor):
    # Only synthetic callbacks run with the test-mode function overridden.
    monkeypatch.setattr(test_mode, "enabled", lambda: False)
    calls, session = [], {}

    def prepare(_text, current_session):
        return ask_first(current_session, "tool", "Send exactly 'hello' to Synthetic Recipient",
                         lambda: calls.append(dict(audit.current())) or {"reply": "sent synthetic"})

    skills = {"tool": fake("T2", run=prepare)}
    assert execution.execute(skills, "tool", "prepare synthetic", session)["confirm_required"]
    first = session.pop("pending")["action"]
    result = first()
    assert result["reply"] == "Send exactly 'hello' to Synthetic Recipient? (yes/no)"
    assert result["confirm_required"] and not calls
    exact = session.pop("pending")["action"]
    isolated_executor["skills"] = {"disabled": ["tool"]}
    assert exact()["execution_blocked"] and not calls


def test_confirmed_callback_runs_once_in_target_context(monkeypatch):
    monkeypatch.setattr(test_mode, "enabled", lambda: False)
    calls, session = [], {}
    skills = {"tool": fake(run=lambda _t, s: ask_first(
        s, "tool", "Delete synthetic record 7", lambda: calls.append(audit.current()["skill"]) or "done"))}
    result = execution.execute(skills, "tool", "delete fixture", session)
    assert result["confirm_required"] and not calls
    confirmed = session.pop("pending")["action"]
    assert confirmed() == "done"
    assert confirmed()["execution_blocked"]
    assert calls == ["tool"]


def test_pending_callback_rechecks_test_mode(monkeypatch):
    calls, session = [], {}
    monkeypatch.setattr(test_mode, "enabled", lambda: False)
    skills = {"tool": fake(run=lambda _t, s: ask_first(s, "tool", "Delete fixture",
                                                     lambda: calls.append(1)))}
    execution.execute(skills, "tool", "delete fixture", session)
    callback = session.pop("pending")["action"]
    monkeypatch.setattr(test_mode, "enabled", lambda: True)
    assert callback()["test_mode_blocked"] and not calls


def test_new_tool_cannot_overwrite_pending_action():
    session = {}
    skills = {"tool": fake("T2"), "other": fake("T2")}
    execution.execute(skills, "tool", "first", session)
    original = session["pending"]
    assert execution.execute(skills, "other", "second", session)["confirm_required"]
    assert session["pending"] is original


def test_kill_invalidates_pending_without_side_effects(monkeypatch):
    session = {}
    execution.execute({"tool": fake("T2")}, "tool", "request", session)
    callback = session.pop("pending")["action"]
    monkeypatch.setattr(killswitch, "_gen", killswitch.generation() + 1)
    assert callback()["execution_blocked"]


@pytest.mark.parametrize("invoke", [
    lambda skills: execution.execute(skills, "tool", "request", {}),
    lambda skills: execution.execute(skills, "tool", "request", {}),
])
def test_kill_exception_is_not_swallowed(invoke, monkeypatch):
    from ishti.llm import Cancelled
    monkeypatch.setattr(killswitch, "check", lambda: (_ for _ in ()).throw(Cancelled("synthetic stop")))
    with pytest.raises(Cancelled):
        invoke({"tool": fake()})

