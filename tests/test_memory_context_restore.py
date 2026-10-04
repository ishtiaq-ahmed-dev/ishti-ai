"""Focused regressions for restored chat context and compound relationship writes.

All storage in this module is an in-memory fake or a temporary SQLite file; it never
opens the owner's runtime session or relationship databases.
"""
import os

import pytest


class FakeConversation:
    def __init__(self, messages):
        self.messages = messages
        self.reads = 0

    def get_messages(self, _sid):
        self.reads += 1
        return self.messages


def test_restore_session_maps_persisted_roles_and_filters_unsafe_assistant_turns():
    from ishti.dispatcher import _restore_session

    conv = FakeConversation([
        {"role": "user", "content": "Remember the two family facts."},
        {"role": "ishti", "content": "Saved both facts."},
        {"role": "user", "content": "What did you save?"},
        {"role": "ishti", "content": "CLASSIFIED document: invented content."},
        {"role": "system", "content": "must not enter model history"},
        {"role": "assistant", "content": "A supported assistant-role message."},
        {"role": "user", "content": "   "},
    ])

    restored = _restore_session("scratch-session", conv)

    assert restored["history"] == [
        {"role": "user", "content": "Remember the two family facts."},
        {"role": "assistant", "content": "Saved both facts."},
        {"role": "user", "content": "What did you save?"},
        {"role": "assistant", "content": "A supported assistant-role message."},
    ]
    assert conv.reads == 1


def test_restore_session_does_not_read_persisted_history_in_incognito(monkeypatch):
    from ishti.core import incognito
    from ishti.dispatcher import _restore_session

    conv = FakeConversation([{"role": "user", "content": "private prior transcript"}])
    monkeypatch.setenv("ISHTI_INCOGNITO", "1")

    restored = _restore_session("scratch-incognito", conv)

    assert restored["history"] == []
    assert conv.reads == 0


@pytest.fixture
def scratch_relationship_graph(tmp_path, monkeypatch):
    from ishti import rgraph

    prior_path, prior_conn = rgraph.DB_PATH, rgraph._conn
    rgraph.DB_PATH = os.fspath(tmp_path / "relationships.sqlite3")
    rgraph._conn = None
    try:
        yield rgraph
    finally:
        scratch_conn = rgraph._conn
        if scratch_conn is not None:
            scratch_conn.close()
        rgraph.DB_PATH, rgraph._conn = prior_path, prior_conn


def test_compound_family_facts_route_and_persist_each_explicit_relationship(scratch_relationship_graph):
    from ishti.skills.relationships import skill
    from ishti.skills.owner import skill as owner_skill

    text = "My brother's name is Bilal and my sister's name is Ayesha"
    assert skill.match(text)
    assert not owner_skill.match(text), "family declarations belong to the relationship graph, not owner-profile facts"
    result = skill.run(text, {})

    assert "brother is Bilal" in result["reply"]
    assert "sister is Ayesha" in result["reply"]
    brother = scratch_relationship_graph.edges_of("me", rel="brother", direction="out")
    sister = scratch_relationship_graph.edges_of("me", rel="sister", direction="out")
    assert [row["d_name"] for row in brother] == ["bilal"]
    assert [row["d_name"] for row in sister] == ["ayesha"]
    assert all(row["source_type"] == "explicit_user" and row["confidence"] == 1.0
               for row in brother + sister)


def test_reported_irfan_and_akbar_compound_sentence_is_saved(scratch_relationship_graph):
    from ishti.skills.relationships import skill

    result = skill.run("My another brother is irfan and grandfather is akbar khan", {})
    assert "brother is Irfan" in result["reply"]
    assert "grandfather is Akbar Khan" in result["reply"]
    edges = scratch_relationship_graph.edges_of("me", direction="out")
    observed = {(row["rel"], row["d_name"], row["source_type"]) for row in edges}
    assert ("brother", "irfan", "explicit_user") in observed
    assert ("grandfather", "akbar khan", "explicit_user") in observed


def test_compound_family_fact_parser_does_not_invent_a_relationship_from_ordinary_text():
    from ishti.skills.relationships import skill

    assert skill._kin_declarations("My brother is sick and my sister is working") == []


