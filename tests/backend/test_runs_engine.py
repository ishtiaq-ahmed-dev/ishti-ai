"""Durable run engine contract tests (docs/API_CONTRACT.md §14, WP1).

Offline, synthetic data only (ISHTI_TEST_MODE=1 conftest pattern). Each test gets a fresh runs DB
via the fixture; nothing touches the owner's real stores.
"""
import json
import os
import sys
import threading
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from ishti import profiles, prompt_compiler, runs  # noqa: E402


@pytest.fixture()
def fresh_runs(tmp_path, monkeypatch):
    monkeypatch.setattr(runs, "DB_PATH_OVERRIDE", str(tmp_path / "runs.db"))
    runs._conn = None
    profiles.set_active("personal")
    yield runs


def test_persist_before_generate_and_replay(fresh_runs):
    ch = runs.new_chat()
    m = runs.add_user_message(ch["chat_id"], "what changed in my downloads?", client_msg_id="c-1")
    # the message exists BEFORE any run started
    assert runs.messages_after(ch["chat_id"], 0)[0]["text"].startswith("what changed")
    run = runs.start_run(ch["chat_id"], m["message"]["message_id"])
    runs.run_step(run["run_id"], "research", "listing folder", sources=["file://downloads"])
    runs.run_step(run["run_id"], "verify", "2 independent checks")
    runs.finish_run(run["run_id"], "3 files changed")
    evs = runs.events_since(0)
    # full replay from zero, no gaps, no duplicates
    assert [e["seq"] for e in evs] == list(range(1, len(evs) + 1))
    kinds = [e["type"] for e in evs]
    assert kinds.count("message_created") == 2  # user + assistant
    assert kinds[:2] == ["message_created", "run_started"]  # user message first
    fin = next(e for e in evs if e["type"] == "run_finished")
    assert fin["data"]["text"] == "3 files changed"
    # reconnect after "WS drop": replay from any point returns exactly the suffix
    mid = evs[2]["seq"]
    assert [e["seq"] for e in runs.events_since(mid)] == list(range(mid + 1, len(evs) + 1))
    msgs = runs.messages_after(ch["chat_id"], 0)
    assert [x["role"] for x in msgs] == ["user", "assistant"]
    assert msgs[1]["run_id"] == run["run_id"]


def test_send_idempotent_on_client_msg_id(fresh_runs):
    ch = runs.new_chat()
    a = runs.add_user_message(ch["chat_id"], "hello", client_msg_id="same")
    b = runs.add_user_message(ch["chat_id"], "hello", client_msg_id="same")
    assert a["duplicate"] is False and b["duplicate"] is True
    assert a["message"]["message_id"] == b["message"]["message_id"]
    assert len(runs.messages_after(ch["chat_id"], 0)) == 1


def test_run_steps_persisted_and_rebuildable(fresh_runs):
    ch = runs.new_chat()
    m = runs.add_user_message(ch["chat_id"], "q", client_msg_id="c")
    run = runs.start_run(ch["chat_id"], m["message"]["message_id"])
    runs.run_step(run["run_id"], "research", "s1", sources=["https://a", "https://b"])
    runs.run_step(run["run_id"], "tool", "t1")
    stored = runs.get_run(run["run_id"])
    assert [s["kind"] for s in stored["steps"]] == ["research", "tool"]
    assert stored["steps"][0]["sources"] == ["https://a", "https://b"]
    step_events = [e for e in runs.events_since(0) if e["type"] == "run_step"]
    assert [(e["data"]["kind"], e["data"]["label"]) for e in step_events] == \
        [("research", "s1"), ("tool", "t1")]


def test_finish_is_idempotent(fresh_runs):
    ch = runs.new_chat()
    m = runs.add_user_message(ch["chat_id"], "q", client_msg_id="c")
    run = runs.start_run(ch["chat_id"], m["message"]["message_id"])
    runs.finish_run(run["run_id"], "first")
    again = runs.finish_run(run["run_id"], "second")
    assert again["text"] == "first"
    assert len([e for e in runs.events_since(0) if e["type"] == "run_finished"]) == 1


def test_cancel_run(fresh_runs):
    ch = runs.new_chat()
    m = runs.add_user_message(ch["chat_id"], "long task", client_msg_id="c")
    run = runs.start_run(ch["chat_id"], m["message"]["message_id"])
    out = runs.cancel_run(run["run_id"])
    assert out["cancelled"] is True
    assert runs.get_run(run["run_id"])["state"] == "cancelled"
    assert runs.cancel_run(run["run_id"])["cancelled"] is False  # terminal stays terminal


