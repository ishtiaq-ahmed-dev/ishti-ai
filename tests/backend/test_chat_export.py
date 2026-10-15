"""The chat export is the owner's own history as Markdown: in order, both sides, and only for a chat the profile can see."""
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ishti import contract_api, profiles


def _client() -> TestClient:
    profiles.set_active("personal")
    app = FastAPI()
    contract_api.attach(app)
    return TestClient(app)


def test_chat_exports_as_markdown_in_order_and_unknown_chat_is_404():
    client = _client()
    chat = client.post("/api/chats", json={}).json()
    client.patch(f"/api/chats/{chat['chat_id']}", json={"title": "Export test"})
    client.post(f"/api/chats/{chat['chat_id']}/messages", json={"client_msg_id": "e1", "text": "first question"})
    res = client.get(f"/api/chats/{chat['chat_id']}/export")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/markdown")
    body = res.text
    assert body.startswith("# Export test")
    assert "**You:** first question" in body
    assert body.index("**You:** first question") > body.index("# Export test")
    assert client.get("/api/chats/does-not-exist/export").status_code == 404
