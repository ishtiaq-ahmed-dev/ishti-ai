"""A remote (proxied) owner session can read and stop, but can never change security configuration."""
from fastapi.testclient import TestClient

from ishti import web_security
from tests.backend.test_remote_auth import REMOTE_HEADERS, secured_server  # noqa: F401  (fixture)

LOCAL_ONLY = [("PUT", "/api/remote/policy"), ("POST", "/api/remote/pin"), ("DELETE", "/api/remote/pin"),
              ("POST", "/api/remote/enable"), ("POST", "/api/remote/pair/start"),
              ("PUT", "/api/connectors/whatsapp/config"), ("POST", "/api/connectors/whatsapp/connect")]


def test_requires_local_matches_only_config_writers():
    for method, path in LOCAL_ONLY:
        assert web_security.requires_local(method, path), (method, path)
    for method, path in [("GET", "/api/remote/policy"), ("POST", "/api/remote/revoke"),
                         ("POST", "/api/remote/tunnel/stop"), ("POST", "/api/chats"),
                         ("POST", "/api/connectors/whatsapp/disconnect")]:
        assert not web_security.requires_local(method, path), (method, path)


def test_remote_session_cannot_change_security_config(secured_server):  # noqa: F811
    server, store = secured_server
    sid = store.create(ip="203.0.113.8", device="phone")
    remote = TestClient(server.app, base_url="https://abc.ngrok-free.app", client=("127.0.0.1", 50000),
                        headers={**REMOTE_HEADERS, "cookie": f"ishti_session={sid}"})
    for method, path in LOCAL_ONLY:
        r = remote.request(method, path, json={})
        assert r.status_code == 403, (method, path, r.status_code)
    assert remote.get("/api/chats").status_code == 200  # still a working session