@pytest.mark.parametrize("text, expected", [
    ("My brother is Bilal and my dentist is Dr. Tariq Mahmood", [("brother", "Bilal"), ("dentist", "Dr. Tariq Mahmood")]),
    ("My dentist is Dr. Tariq Mahmood and brother is Irfan", [("dentist", "Dr. Tariq Mahmood"), ("brother", "Irfan")]),
    ("My brother is Bilal; my grandfather is Akbar Khan.", [("brother", "Bilal"), ("grandfather", "Akbar Khan")]),
])
def test_compound_name_boundaries_and_honorifics(text, expected):
    from ishti.skills.relationships import skill
    assert skill._kin_declarations(text) == expected


def test_relationship_events_are_published_after_committed_writes(scratch_relationship_graph):
    from ishti import events
    seen = []

    def on_event(message):
        if message["type"] == "memory_updated":
            seen.append((message["data"], scratch_relationship_graph.edges_of("me", direction="out")))

    events.subscribe(on_event)
    try:
        scratch_relationship_graph.relate("me", "brother", "Irfan", source_type="explicit_user")
        scratch_relationship_graph.unrelate("me", "brother", "Irfan")
    finally:
        events.unsubscribe(on_event)
    assert [(event["type"], event["action"]) for event, _ in seen] == [
        ("relationship", "upsert"), ("family", "upsert"),
        ("relationship", "delete"), ("family", "delete"),
    ]
    assert all(edges[0]["d_name"] == "irfan" for _, edges in seen[:2])
    assert all(edges == [] for _, edges in seen[2:])


def test_empty_family_tree_does_not_invent_relatives(monkeypatch, scratch_relationship_graph):
    from interface.web_ui import server
    from ishti.learning import entities
    from ishti.facts import get_facts
    monkeypatch.setattr(entities, "list_people", lambda: [])
    get_facts().set("owner", "name", "Test Owner")
    family = server._get_family_members()
    assert [(row["name"], row["relationship"]) for row in family] == [("Test Owner", "self")]


def test_family_api_persists_ui_supported_relationship(monkeypatch, scratch_relationship_graph):
    import asyncio
    from interface.web_ui import server
    from ishti.learning import entities
    monkeypatch.setattr(entities, "observe", lambda *_args, **_kwargs: None)

    class Body:
        async def json(self):
            return {"name": "Test Spouse", "relationship": "spouse"}

    result = asyncio.run(server.learning_family_add(Body()))
    assert result["ok"]
    edges = scratch_relationship_graph.edges_of("me", rel="spouse", direction="out")
    assert edges[0]["d_name"] == "test spouse" and edges[0]["private"]


def test_chat_skill_sends_bounded_older_context_and_pinned_state(monkeypatch):
    from types import SimpleNamespace
    from ishti.skills.chat import skill as chat_skill
    from ishti import llm
    from ishti.core import state as conversation_state

    captured = {}

    class StateManager:
        def get(self, _sid):
            return object()

    monkeypatch.setattr(conversation_state, "get_manager", lambda: StateManager())
    monkeypatch.setattr(chat_skill, "build_context", lambda *_args, **_kwargs: "")
    monkeypatch.setattr(chat_skill, "trace", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(chat_skill, "inspect", lambda _answer: (True, None))
    monkeypatch.setattr(llm, "style", lambda: "style")
    monkeypatch.setattr(llm, "model_for", lambda _role: "local-test")

    def fake_chat(messages, **_kwargs):
        captured["messages"] = messages
        return SimpleNamespace(content="Your brother is Irfan.")

    monkeypatch.setattr(llm, "chat", fake_chat)
    history = [{"role": "user" if i % 2 == 0 else "assistant",
                "content": ("Earlier fact: Irfan is my brother" if i == 8 else f"turn {i}")}
               for i in range(24)]
    session = {"id": "scratch-chat", "history": history, "state_summary": "Active person: Irfan"}
    result = chat_skill.run("Who is my brother?", session)

    assert result["reply"] == "Your brother is Irfan."
    assembled = "\n".join(m["content"] for m in captured["messages"])
    assert "Earlier fact: Irfan is my brother" in assembled
    assert "Active person: Irfan" in assembled
    assert len(captured["messages"]) < len(history)
