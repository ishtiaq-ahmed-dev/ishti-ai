from __future__ import annotations

import urllib.parse

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from ishti import web_security


REMOTE_HEADERS = {
    "host": "abc.ngrok-free.app",
    "origin": "https://abc.ngrok-free.app",
    "x-forwarded-for": "203.0.113.8",
    "x-forwarded-proto": "https",
    "sec-fetch-site": "same-origin",
}


@pytest.fixture
def secured_server(tmp_path, monkeypatch):
    from interface.web_ui import server
    store = web_security.OwnerSessionStore(tmp_path / "sessions.json")
    guard = web_security.LoginGuard()
    monkeypatch.setattr(server, "API_TOKEN", "owner-test-key")
    monkeypatch.setattr(server, "owner_sessions", store)
    monkeypatch.setattr(server, "login_guard", guard)
    monkeypatch.setattr(web_security, "owner_sessions", store)
    return server, store


def remote_client(server):
    return TestClient(server.app, base_url="https://abc.ngrok-free.app", client=("127.0.0.1", 50000),
                      headers=REMOTE_HEADERS)


def test_proxy_is_remote_and_unauthenticated_api_vs_html(secured_server):
    server, _ = secured_server
    client = remote_client(server)
    assert client.get("/api/chats").status_code == 401
    page = client.get("/", follow_redirects=False)
    assert page.status_code == 303 and page.headers["location"] == "/login"
    assert client.get("/login").status_code == 200


def test_five_wrong_keys_lock_out_the_forwarded_ip(secured_server):
    server, _ = secured_server
    client = remote_client(server)
    codes = [client.post("/api/remote/login", json={"access_key": "wrong"}).status_code for _ in range(5)]
    assert codes == [401, 401, 401, 401, 429]
    locked = client.post("/api/remote/login", json={"access_key": "owner-test-key"})
    assert locked.status_code == 429 and locked.json()["retry_after_s"] > 0


def test_magic_link_is_single_use_and_creates_secure_session(secured_server):
    server, _ = secured_server
    local = TestClient(server.app, base_url="http://127.0.0.1:8888", client=("127.0.0.1", 50001))
    generated = local.post("/api/remote/magic-link").json()
    code = urllib.parse.parse_qs(urllib.parse.urlsplit(generated["url"]).query)["code"][0]
    remote = remote_client(server)
    first = remote.get(f"/login?code={code}", follow_redirects=False)
    assert first.status_code == 303 and "ishti_session=" in first.headers["set-cookie"]
    assert "HttpOnly" in first.headers["set-cookie"] and "Secure" in first.headers["set-cookie"]
    assert "SameSite=lax" in first.headers["set-cookie"]
    second = remote_client(server).get(f"/login?code={code}", follow_redirects=False)
    assert second.status_code == 200 and "set-cookie" not in second.headers


def test_session_cookie_authenticates_chats_and_websocket_then_revocation_kills_it(secured_server):
    server, _ = secured_server
    client = remote_client(server)
    login = client.post("/api/remote/login", json={"access_key": "owner-test-key", "device_label": "Test phone"})
    assert login.status_code == 200
    assert client.get("/api/chats").status_code == 200
    chat = client.post("/api/chats", json={}).json()
    messages = client.get(f"/api/chats/{chat['chat_id']}/messages")
    assert messages.status_code == 200 and messages.json()["chat_id"] == chat["chat_id"]
    session = client.cookies.get("ishti_session")
    with client.websocket_connect("/ws/events", headers={**REMOTE_HEADERS, "cookie": f"ishti_session={session}"}):
        pass
    sessions = client.get("/api/remote/sessions").json()["sessions"]
    assert sessions and sessions[0]["device"] == "Test phone" and sessions[0]["ip"] == "203.0.113.8"
    assert client.post("/api/remote/sessions/revoke", json={"id": sessions[0]["id"]}).json()["revoked"] is True
    assert client.get("/api/chats").status_code == 401


def test_websocket_without_session_is_rejected_and_loopback_ui_stays_passwordless(secured_server):
    server, _ = secured_server
    remote = remote_client(server)
    with pytest.raises(WebSocketDisconnect) as exc:
        with remote.websocket_connect("/ws/events", headers=REMOTE_HEADERS):
            pass
    assert exc.value.code == 1008
    local = TestClient(server.app, base_url="http://127.0.0.1:8888", client=("127.0.0.1", 50001))
    assert local.get("/api/chats").status_code == 200


def test_remote_log_tail_is_authenticated_capped_and_redacted(secured_server, tmp_path, monkeypatch):
    server, _ = secured_server
    client = remote_client(server)
    assert client.get("/api/remote/logs?source=audit").status_code == 401
    assert client.post("/api/remote/login", json={"access_key": "owner-test-key"}).status_code == 200
    from ishti import audit, lifecycle, wa_bridge
    monkeypatch.setattr(audit, "query", lambda limit: [(1.0, "owner", "remote", "login token=TOPSECRET",
                                                         "phone +923001234567", "", None, "", "whatsapp")])
    backend = tmp_path / "backend.log"; backend.write_text("Bearer TOPSECRET phone +923001234567\n", encoding="utf-8")
    monkeypatch.setattr(wa_bridge, "logs", lambda lines=200: ["authtoken=TOPSECRET 03001234567"])  # WAHA docker logs
    monkeypatch.setattr(lifecycle, "LOG_FILE", str(backend))
    for source in ("audit", "backend", "wa_bridge"):
        result = client.get(f"/api/remote/logs?source={source}&lines=999").json()
        text = "\n".join(result["lines"])
        assert result["capped"] == 500 and "TOPSECRET" not in text and "923001234567" not in text
        assert text.endswith("567")


def test_rotate_key_and_revoke_all_are_local_only(secured_server, tmp_path, monkeypatch):
    server, store = secured_server
    token_path = tmp_path / "api_token"; token_path.write_text("owner-test-key", encoding="utf-8")
    monkeypatch.setattr(server, "TOKEN_PATH", str(token_path))
    remote = remote_client(server)
    assert remote.post("/api/remote/login", json={"access_key": "owner-test-key"}).status_code == 200
    assert remote.post("/api/remote/sessions/revoke-all").status_code == 403
    assert remote.post("/api/remote/sessions/rotate-api-token").status_code == 403
    local = TestClient(server.app, base_url="http://127.0.0.1:8888", client=("127.0.0.1", 50001))
    rotated = local.post("/api/remote/sessions/rotate-api-token").json()
    assert rotated["rotated"] is True and rotated["sessions_revoked"] == 1
    assert token_path.read_text(encoding="utf-8") != "owner-test-key" and store.list() == []
