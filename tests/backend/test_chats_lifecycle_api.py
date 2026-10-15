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


def test_business_profile_is_refused_and_personal_chats_keep_their_contract():
    """Personal is the only profile: switching to business is refused, and a chat stays on its own history."""
    client = _client()
    personal = client.post("/api/chats", json={}).json()
    client.post(f"/api/chats/{personal['chat_id']}/messages",
                json={"client_msg_id": "personal-1", "text": "private personal text"})
    assert client.put("/api/profile", json={"active": "business"}).status_code == 400
    assert client.get("/api/profile").json()["active"] == "personal"
    assert [c["chat_id"] for c in client.get("/api/chats").json()["chats"]] == [personal["chat_id"]]
    assert client.get(f"/api/chats/{personal['chat_id']}/messages").status_code == 200
