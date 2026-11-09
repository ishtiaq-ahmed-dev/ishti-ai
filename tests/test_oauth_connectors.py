"""Offline OAuth integration regressions; scratch secrets and loopback callbacks only."""
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

import pytest

from ishti import oauth, connectors


@pytest.fixture(autouse=True)
def isolated_oauth(tmp_path, monkeypatch):
    import ishti
    from ishti.core import vault
    from cryptography.fernet import Fernet
    monkeypatch.setattr(oauth, "_dir", lambda: str(tmp_path))
    import copy
    monkeypatch.setattr(ishti, "CONFIG", {**copy.deepcopy(ishti.CONFIG), "oauth": {}, "oauth_apps": {}})
    key = Fernet.generate_key()
    monkeypatch.setattr(vault, "get_or_create_key", lambda: key)
    monkeypatch.setattr(connectors, "_PROGRESS", {})
    monkeypatch.setattr(connectors, "_CANCEL", {})
    monkeypatch.setattr(oauth, "_who", lambda *_args: "test@example.invalid")
    for name in oauth.PROVIDERS:
        for suffix in ("CLIENT_ID", "CLIENT_SECRET"):
            monkeypatch.delenv(f"ISHTI_OAUTH_{name.upper()}_{suffix}", raising=False)
    monkeypatch.delenv("ISHTI_OAUTH_GOOGLE_CLIENT_ID", raising=False)
    monkeypatch.delenv("ISHTI_OAUTH_GOOGLE_CLIENT_SECRET", raising=False)


def test_shared_google_app_supports_one_click_for_all_existing_google_services(monkeypatch):
    monkeypatch.setenv("ISHTI_OAUTH_GOOGLE_CLIENT_ID", "synthetic-desktop-client")
    monkeypatch.setenv("ISHTI_OAUTH_GOOGLE_CLIENT_SECRET", "synthetic-secret")
    for service in oauth.GOOGLE_SERVICES:
        assert oauth.config(service) == ("synthetic-desktop-client", "synthetic-secret")
        assert connectors.entry(service)["one_click"]


def test_credentials_and_tokens_are_encrypted_and_not_written_to_config(tmp_path):
    import ishti
    oauth.set_credentials("gmail", "test-client", "test-client-secret")
    oauth._save("gmail", {"access_token": "test-access-token", "refresh_token": "test-refresh-token"})
    assert ishti.CONFIG["oauth"] == {}
    assert oauth.config("drive") == ("test-client", "test-client-secret")
    assert oauth._load("gmail")["access_token"] == "test-access-token"
    for path in tmp_path.glob("*.json"):
        raw = path.read_text()
        assert "test-client-secret" not in raw and "test-access-token" not in raw
        assert json.loads(raw)["format"] == "ishti-oauth-v1"
    assert "test-client-secret" not in json.dumps(connectors.entry("gmail"))


def test_expired_nonrefreshable_token_is_not_connected_or_returned():
    oauth._save("github", {"access_token": "expired", "expires_at": time.time() - 5})
    assert not oauth.connected("github")
    assert oauth.token("github") is None


