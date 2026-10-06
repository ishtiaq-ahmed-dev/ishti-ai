"""Execution governance with fake skills, local settings and no model calls."""
from types import SimpleNamespace

import pytest

from ishti import agents, audit, killswitch, scope, skill_execution as execution, test_mode
from ishti.arch2 import pipeline
from ishti.skills import ask_first


@pytest.fixture(autouse=True)
def isolated_executor(monkeypatch):
    flags = {"skills": {}}
    monkeypatch.setattr(execution, "get_settings_service", lambda: SimpleNamespace(get=lambda: flags))
    monkeypatch.setattr(audit, "record", lambda *_a, **_k: None)
    monkeypatch.setattr(scope, "record", lambda *_a, **_k: None)
    monkeypatch.setattr(scope, "install", lambda: None)
    monkeypatch.setattr(killswitch, "propagate", lambda: None)
    monkeypatch.setattr(agents, "_perf", lambda *_a: None)
    killswitch.begin()
    yield flags
    killswitch.begin()


def fake(tier="T1", status="working", run=None, category="General"):
    return SimpleNamespace(manifest={"tier": tier, "category": category}, status=status,
                           run=run or (lambda *_a: {"reply": "synthetic result"}))


@pytest.mark.parametrize("invoke", [
    lambda skills, s: pipeline._run_tool(skills, "tool", "opaque request", s, []),
    lambda skills, s: agents._skill_run(skills, "tool", "opaque request", s),
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
    assert pipeline._usable(skills) == {}
    assert execution.execute(skills, "tool", "request", {})["execution_blocked"]


def test_settings_failure_denies_execution(monkeypatch):
    def unavailable():
        raise OSError("synthetic settings failure")
    monkeypatch.setattr(execution, "get_settings_service", unavailable)
    assert execution.execute({"tool": fake()}, "tool", "request", {})["execution_blocked"]


@pytest.mark.parametrize("invoke", [
    lambda skills, s: pipeline._run_tool(skills, "tool", "request", s, []),
    lambda skills, s: agents._skill_run(skills, "tool", "request", s),
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
    lambda skills: pipeline._run_tool(skills, "tool", "request", {}, []),
    lambda skills: agents._skill_run(skills, "tool", "request", {}),
])
def test_kill_exception_is_not_swallowed(invoke, monkeypatch):
    from ishti.llm import Cancelled
    monkeypatch.setattr(killswitch, "check", lambda: (_ for _ in ()).throw(Cancelled("synthetic stop")))
    with pytest.raises(Cancelled):
        invoke({"tool": fake()})


def test_arch2_returns_confirmation_before_validation(monkeypatch):
    from ishti import llm
    monkeypatch.setattr(pipeline.retrieval, "assemble_context", lambda *_a: "")
    monkeypatch.setattr(pipeline, "_tool_manifests", lambda *_a: "fixture")
    monkeypatch.setattr(pipeline, "_rule_tool", lambda *_a: None)
    monkeypatch.setattr(llm, "style", lambda: "fixture")
    monkeypatch.setattr(llm, "model_for", lambda *_a: "fixture")
    monkeypatch.setattr(llm, "chat", lambda *_a, **_k: SimpleNamespace(content='{"tool":"tool","input":"opaque"}'))
    monkeypatch.setattr(llm, "ask", lambda *_a, **_k: pytest.fail("approval entered model validation"))
    session = {}
    result = pipeline.run("request", session, {"tool": fake("T2")})
    assert result["confirm_required"] and session["pending"]["skill"] == "tool"


def test_mutating_fanout_stops_at_first_confirmation(monkeypatch):
    calls = []

    def run_agent(agent, task, skills, session, bb, budget):
        calls.append(task)
        return execution.execute(skills, "apps", task, session)

    monkeypatch.setattr(agents, "_run_agent", run_agent)
    session = {}
    tasks = [{"agent": "pc_control", "task": t} for t in ("first", "second")]
    results = agents._fan_out(tasks, {"apps": fake("T2")}, session, {}, {}, 0)
    assert calls == ["first"]
    assert results[0]["confirm_required"]
    assert session["pending"]["request"] == "first"


def test_worker_confirmation_adopts_exact_followup_in_owner_session(monkeypatch):
    monkeypatch.setattr(test_mode, "enabled", lambda: False)
    calls, owner, child = [], {}, {}

    def prepare(_text, session):
        return ask_first(session, "tool", "Exact synthetic recipient and text",
                         lambda: calls.append(1) or "done")

    skills = {"tool": fake("T2", run=prepare)}
    execution.execute(skills, "tool", "prepare fixture", child)
    agents._adopt_pending(child, owner)
    initial = owner.pop("pending")["action"]
    assert initial()["confirm_required"]
    assert owner["pending"]["gate"] == "Exact synthetic recipient and text"
    exact = owner.pop("pending")["action"]
    assert exact() == "done" and calls == [1]
    assert exact()["execution_blocked"]


def test_agent_top_level_does_not_synthesize_approval(monkeypatch):
    monkeypatch.setattr(agents, "_budget", lambda: {"pattern": "sequential", "max_subagents": 2})
    monkeypatch.setattr(agents, "decompose", lambda *_a: [{"agent": "pc_control", "task": "fixture"}])
    monkeypatch.setattr(agents, "assign", lambda tasks, _skills: tasks)
    monkeypatch.setattr(agents, "_run_agent", lambda _a, text, skills, session, *_rest:
                        execution.execute(skills, "apps", text, session))
    monkeypatch.setattr(agents, "synthesize", lambda *_a: pytest.fail("approval entered synthesis"))
    result = agents.run("fixture", {}, {"apps": fake("T2")})
    assert result["confirm_required"] and result["mode"] == "MULTI_AGENT"
