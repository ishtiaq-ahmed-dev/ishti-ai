"""Run executor paths with a stubbed dispatcher: done, Cancelled, and failure terminalize the run."""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from ishti import run_executor, runs  # noqa: E402

# The tests/backend conftest stubs launch() for the API tests; these tests need the REAL
# executor, restored over the stub.
_REAL_LAUNCH = run_executor.launch


@pytest.fixture(autouse=True)
def _real_launch(monkeypatch):
    monkeypatch.setattr(run_executor, "launch", _REAL_LAUNCH)


@pytest.fixture()
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setattr(runs, "DB_PATH_OVERRIDE", str(tmp_path / "runs.db"))
    runs._conn = None
    runs.set_listener(None)
    yield runs


class _FakeCancelled(Exception):
    pass


def _make_run(runs):
    ch = runs.new_chat()
    msg = runs.add_user_message(ch["chat_id"], "do a thing", client_msg_id="c1")
    return runs.start_run(ch["chat_id"], msg["message"]["message_id"])


def test_executor_done(fresh_db, monkeypatch):
    import ishti.dispatcher as dispatcher
    monkeypatch.setattr(dispatcher, "handle", lambda text, sid, channel="text",
                        on_token=None, on_event=None: {"final_answer": f"answer to: {text}"})
    run = _make_run(fresh_db)
    run_executor.launch(run["run_id"]).join(timeout=30)
    final = fresh_db.get_run(run["run_id"])
    assert final["state"] == "done" and final["text"] == "answer to: do a thing"


def test_executor_cancelled_exception(fresh_db, monkeypatch):
    """A Cancelled escaping the dispatcher (barge-in/kill) maps to the cancel path: terminal,
    visible, never stuck in 'running'."""
    import ishti.dispatcher as dispatcher

    def boom(text, sid, channel="text", on_token=None, on_event=None):
        from ishti.llm import Cancelled
        raise Cancelled("stopped by the kill switch")

    monkeypatch.setattr(dispatcher, "handle", boom)
    run = _make_run(fresh_db)
    run_executor.launch(run["run_id"]).join(timeout=30)
    final = fresh_db.get_run(run["run_id"])
    assert final["state"] == "cancelled"
    ev_types = [e["type"] for e in fresh_db.events_since(0)]
    assert ev_types[-1] in ("run_finished", "run_failed")  # subscribers always see a terminal event


def test_executor_failure(fresh_db, monkeypatch):
    import ishti.dispatcher as dispatcher

    def boom(text, sid, channel="text", on_token=None, on_event=None):
        raise RuntimeError("skill exploded")

    monkeypatch.setattr(dispatcher, "handle", boom)
    run = _make_run(fresh_db)
    run_executor.launch(run["run_id"]).join(timeout=30)
    final = fresh_db.get_run(run["run_id"])
    assert final["state"] == "failed" and "RuntimeError" in final["error"]
    # _FakeCancelled (an unknown exception class) would also land here honestly, never silently
    assert fresh_db.events_since(0)[-1]["type"] == "run_failed"


def test_executor_maps_events_and_cancel_guard(fresh_db, monkeypatch):
    import ishti.dispatcher as dispatcher

    def flow(text, sid, channel="text", on_token=None, on_event=None):
        on_event({"type": "tool", "label": "reading folder"})
        on_event({"type": "research", "label": "searching", "sources": ["https://a"]})
        on_token("partial ")
        fresh_db.cancel_run(run_id_holder["id"])  # cancel mid-run
        on_token("text")
        return {"final_answer": "late answer"}

    monkeypatch.setattr(dispatcher, "handle", flow)
    run = _make_run(fresh_db)
    run_id_holder = {"id": run["run_id"]}
    run_executor.launch(run["run_id"]).join(timeout=30)
    final = fresh_db.get_run(run["run_id"])
    assert final["state"] == "cancelled"  # late answer must NOT overwrite the cancel
    steps = [e["data"]["kind"] for e in fresh_db.events_since(0) if e["type"] == "run_step"]
    assert steps == ["tool", "research"]
