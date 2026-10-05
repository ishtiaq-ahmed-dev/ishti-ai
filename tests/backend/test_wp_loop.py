"""WP2 research gate, WP3 decision loop, WP7 agent bus - offline with stubs."""
import os
import sys
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from ishti import agent_bus, decision_loop, run_executor, runs  # noqa: E402

# The tests/backend conftest stubs launch(); the WP2 gate tests here need the REAL executor
# with a stubbed dispatcher.
_REAL_LAUNCH = run_executor.launch


@pytest.fixture(autouse=True)
def _offline(tmp_path, monkeypatch):
    monkeypatch.setattr(runs, "DB_PATH_OVERRIDE", str(tmp_path / "runs.db"))
    monkeypatch.setattr(agent_bus, "BB_DB_OVERRIDE", str(tmp_path / "bb.db"))
    runs._conn = None
    agent_bus._bb = None
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

def test_decision_loop_first_pass_and_low_confidence():
    def act(step):
        return {"reply": "There are 3 files (tool:list).", "evidence": [{"ref": "tool:list"}]}

    out = decision_loop.run("list downloads", act, critic_fn=lambda d, e: {
        "supported": [{"claim": "3 files", "evidence": "tool:list"}], "unsupported": [],
        "issues": [], "verdict": "pass"})
    assert out["verdict"] == "pass" and out["confidence"] >= 0.7 and out["iterations"] == 1


def test_decision_loop_revises_then_reports_limit():
    calls = {"n": 0}

    def act(step):
        calls["n"] += 1
        return {"reply": "It is definitely so.", "evidence": []}

    out = decision_loop.run("q", act, critic_fn=lambda d, e: {
        "supported": [], "unsupported": ["the claim"], "issues": ["no evidence"],
        "verdict": "needs_revision"}, max_iterations=2)
    assert calls["n"] == 2 and out["stop_reason"].startswith("iteration limit")
    assert "Stopped early" in out["reply"] and out["confidence"] < 0.75


def test_decision_loop_contradiction_flagged():
    flags = decision_loop.contradiction_check("The shop is not open now.", ["The shop is open"])
    assert flags and flags[0].startswith("The shop")
    out = decision_loop.run("q", lambda s: {"reply": "The shop is not open now.", "evidence": [
        {"ref": "tool:check"}]}, memories=["The shop is open"],
        critic_fn=lambda d, e: {"supported": [{"claim": "c", "evidence": "tool:check"}],
                                "unsupported": [], "issues": [], "verdict": "pass"})
    assert "conflicts with stored memory" in out["reply"]


def test_decision_loop_act_error_honest():
    def act(step):
        raise RuntimeError("disk full")

    out = decision_loop.run("q", act)
    assert out["stop_reason"] == "act_error" and "Nothing was fabricated" in out["reply"]


# ---------------------------------------------------------------- WP7 agent bus

def test_bus_publish_subscribe_and_dedupe():
    got = []
    agent_bus.subscribe("research.done", got.append)
    assert agent_bus.publish("research.done", {"n": 1}, sender="researcher",
                             type_="result")["ok"] is True
    dup = agent_bus.publish("research.done", {"n": 1}, sender="researcher", type_="result")
    assert dup["ok"] is False and dup["reason"] == "duplicate_suppressed"
    assert len(got) == 1 and got[0]["body"] == {"n": 1}


def test_bus_loop_prevention_depth_and_ttl():
    assert agent_bus.publish("t", {}, sender="a", depth=agent_bus.MAX_DEPTH)["reason"] == \
        "max_depth_reached"
    assert agent_bus.deliver({"ts": time.time() - agent_bus.TTL_S - 1, "depth": 0})["reason"] == \
        "expired"
    assert agent_bus.deliver({"ts": time.time(), "depth": 0})["delivered"] is True


def test_bus_scopes_refuse_out_of_scope():
    out = agent_bus.publish("crm.leak", {}, sender="locked", type_="status",
                            scopes={"locked": ["files."]})
    assert out["ok"] is False and out["reason"] == "scope_refused"
    ok = agent_bus.publish("files.list", {}, sender="locked", type_="status",
                           scopes={"locked": ["files."]})
    assert ok["ok"] is True


def test_bus_bad_type_refused():
    with pytest.raises(ValueError):
        agent_bus.publish("t", {}, sender="a", type_="command")


def test_blackboard_persisted_and_wiped():
    agent_bus.bb_put("k", {"v": [1, 2]}, writer="coder")
    rows = agent_bus.bb_get("k")
    assert rows[0]["value"] == {"v": [1, 2]}
    assert agent_bus.bb_forget_everything() == 1
    assert agent_bus.bb_get("k", []) == []


def test_bus_mirrors_into_durable_log():
    agent_bus.publish("research.done", {"urls": 2}, sender="researcher", type_="result")
    agent_bus.request_input("researcher", "Which client should I draft for?", run_id="r1")
    types = [e["type"] for e in runs.events_since(0)]
    assert "agent_message" in types and "agent_status" in types
    assert types[-1] == "input_required"  # INPUT_REQUIRED surfaced to the owner
    st = runs.AGENT_STATE["researcher"]
    assert st["state"] == "input_required"
