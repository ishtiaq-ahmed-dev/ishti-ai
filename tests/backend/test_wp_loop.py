"""WP2 research gate, WP3 decision loop, WP7 agent bus - offline with stubs."""
import os
import sys
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from ishti import run_executor, runs  # noqa: E402

# The tests/backend conftest stubs launch(); the WP2 gate tests here need the REAL executor
# with a stubbed dispatcher.
_REAL_LAUNCH = run_executor.launch


@pytest.fixture(autouse=True)
def _offline(tmp_path, monkeypatch):
    monkeypatch.setattr(runs, "DB_PATH_OVERRIDE", str(tmp_path / "runs.db"))
    runs._conn = None
    runs.set_listener(None)

    def _instant(_run_id):
        run = runs.get_run(_run_id)
        if run and run["state"] not in ("done", "failed", "cancelled"):
            runs.finish_run(_run_id, "[stub answer]")

    monkeypatch.setattr(run_executor, "launch", _instant)
    yield


@pytest.fixture()
def real_executor(monkeypatch):
    monkeypatch.setattr(run_executor, "launch", _REAL_LAUNCH)


def _make_run(runs):
    ch = runs.new_chat()
    msg = runs.add_user_message(ch["chat_id"], "research x", client_msg_id="c1")
    return runs.start_run(ch["chat_id"], msg["message"]["message_id"])


# ---------------------------------------------------------------- WP2 gate

def test_research_gate_holds_early_answer(monkeypatch, real_executor):
    run = _make_run(runs)

    def flow(text, sid, channel="text", on_token=None, on_event=None):
        on_event({"type": "research_state", "state": "SEARCHING"})
        on_event({"type": "research_state", "state": "RESEARCHING"})
        return {"final_answer": "Here is the answer probably."}

    monkeypatch.setattr("ishti.dispatcher.handle", flow)
    run_executor.launch(run["run_id"]).join(timeout=30)
    final = runs.get_run(run["run_id"])
    assert final["state"] == "done"
    assert "could not finish verifying" in final["text"]  # early answer replaced, not shipped
    kinds = [e["data"]["kind"] for e in runs.events_since(0) if e["type"] == "run_step"]
    assert "verify" in kinds


def test_research_gate_passes_complete(monkeypatch, real_executor):
    run = _make_run(runs)

    def flow(text, sid, channel="text", on_token=None, on_event=None):
        on_event({"type": "research_state", "state": "SEARCHING"})
        on_event({"type": "research_state", "state": "COMPLET: 2 independent sources"})
        return {"final_answer": "Verified answer with sources."}

    monkeypatch.setattr("ishti.dispatcher.handle", flow)
    run_executor.launch(run["run_id"]).join(timeout=30)
    final = runs.get_run(run["run_id"])
    assert final["text"] == "Verified answer with sources."  # complete research: answer ships as-is


def test_no_research_states_untouched(monkeypatch, real_executor):
    """Local tasks (files, notes) run no research state machine: the gate must not touch them."""
    run = _make_run(runs)

    def flow(text, sid, channel="text", on_token=None, on_event=None):
        on_event({"type": "tool", "label": "reading Downloads"})
        return {"final_answer": "3 files."}

    monkeypatch.setattr("ishti.dispatcher.handle", flow)
    run_executor.launch(run["run_id"]).join(timeout=30)
    assert runs.get_run(run["run_id"])["text"] == "3 files."


# ---------------------------------------------------------------- WP3 decision loop