def test_restart_recovery_marks_interrupted_failed(fresh_runs):
    ch = runs.new_chat()
    m = runs.add_user_message(ch["chat_id"], "q", client_msg_id="c")
    run = runs.start_run(ch["chat_id"], m["message"]["message_id"])  # left 'queued' = crash
    stale = fresh_runs.recover_interrupted()
    assert [r["run_id"] for r in stale] == [run["run_id"]]
    row = runs.get_run(run["run_id"])
    assert row["state"] == "failed" and "restart" in row["error"]
    assert any(e["type"] == "run_failed" for e in runs.events_since(0))
    assert fresh_runs.recover_interrupted() == []  # second boot: nothing to do


def test_feedback_idempotent_and_stats(fresh_runs):
    ch = runs.new_chat()
    m = runs.add_user_message(ch["chat_id"], "q", client_msg_id="c")
    run = runs.start_run(ch["chat_id"], m["message"]["message_id"])
    runs.finish_run(run["run_id"], "ans")
    msgs = runs.messages_after(ch["chat_id"], 0)
    aid = msgs[1]["message_id"]
    r1 = runs.add_feedback(aid, run["run_id"], -1, reasons=["wrong"], comment="no")
    assert r1["state"] == "stored"
    r2 = runs.add_feedback(aid, run["run_id"], -1)
    assert r2["state"] == "duplicate" and r2["feedback_id"] == r1["feedback_id"]
    stats = runs.feedback_stats()
    assert stats["neg"] == 1 and stats["total"] == 1
    with pytest.raises(ValueError):
        runs.add_feedback(aid, run["run_id"], 7)


def test_learning_mode_and_proposals(fresh_runs):
    assert runs.learning_mode() == "propose"
    assert runs.set_learning_mode("auto_t0") == "auto_t0"
    with pytest.raises(ValueError):
        runs.set_learning_mode("auto_t9")
    p = runs.add_proposal("auto csv summary", skill="csv", origin="trajectory")
    assert runs.list_proposals("open")[0]["id"] == p["id"]
    assert runs.resolve_proposal(p["id"], approve=True)["status"] == "approved"
    assert runs.list_proposals("open") == []


def test_agent_bus_publish_and_replay(fresh_runs):
    runs.agent_message("researcher", "orchestrator", "find sources")
    runs.agent_status("researcher", "running", "step 1")
    evs = [e for e in runs.events_since(0)
           if e["type"] in ("agent_message", "agent_status")]
    assert evs[0]["data"]["text"] == "find sources"
    assert evs[1]["data"]["state"] == "running"
    assert runs.AGENT_MESSAGES["researcher"][0]["from"] == "orchestrator"


def test_profile_isolation_and_prompt_layers(fresh_runs):
    ch = runs.new_chat(profile="business")
    assert runs.chat_profile(ch["chat_id"]) == "business"
    assert profiles.memory_ns("business") != profiles.memory_ns("personal")
    core = prompt_compiler.CORE
    a = prompt_compiler.build("business", dynamic="d1")
    b = prompt_compiler.build("business", dynamic="d2")
    assert a.split("CONTEXT")[0] == b.split("CONTEXT")[0]  # byte-stable prefix
    assert core in a and "CRM" in a and "Warm, casual tone" not in a
    lay = prompt_compiler.layers("personal")
    assert [l["name"] for l in lay["layers"]][:2] == ["core", "profile:personal"]


def test_second_assistant_message_finishes(fresh_runs):
    """Regression: UNIQUE(chat_id, client_msg_id) + client_msg_id='' rejected the SECOND
    assistant row of a chat, silently dropping message_created + run_finished (state was
    already 'done' so fail_run was a no-op). The fix stores NULL for assistant rows."""
    ch = runs.new_chat()
    for i, text in enumerate(("first question", "second question")):
        msg = runs.add_user_message(ch["chat_id"], text, client_msg_id=f"c{i}")
        run = runs.start_run(ch["chat_id"], msg["message"]["message_id"])
        out = runs.finish_run(run["run_id"], f"answer {i}")
        assert out["state"] == "done"
    evs = [e["type"] for e in runs.events_since(0)]
    assert evs.count("message_created") == 4  # 2 user + 2 assistant
    assert evs.count("run_finished") == 2
    msgs = runs.messages_after(ch["chat_id"], 0, limit=100)
    assert [m["role"] for m in msgs] == ["user", "assistant", "user", "assistant"]
