"""Contract v2 HTTP surface tests (docs/API_CONTRACT.md §14) over a fresh ASGI app, no live server."""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from ishti import contract_api, profiles, runs  # noqa: E402


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(runs, "DB_PATH_OVERRIDE", str(tmp_path / "runs.db"))
    runs._conn = None
    profiles.set_active("personal")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    app = FastAPI()
    contract_api.attach(app)
    return TestClient(app)


def test_chats_and_idempotent_send(client):
    ch = client.post("/api/chats", json={}).json()
    assert ch["profile"] == "personal" and ch["chat_id"]
    # a chat without an explicit profile emits no profile_changed (that event = an explicit switch)
    assert client.get("/api/events", params={"since_seq": 0}).json()["events"] == []
    body = {"client_msg_id": "m-1", "text": "summarise my downloads"}
    r1 = client.post(f"/api/chats/{ch['chat_id']}/messages", json=body).json()
    r2 = client.post(f"/api/chats/{ch['chat_id']}/messages", json=body).json()
    assert r1["message_id"] and r1["run_id"] and r1.get("duplicate") is False
    assert r2["duplicate"] is True and r2["message_id"] == r1["message_id"]
    assert r2["run_id"] == r1["run_id"]
    run = client.get(f"/api/runs/{r1['run_id']}").json()
    assert run["state"] in runs.STATES
    msgs = client.get(f"/api/chats/{ch['chat_id']}/messages").json()["messages"]
    assert msgs[0]["role"] == "user"
    # after_seq pagination returns only the suffix
    later = client.get(f"/api/chats/{ch['chat_id']}/messages",
                       params={"after_seq": msgs[0]["seq"]}).json()["messages"]
    assert all(m["seq"] > msgs[0]["seq"] for m in later)
    assert client.get("/api/chats/nope/messages").status_code == 404
    assert client.post("/api/chats", json={"profile": "chaos"}).json()["profile"] == "personal"


def test_run_get_cancel_and_events(client):
    ch = client.post("/api/chats", json={}).json()
    r = client.post(f"/api/chats/{ch['chat_id']}/messages",
                    json={"client_msg_id": "m", "text": "hello there"}).json()
    assert client.get("/api/runs/ghost").status_code == 404
    ev = client.get("/api/events", params={"since_seq": 0}).json()
    types = [e["type"] for e in ev["events"]]
    assert types[0] == "message_created" and "run_started" in types
    assert ev["last_seq"] >= len(ev["events"])
    tail = client.get("/api/events", params={"since_seq": ev["events"][0]["seq"]}).json()
    assert tail["events"][0]["seq"] == ev["events"][1]["seq"]
    # a queued run can be cancelled through the API
    out = client.post(f"/api/runs/{r['run_id']}/cancel").json()
    assert out.get("cancelled") in (True, False) or "state" in out


def test_feedback_and_profile(client):
    fb = client.post("/api/feedback", json={"message_id": "m1", "run_id": "r1", "rating": 1,
                                            "reasons": ["ok"], "comment": "", "correction": ""})
    assert fb.status_code == 200 and fb.json()["state"] in ("stored", "duplicate")
    assert client.post("/api/feedback", json={"message_id": "m1", "run_id": "r1",
                                              "rating": 9}).status_code == 400
    assert client.get("/api/feedback/stats").json()["total"] == 1
    assert client.get("/api/profile").json()["active"] == "personal"
    assert client.put("/api/profile", json={"active": "business"}).json()["active"] == "business"
    assert client.put("/api/profile", json={"active": "chaos"}).status_code == 400
    assert client.put("/api/profile", json={"active": "personal"}).json()["active"] == "personal"


def test_connectors_agents_learning_prompt_layers(client):
    rows = client.get("/api/connectors").json()["connectors"]
    assert isinstance(rows, list)
    assert client.get("/api/connectors/whatsapp/status").json()["id"] == "whatsapp"
    assert client.get("/api/connectors/ghost/status").status_code == 404
    assert client.put("/api/connectors/telegram/config",
                      json={"control_numbers": ["+923001234567"]}).json()["saved"] is True
    assert client.put("/api/connectors/ghost/config", json={}).status_code == 400
    agents = client.get("/api/agents").json()["agents"]
    assert any(a["id"] == "researcher" for a in agents)
    assert client.get("/api/agents/researcher/messages").json()["agent_id"] == "researcher"
    assert client.get("/api/agents/ghost/messages").status_code == 404
    ls = client.get("/api/learning/status").json()
    assert ls["mode"] in ("off", "propose", "auto_t0") and ls["proposals_open"] == 0
    p = runs.add_proposal("demo", skill="demo_skill")
    assert client.get("/api/skills/proposals").json()["proposals"][0]["id"] == p["id"]
    act = client.post(f"/api/skills/proposals/{p['id']}/reject")
    assert act.json()["status"] == "rejected"
    assert client.post("/api/skills/proposals/ghost/approve").status_code == 404
    assert client.post("/api/skills/proposals/ghost/pause").status_code in (400, 404)
    assert client.put("/api/learning/mode", json={"mode": "auto_t0"}).json()["mode"] == "auto_t0"
    assert client.put("/api/learning/mode", json={"mode": "nope"}).status_code == 400
    lay = client.get("/api/prompt/layers").json()
    assert lay["layers"][0]["name"] == "core" and lay["total_chars"] > 0
    assert lay["core_digest"] == client.get("/api/prompt/layers").json()["core_digest"]