def test_refresh_rotates_refresh_token_and_is_serialized(monkeypatch):
    oauth._save("microsoft", {"access_token": "old", "refresh_token": "old-refresh", "expires_at": 1})
    calls = []

    def post(_url, params):
        calls.append(params)
        time.sleep(.02)
        return {"access_token": "new", "refresh_token": "rotated", "expires_in": 3600}

    monkeypatch.setattr(oauth, "_post", post)
    results = []
    workers = [threading.Thread(target=lambda: results.append(oauth.token("microsoft"))) for _ in range(4)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(2)
    assert results == ["new"] * 4 and len(calls) == 1
    assert oauth._load("microsoft")["refresh_token"] == "rotated"


def test_revoked_grant_updates_connection_state_without_echoing_secrets(monkeypatch):
    oauth._save("gmail", {"access_token": "old", "refresh_token": "secret", "expires_at": 1})
    monkeypatch.setattr(oauth, "_post", lambda *_args: {"error": "invalid_grant"})
    assert oauth.token("gmail") is None
    assert not oauth.connected("gmail")
    assert connectors.entry("gmail")["state"] == "reconnect"


def _callback(auth_url, valid=True):
    auth = urllib.parse.parse_qs(urllib.parse.urlsplit(auth_url).query)
    url = auth["redirect_uri"][0] + "?" + urllib.parse.urlencode({
        "code": "synthetic-code", "state": auth["state"][0] if valid else "wrong-state"})
    with urllib.request.urlopen(url, timeout=3) as response:
        assert "Authorization received" in response.read().decode()
    return auth


def test_microsoft_public_client_callback_pkce_and_provider_parameters(monkeypatch):
    oauth.set_credentials("microsoft", "public-client")
    captured = {}

    def browser(url):
        captured.update(_callback(url))

    def exchange(_url, params):
        assert "client_secret" not in params
        assert oauth._challenge(params["code_verifier"]) == captured["code_challenge"][0]
        return {"access_token": "synthetic-access", "expires_in": 3600}

    monkeypatch.setattr(oauth, "_post", exchange)
    result = oauth.connect("microsoft", open_browser=browser, timeout=2)
    assert result["ok"] and oauth.connected("microsoft")
    assert "access_type" not in captured and "include_granted_scopes" not in captured


def test_wrong_callback_state_never_exchanges_code(monkeypatch):
    oauth.set_credentials("gmail", "test-client", "test-secret")
    calls = []
    monkeypatch.setattr(oauth, "_post", lambda *_args: calls.append("exchange"))

    def browser(url):
        with pytest.raises(urllib.error.HTTPError) as error:
            _callback(url, valid=False)
        assert error.value.code == 400

    result = oauth.connect("gmail", open_browser=browser, timeout=.1)
    assert not result["ok"] and calls == [] and not oauth.connected("gmail")


def test_disconnect_does_not_claim_provider_revocation_when_it_failed(monkeypatch):
    oauth._save("gmail", {"access_token": "synthetic"})

    def fail(*_args):
        raise oauth.OAuthError("network")

    monkeypatch.setattr(oauth, "_post", fail)
    reply = oauth.disconnect("gmail")
    assert "revoked the grant" not in reply and not oauth.connected("gmail")


def test_missing_registration_returns_clear_action_and_no_background_flow():
    result = connectors.start("gmail")
    assert not result["ok"] and "registration" in result["reply"]
    assert connectors.progress("gmail")["state"] == "idle"


def test_disconnecting_cancels_inflight_signin_without_late_state_overwrite(monkeypatch):
    oauth.set_credentials("gmail", "test-client", "test-secret")
    started, finished = threading.Event(), threading.Event()

    def connect(_service, timeout, cancel):
        started.set()
        cancel.wait(3)
        finished.set()
        return {"ok": False, "reply": "cancelled"}

    monkeypatch.setattr(oauth, "connect", connect)
    assert connectors.start("gmail")["waiting"]
    assert started.wait(2)
    assert connectors.start("gmail")["waiting"]
    connectors.disconnect("gmail")
    assert finished.wait(2)
    assert connectors.progress("gmail")["state"] == "idle"


def test_legacy_secret_redaction_occurs_before_audit_truncation(tmp_path, monkeypatch):
    from ishti import audit
    monkeypatch.setattr(audit, "DB", str(tmp_path / "audit.sqlite3"))
    secret = "synthetic-very-long-client-secret"
    prompt = "x" * 180 + " client-secret " + secret
    values = connectors.credential_secrets(prompt)
    assert values == [secret]
    with audit.context(request=prompt, redact=values):
        audit.record(prompt, prompt)
    rows = audit.query()
    for column in rows[0]:
        assert "synthetic-very" not in str(column)


def test_read_from_sender_is_not_swallowed_by_generic_inbox_path(monkeypatch):
    from ishti.skills.email import skill as email
    from ishti import gmail_api
    oauth._save("gmail", {"access_token": "synthetic"})
    monkeypatch.setattr(gmail_api, "unread", lambda *_args: pytest.fail("Specific sender request must not use generic unread listing"))
    monkeypatch.setattr(gmail_api, "search", lambda *_args: [{"id": "fixture-message"}])
    monkeypatch.setattr(gmail_api, "read", lambda *_args: {"subject": "Invoice", "from": "Ali", "date": "today", "body": "fixture invoice"})
    from ishti import llm
    monkeypatch.setattr(llm, "ask", lambda prompt, **_k: "summary of: " + prompt[prompt.find("fixture"):][:15])
    result = email.run("Read the latest email from Ali", {})
    assert "Invoice" in result["reply"] and "fixture invoice" in result["reply"]


def test_provider_hosts_are_allowed_under_the_real_connector_scope(monkeypatch):
    from ishti import audit, privacy, scope
    monkeypatch.setattr(privacy, "internet", lambda: True)
    scope._scopes.pop("connectors", None)
    scope._scopes.pop("email", None)
    with audit.context(skill="connectors"):
        for host in ("github.com", "api.github.com", "oauth2.googleapis.com", "gmail.googleapis.com", "graph.microsoft.com", "www.linkedin.com"):
            assert privacy.decide(host)[0], host
            scope.check_url("https://" + host + "/fixture")
        assert not privacy.decide("example.invalid")[0]
    with audit.context(skill="email"):
        assert privacy.decide("gmail.googleapis.com")[0]
        assert not privacy.decide("example.invalid")[0]


def test_connector_api_errors_are_visible_and_credentials_do_not_echo(monkeypatch):
    from fastapi.testclient import TestClient
    from interface.web_ui import server
    client = TestClient(server.app, base_url="http://127.0.0.1", client=("127.0.0.1", 50000))
    response = client.post("/api/connectors/gmail/connect")
    assert response.status_code == 400 and "registration" in response.json()["reply"]
    response = client.post("/api/connectors/gmail/credentials", json=[])
    assert response.status_code == 400
    response = client.post("/api/connectors/gmail/credentials", json={"client_id": "synthetic-client", "client_secret": "synthetic-secret"})
    assert response.status_code == 200 and "synthetic-secret" not in response.text
    assert client.get("/api/connectors/gmail/progress").json()["connector"]["one_click"]
    assert client.get("/api/connectors/unknown/progress").status_code == 404


def test_websocket_delivers_connector_state_without_refresh(monkeypatch):
    from fastapi.testclient import TestClient
    from interface.web_ui import server
    from ishti import events
    monkeypatch.setattr(events, "replay", lambda: [])
    client = TestClient(server.app, base_url="http://127.0.0.1", client=("127.0.0.1", 50000))
    with client.websocket_connect("ws://127.0.0.1/ws") as socket:
        assert socket.receive_json()["type"] == "connected"
        events.publish("connectors_updated", {"service": "gmail"})
        message = socket.receive_json()
        assert message["type"] == "connectors_updated" and message["data"]["service"] == "gmail"
