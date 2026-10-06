from types import SimpleNamespace

from ishti.skills.memory import skill as memory_skill
from ishti.skills.relationships import skill as relationships


def test_fact_forget_requires_confirmation_before_mutating(monkeypatch):
    calls = []
    facts = SimpleNamespace(forget=lambda name: calls.append(name) or 1,
                            get=lambda name: {"entity": name})
    monkeypatch.setattr(memory_skill, "get_facts", lambda: facts)
    monkeypatch.delenv("ISHTI_TEST_MODE", raising=False)
    session = {}
    result = memory_skill.run("forget project-x", session)
    assert "yes/no" in result["reply"] and session["pending"]
    assert calls == []
    approved = session["pending"]["action"]()
    assert "Forgot project-x" in approved["reply"]
    assert calls == ["project-x"]


def test_relationship_delete_is_gated_before_unrelate(monkeypatch):
    calls = []

    class Graph:
        def edges_of(self, *_args, **_kwargs):
            return [{"s_name": "me", "d_name": "ali", "rel": "friend"}]

        def unrelate(self, *args):
            calls.append(args)

    graph = Graph()
    monkeypatch.delenv("ISHTI_TEST_MODE", raising=False)
    session = {}
    result = relationships._rel_delete(graph, "Ali", "friend", session)
    assert "yes/no" in result["reply"] and calls == []
    approved = session["pending"]["action"]()
    assert "Removed" in approved["reply"]
    assert calls
