"""Offline API regressions for durable, profile-isolated chat lifecycle operations."""
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ishti import contract_api, profiles, run_executor, runs


def _client() -> TestClient:
    profiles.set_active("personal")
    app = FastAPI()
    contract_api.attach(app)
    return TestClient(app)


def test_chat_rename_trash_and_token_bound_undo():
    client = _client()
    chat = client.post("/api/chats", json={}).json()

    renamed = client.patch(f"/api/chats/{chat['chat_id']}",
                           json={"title": "  Durable title  "})
    assert renamed.status_code == 200
    assert renamed.json()["title"] == "Durable title"

    deleted = client.delete(f"/api/chats/{chat['chat_id']}")
    assert deleted.status_code == 200
    body = deleted.json()
    assert body["chat_id"] == chat["chat_id"] and body["trashed"] is True
    assert body["undo_token"] and body["undo_seconds"] == 8
    assert client.get("/api/chats").json()["chats"] == []
    assert client.get(f"/api/chats/{chat['chat_id']}/messages").status_code == 404

    wrong = client.patch(f"/api/chats/{chat['chat_id']}",
                         json={"restore": True, "undo_token": "wrong"})
    assert wrong.status_code == 400
    restored = client.patch(f"/api/chats/{chat['chat_id']}",
                            json={"restore": True, "undo_token": body["undo_token"]})
    assert restored.status_code == 200 and restored.json()["restored"] is True
    assert client.get("/api/chats").json()["chats"][0]["title"] == "Durable title"
    assert client.patch(f"/api/chats/{chat['chat_id']}",
                        json={"restore": True, "undo_token": body["undo_token"]}).status_code == 400


def test_personal_and_business_chat_contracts_are_isolated():
    client = _client()
    personal = client.post("/api/chats", json={}).json()
    client.post(f"/api/chats/{personal['chat_id']}/messages",
                json={"client_msg_id": "personal-1", "text": "private personal text"})

    assert client.put("/api/profile", json={"active": "business"}).status_code == 200
    assert client.get("/api/chats").json()["chats"] == []
    assert client.get(f"/api/chats/{personal['chat_id']}/messages").status_code == 404
    assert client.patch(f"/api/chats/{personal['chat_id']}",
                        json={"title": "cross profile"}).status_code == 404
    assert client.delete(f"/api/chats/{personal['chat_id']}").status_code == 404
    assert client.post(f"/api/chats/{personal['chat_id']}/messages",
                       json={"text": "cross profile"}).status_code == 404
    assert client.post("/api/chats", json={"profile": "personal"}).status_code == 403

    business = client.post("/api/chats", json={"profile": "business"}).json()
    assert business["profile"] == "business"
    assert [c["chat_id"] for c in client.get("/api/chats").json()["chats"]] == [business["chat_id"]]
    visible_events = client.get("/api/events", params={"since_seq": 0}).json()["events"]
    assert all("private personal text" not in str(event) for event in visible_events)


def test_inflight_reply_stays_with_original_chat_after_profile_switch(monkeypatch):
    client = _client()
    monkeypatch.setattr(run_executor, "launch", lambda _run_id: None)
    personal = client.post("/api/chats", json={}).json()
    sent = client.post(f"/api/chats/{personal['chat_id']}/messages",
                       json={"client_msg_id": "switch-1", "text": "finish in this chat"}).json()
    runs.run_step(sent["run_id"], "tool", "Using chat")

    client.put("/api/profile", json={"active": "business"})
    business = client.post("/api/chats", json={}).json()
    runs.finish_run(sent["run_id"], "reply bound to personal")
    assert client.get(f"/api/runs/{sent['run_id']}").status_code == 404
    assert client.get(f"/api/chats/{personal['chat_id']}/messages").status_code == 404
    assert client.get(f"/api/chats/{business['chat_id']}/messages").json()["messages"] == []
    assert "reply bound to personal" not in str(client.get("/api/events").json()["events"])

    client.put("/api/profile", json={"active": "personal"})
    messages = client.get(f"/api/chats/{personal['chat_id']}/messages").json()["messages"]
    assert [(m["role"], m["text"]) for m in messages] == [
        ("user", "finish in this chat"), ("assistant", "reply bound to personal")]
    run = client.get(f"/api/runs/{sent['run_id']}").json()
    assert run["steps"][0]["label"] == "Using chat"
    listed = client.get("/api/runs", params={"chat_id": personal["chat_id"]}).json()["runs"]
    assert listed[0]["steps"] == run["steps"] and listed[0]["ended_at"] is not None
