"""WS /ws/events?since_seq=N replay over the durable event log (contract v2 §14.3, WP1)."""
import os
import sys
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from fastapi import FastAPI          # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from ishti import contract_api, lifecycle_api, profiles, runs  # noqa: E402


def _settle(client, run_id: str, timeout_s: float = 60.0) -> str:
    """Wait until the run is terminal so the event log stops moving (replay tests are exact)."""
    deadline = time.time() + timeout_s
    state = None
    while time.time() < deadline:
        state = client.get(f"/api/runs/{run_id}").json().get("state")
        if state in ("done", "failed", "cancelled"):
            return state
        time.sleep(0.2)
    raise AssertionError(f"run {run_id} did not settle (state={state})")


@pytest.fixture()
def app_client(tmp_path, monkeypatch):
    monkeypatch.setattr(runs, "DB_PATH_OVERRIDE", str(tmp_path / "runs.db"))
    runs._conn = None
    runs.set_listener(None)
    profiles.set_active("personal")
    _allow_testclient_ws(monkeypatch)
    app = FastAPI()
    contract_api.attach(app)
    lifecycle_api.attach(app)  # wires runs.set_listener -> events.publish_raw (live path)
    return TestClient(app)


def test_events_replay_over_http_twin(app_client):
    ch = app_client.post("/api/chats", json={}).json()
    sent = app_client.post(f"/api/chats/{ch['chat_id']}/messages",
                           json={"client_msg_id": "m1", "text": "replay me"}).json()
    _settle(app_client, sent["run_id"])
    full = app_client.get("/api/events", params={"since_seq": 0}).json()
    assert full["last_seq"] >= 3
    # reconnect after a "drop": only the suffix, exactly once, in order
    cut = full["events"][1]["seq"]
    tail = app_client.get("/api/events", params={"since_seq": cut}).json()["events"]
    assert [e["seq"] for e in tail] == list(range(cut + 1, full["last_seq"] + 1))


def _allow_testclient_ws(monkeypatch):
    """ws_events enforces the production auth (loopback/token) whenever interface.web_ui.server is
    imported in the process (i.e. in full-suite runs, but not standalone). TestClient reports a
    client host of "testclient", not loopback, so the production loopback trust cannot see it.
    Replay is the subject here - auth itself has dedicated security tests - so stub it explicitly
    instead of depending on which modules another test happened to import."""
    try:
        from interface.web_ui import server as ui_server
    except Exception:
        return
    monkeypatch.setattr(ui_server, "authorized", lambda conn: True)


def test_ws_since_seq_replay_matches_log(app_client, monkeypatch):
    """True websocket replay: connect with since_seq, receive exactly the suffix."""
    app = FastAPI()
    lifecycle_api.attach(app)
    contract_api.attach(app)
    client = TestClient(app)
    ch = client.post("/api/chats", json={}).json()
    sent = client.post(f"/api/chats/{ch['chat_id']}/messages",
                       json={"client_msg_id": "m1", "text": "ws replay"}).json()
    _settle(client, sent["run_id"])
    log = runs.events_since(0)
    assert len(log) >= 3
    cut = log[0]["seq"]
    expected = [e["seq"] for e in runs.events_since(cut)]
    with client.websocket_connect(f"/ws/events?since_seq={cut}") as ws:
        got = []
        for _ in expected:
            got.append(ws.receive_json())
    assert [m["seq"] for m in got] == expected
    types = {m["type"] for m in got}
    assert {"message_created", "run_started", "run_finished"} <= types


def test_listener_fanout(app_client):
    """runs.publish must push to the live listener AND persist (once, in order)."""
    seen = []
    runs.set_listener(seen.append)
    ch = runs.new_chat()
    runs.add_user_message(ch["chat_id"], "fanout", client_msg_id="f1")
    runs.set_listener(None)
    assert [m["type"] for m in seen] == ["message_created"]
    assert seen[0]["seq"] == runs.last_seq()
