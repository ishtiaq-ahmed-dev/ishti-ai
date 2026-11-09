"""Offline long-chat context checks. No owner transcript, model or browser access."""
from types import SimpleNamespace
import time

import pytest

from ishti.core.context import ContextAssembler, history_for_session
from ishti.core import state as cstate


def dialogue(count=300):
    return [{"role": "user" if i % 2 == 0 else "assistant", "content": f"ordinary turn {i}"}
            for i in range(count)]


def test_query_finds_fact_before_previous_40_message_cutoff():
    history = dialogue()
    history[2]["content"] = "My brother is Irfan and my grandfather is Akbar Khan."
    before = [dict(m) for m in history]
    result = ContextAssembler().build(history, cstate.ConversationState("scratch"), query="Who is my brother?")
    joined = "\n".join(m["content"] for m in result)
    assert "Irfan" in joined and "Akbar Khan" in joined
    assert "of 284 older messages" in joined
    assert history == before
    assert sum(len(m["content"]) for m in result) <= 16000
    assert all("Irfan" not in m["content"] for m in result if m["role"] == "system")


def test_relevant_fact_deep_in_a_long_message_is_not_prefix_clipped():
    history = dialogue(80)
    history[0]["content"] = "irrelevant filler " * 1000 + "My brother Irfan lives in Kohat." + " tail" * 500
    result = ContextAssembler().build(history, cstate.ConversationState("scratch"), query="Where does my brother live?")
    assert "My brother Irfan lives in Kohat" in "\n".join(m["content"] for m in result)


@pytest.mark.parametrize("budget,turns", [(1000, 500), (4000, 8), (16000, 6)])
def test_total_budget_survives_huge_state_and_messages(budget, turns):
    result = ContextAssembler(max_total_chars=budget, verbatim_turns=turns).build(
        [{"role": "user", "content": "brother " * 2000} for _ in range(400)],
        cstate.ConversationState("scratch"), state_summary="topic " * 2000, query="brother")
    assert sum(len(m["content"]) for m in result) <= budget


def test_durable_prefix_and_unsaved_tail_are_joined_once():
    saved = dialogue()
    live = saved[-200:] + [{"role": "user", "content": "new unsaved turn"}]
    manager = SimpleNamespace(get_messages=lambda _sid: saved)
    combined = history_for_session({"id": "scratch", "history": live, "history_truncated": True}, manager)
    assert combined == saved + live[-1:]


def test_incognito_does_not_read_saved_prefix(monkeypatch):
    monkeypatch.setenv("ISHTI_INCOGNITO", "1")
    def forbidden(_sid):
        pytest.fail("incognito read the persisted transcript")
    live = dialogue(2)
    assert history_for_session({"id": "scratch", "history": live, "history_truncated": True},
                               SimpleNamespace(get_messages=forbidden)) == live


def test_finish_keeps_runtime_history_after_20_messages_and_marks_overflow(monkeypatch):
    from ishti import dispatcher, calltrace
    monkeypatch.setattr(dispatcher.threading.Thread, "start", lambda _self: None)
    monkeypatch.setattr(dispatcher, "RECOVER", [])
    monkeypatch.setattr(calltrace, "end", lambda: [])
    writes = []
    manager = SimpleNamespace(add_message=lambda *args, **kwargs: writes.append(args))
    session = {"history": dialogue(198), "trace": lambda **kwargs: None}
    for i in range(2):
        dispatcher._finish(f"request {i}", "scratch", manager, session, [], time.perf_counter(),
                           "text", "chat", {"reply": f"answer {i}"}, {})
    assert len(session["history"]) == 200
    assert session["history_truncated"]
    assert session["history"][-4:] == [
        {"role": "user", "content": "request 0"}, {"role": "assistant", "content": "answer 0"},
        {"role": "user", "content": "request 1"}, {"role": "assistant", "content": "answer 1"}]
    assert len(writes) == 4


def test_generated_secret_never_enters_background_learning(monkeypatch):
    from ishti import dispatcher, calltrace
    jobs = []
    monkeypatch.setattr(dispatcher.threading.Thread, "start", lambda worker: jobs.append(worker._target))
    monkeypatch.setattr(dispatcher, "RECOVER", [])
    monkeypatch.setattr(calltrace, "end", lambda: [])
    writes = []
    manager = SimpleNamespace(add_message=lambda *args, **kwargs: writes.append(args))
    session = {"history": [], "trace": lambda **kwargs: None, "no_history": True}
    password = "synthetic-only-password-42"
    result = dispatcher._finish("Generate a password", "scratch", manager, session, [],
                                time.perf_counter(), "text", "utilities", {"reply": password}, {})
    assert result["final_answer"] == password
    assert not jobs
    assert password not in str(writes) and password not in str(session["history"])


def test_chat_retry_preserves_older_context(monkeypatch):
    from ishti.skills.chat import skill
    from ishti import llm
    monkeypatch.setattr(skill, "build_context", lambda *a, **k: "")
    monkeypatch.setattr(skill, "trace", lambda *a, **k: None)
    checks = iter([(False, None), (True, None)])
    monkeypatch.setattr(skill, "inspect", lambda _answer: next(checks))
    monkeypatch.setattr(llm, "style", lambda **_k: "style")
    monkeypatch.setattr(llm, "dynamic_block", lambda *_a, **_k: "NOW")
    monkeypatch.setattr(llm, "model_for", lambda _role: "fixture")
    monkeypatch.setattr(cstate, "get_manager", lambda: cstate.ConversationStateManager(cstate.MemoryStore()))
    seen = []
    def chat(messages, **kwargs):
        seen.append(messages)
        return SimpleNamespace(content="Your brother is Irfan.")
    monkeypatch.setattr(llm, "chat", chat)
    history = dialogue(80)
    history[0]["content"] = "My brother is Irfan."
    skill.run("Who is my brother?", {"id": "scratch", "history": history})
    assert len(seen) == 2
    assert all("Irfan" in "\n".join(m["content"] for m in messages) for messages in seen)


def test_brain_keeps_older_context_in_its_one_call(monkeypatch):
    from ishti import brain, llm
    from ishti.memstore import retrieval
    from ishti import skill_execution
    monkeypatch.setattr(retrieval, "assemble_context", lambda *a, **k: "")
    monkeypatch.setattr(skill_execution, "execute", lambda *a, **k: {"reply": "fixture read", "sources": []})
    monkeypatch.setattr(llm, "style", lambda **_k: "style")
    monkeypatch.setattr(llm, "dynamic_block", lambda *_a, **_k: "NOW")
    monkeypatch.setattr(llm, "model_for", lambda _role: "fixture")
    monkeypatch.setattr(cstate, "get_manager", lambda: cstate.ConversationStateManager(cstate.MemoryStore()))
    seen = []
    def chat(messages, **kwargs):
        seen.append(messages)
        return SimpleNamespace(content="Your brother is Irfan.")
    monkeypatch.setattr(llm, "chat", chat)
    history = dialogue(80)
    history[0]["content"] = "My brother is Irfan."
    skills = {"fixture": SimpleNamespace(status="ok", manifest={"tier": "T1", "description": "fixture"})}
    brain.run("Who is my brother?", {"id": "scratch", "history": history}, skills)
    assert len(seen) == 1, "one model call per turn"
    assert "Irfan" in "\n".join(m["content"] for m in seen[0])