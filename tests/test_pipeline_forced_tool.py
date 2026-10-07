"""When the rules own a request (e.g. 'delete them' -> fileops) a small model that answers in prose instead of
calling the tool must not win: the matched tool runs. And a tool that keeps failing verification stops after 2 tries."""
from types import SimpleNamespace

from ishti import llm
from ishti.arch2 import pipeline
from ishti.core import state as cstate


def _stub(monkeypatch, forced, chat, ask='{"ok": true}'):
    monkeypatch.setattr(pipeline.retrieval, "assemble_context", lambda *a, **k: "")
    monkeypatch.setattr(pipeline, "_tool_manifests", lambda *a: "fixture")
    monkeypatch.setattr(pipeline, "_rule_tool", lambda *a: forced)
    monkeypatch.setattr(pipeline, "trace", lambda *a, **k: None)
    monkeypatch.setattr(llm, "style", lambda: "style")
    monkeypatch.setattr(llm, "model_for", lambda _role: "fixture")
    monkeypatch.setattr(llm, "ask", lambda *a, **k: ask)
    monkeypatch.setattr(llm, "chat", chat)
    monkeypatch.setattr(cstate, "get_manager", lambda: cstate.ConversationStateManager(cstate.MemoryStore()))


SKILLS = {"fileops": SimpleNamespace(status="ok", manifest={"tier": "T1"}),
          "web_search": SimpleNamespace(status="ok", manifest={"tier": "T0"})}


def test_matched_tool_runs_when_the_model_answers_in_prose(monkeypatch):
    calls = []
    _stub(monkeypatch, "fileops", lambda *a, **k: SimpleNamespace(content="I can't delete files."))
    monkeypatch.setattr(pipeline, "_run_tool", lambda skills, name, text, *a: calls.append((name, text)) or
                        {"reply": "Move these 3 files to the Recycle Bin? (yes/no)", "confirm_required": True})
    out = pipeline.run("delete them", {"id": "s", "history": []}, SKILLS)
    assert calls == [("fileops", "delete them")] and "Recycle Bin" in out["reply"]


def test_same_tool_failing_verification_is_not_retried_forever(monkeypatch):
    calls = []
    _stub(monkeypatch, None, lambda *a, **k: SimpleNamespace(content='{"tool":"web_search","input":"x"}'),
          ask='{"ok": false, "missing": "nothing useful"}')
    monkeypatch.setattr(pipeline, "_run_tool", lambda skills, name, text, *a: calls.append(name) or {"reply": "meh"})
    pipeline.run("some question", {"id": "s", "history": []}, SKILLS)
    assert len(calls) == pipeline.MAX_SAME_TOOL_FAILS
