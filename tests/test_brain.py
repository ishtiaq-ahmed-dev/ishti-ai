"""The single architecture (ishti/brain.py): one model call per turn, tools run through the governed executor,
plain answers pass through, and the shortlist never offers chat, a disabled skill or more than SHORTLIST_MAX tools.
Offline: the model and the skill executor are stubs."""
import pytest

from ishti import brain, llm
from ishti.memstore import retrieval


class FakeSkill:
    def __init__(self, name, description="does things", status="working", tier="T0", priority=50):
        self.name = name
        self.status = status
        self.manifest = {"description": description, "tier": tier, "priority": priority, "route": description}


@pytest.fixture
def stubs(monkeypatch):
    calls = {"model": [], "tools": []}
    replies = []

    def fake_chat(messages, **kw):
        calls["model"].append(kw)
        return type("R", (), {"content": replies.pop(0) if replies else ""})()

    def fake_execute(skills, name, text, session):
        calls["tools"].append((name, text))
        return {"reply": f"{name} did: {text}", "sources": []}

    monkeypatch.setattr(llm, "chat", fake_chat)
    monkeypatch.setattr(llm, "style", lambda: "STYLE")
    monkeypatch.setattr(llm, "model_for", lambda role: "planner-model")
    monkeypatch.setattr(retrieval, "assemble_context", lambda *a, **k: "")
    monkeypatch.setattr("ishti.skill_execution.execute", fake_execute)
    return calls, replies


def _session():
    return {"id": __import__("uuid").uuid4().hex, "history": [], "trace": lambda **kw: None}


def _skills():
    return {
        "weather": FakeSkill("weather", "weather forecast and rain", priority=10),
        "reminders": FakeSkill("reminders", "set reminders and timers", priority=20),
        "calc": FakeSkill("calc", "arithmetic and calculations", priority=30),
        "chat": FakeSkill("chat", "conversation", priority=5),
        "old": FakeSkill("old", "retired skill", status="retired"),
    }


def test_one_model_call_then_the_chosen_tool_answers(stubs):
    calls, replies = stubs
    replies.append('{"calls": [{"tool": "calc", "input": "2 + 2"}]}')
    out = brain.run("what is 2 plus 2", _session(), _skills())
    assert len(calls["model"]) == 1, "a tool turn must cost exactly one model call"
    assert calls["tools"] == [("calc", "2 + 2")]
    assert out["reply"] == "calc did: 2 + 2" and out["mode"] == "BRAIN"


def test_plain_prose_is_the_answer_and_no_tool_runs(stubs):
    calls, replies = stubs
    replies.append("The capital of Kazakhstan is Astana.")
    out = brain.run("what is the capital of Kazakhstan", _session(), _skills())
    assert out["reply"] == "The capital of Kazakhstan is Astana."
    assert calls["tools"] == [] and len(calls["model"]) == 1


def test_a_call_naming_an_unlisted_tool_returns_none_so_the_route_decides(stubs):
    calls, replies = stubs
    replies.append('{"tool": "teleport", "input": "mars"}')
    assert brain.run("beam me up", _session(), _skills()) is None
    assert calls["tools"] == []


def test_several_parts_run_in_order_and_are_capped(stubs):
    calls, replies = stubs
    many = ",".join('{"tool": "calc", "input": "%d+1"}' % i for i in range(9))
    replies.append('{"calls": [%s]}' % many)
    out = brain.run("add one to nine numbers", _session(), _skills())
    assert len(calls["tools"]) == brain.MAX_CALLS
    assert out["reply"].count("calc did:") == brain.MAX_CALLS
    assert out["remaining_steps"] == 6 and out["workflow_state"] == "paused"


def test_an_approval_request_is_returned_as_is(stubs, monkeypatch):
    calls, replies = stubs
    replies.append('{"calls": [{"tool": "reminders", "input": "call Ali at 5"}]}')
    monkeypatch.setattr("ishti.skill_execution.execute",
                        lambda skills, name, text, session: {"reply": "Shall I set it? (yes/no)", "confirm_required": True})
    out = brain.run("remind me to call Ali at 5", _session(), _skills())
    assert out["confirm_required"] is True and out["reply"].endswith("(yes/no)")


def test_shortlist_never_offers_chat_retired_or_disabled_skills_and_respects_the_cap():
    skills = _skills()
    names = brain.shortlist(skills, "what is the weather tomorrow")
    assert "chat" not in names and "old" not in names
    assert names[0] == "weather" or "weather" in names
    many = {f"skill{i}": FakeSkill(f"skill{i}", f"tool number {i}") for i in range(40)}
    assert len(brain.shortlist(many, "tool number 7")) <= brain.SHORTLIST_MAX


def test_rule_choice_leads_the_shortlist():
    skills = _skills()
    hint = {"skill": "calc", "source": "rule"}
    assert brain.shortlist(skills, "remind me tomorrow", hint)[0] == "calc"


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-q"]))
