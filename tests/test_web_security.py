"""Dashboard policy and its real middleware wiring; no server startup or account calls."""
import pytest
from starlette.requests import HTTPConnection
from fastapi.testclient import TestClient

from ishti import web_security as security
from ishti.api_sessions import origin_ok


def connection(headers=None, client="127.0.0.1", scheme="http", websocket=False):
    values = {"host": "127.0.0.1:8888", **(headers or {})}
    return HTTPConnection({"type": "websocket" if websocket else "http", "scheme": scheme,
                           "path": "/", "query_string": b"", "client": (client, 12345),
                           "headers": [(k.encode(), v.encode()) for k, v in values.items()]})


@pytest.mark.parametrize("host", ["127.0.0.1:bad", "127.0.0.1:", "user@127.0.0.1:8888",
                                     "127.0.0.1:0", "127.0.0.1:65536", "127.0.0.1/path",
                                     "127.0.0.1,evil.example", "[::1]bad", "127.0.0.1: 8888"])
def test_malformed_host_is_never_same_origin(host):
    assert not origin_ok("http://127.0.0.1:8888", host, None)
    assert not security.authorized(connection({"host": host}), "test-token")


@pytest.mark.parametrize("origin", ["http://user@127.0.0.1:8888", "http://127.0.0.1:8888/",
                                       "http://127.0.0.1:8888?x=1", "http://127.0.0.1:8888#x",
                                       "https://127.0.0.1:8888", "null", "http://evil.example"])
def test_bad_browser_origin_refused_even_with_valid_token(origin):
    assert not security.authorized(connection({"origin": origin, "authorization": "Bearer test-token"}),
                                   "test-token")


@pytest.mark.parametrize("host,origin", [("127.0.0.1:8888", "http://127.0.0.1:8888"),
                                            ("localhost:5173", "http://localhost:5173"),
                                            ("[::1]:8888", "http://[::1]:8888")])
def test_local_ui_and_native_calls_work(host, origin):
    assert security.authorized(connection({"host": host, "origin": origin}), "test-token")
    assert security.authorized(connection({"host": host}), "test-token")


def test_rebinding_host_and_cross_port_do_not_inherit_local_trust():
    assert not security.authorized(connection({"host": "evil.example:8888"}), "test-token")
    assert not security.authorized(connection({"origin": "http://127.0.0.1:9999"}), "test-token")
    for site in ("cross-site", "same-site"):
        assert not security.authorized(connection({"sec-fetch-site": site}), "test-token")


@pytest.mark.parametrize("header", security.PROXY_HEADERS)
def test_every_proxy_header_requires_a_token(header):
    value = "https" if header == "x-forwarded-proto" else "test-proxy"
    conn = connection({header: value})
    assert not security.authorized(conn, "test-token")
    assert security.authorized(connection({header: value, "authorization": "Bearer test-token"}), "test-token")


def test_https_tunnel_and_remote_native_client_remain_supported(tmp_path):
    store = security.OwnerSessionStore(tmp_path / "sessions.json")
    sid = store.create(ip="203.0.113.8", device="test")
    h = {"host": "ishtitest.example", "origin": "https://ishtitest.example",
         "x-forwarded-proto": "https", "cookie": f"ishti_session={sid}"}
    assert security.authorized(connection(h), "test-token", store)
    assert not security.authorized(connection({**h, "origin": "https://evil.example"}), "test-token", store)
    # the retired long-lived-key cookie must NOT authenticate any more
    legacy = {**h, "cookie": "ishti_token=test-token"}
    assert not security.authorized(connection(legacy), "test-token", store)
    assert security.authorized(connection({"authorization": "bearer test-token"}, client="192.0.2.1"), "test-token")
    assert not security.authorized(connection(client="192.0.2.1"), "")
    assert not security.token_matches("", "")
    assert not security.token_matches("invalid-اردو-token", "test-token")


def test_same_policy_applies_to_websockets():
    assert security.authorized(connection({"origin": "http://127.0.0.1:8888"}, scheme="ws", websocket=True), "t")
    assert not security.authorized(connection({"origin": "http://evil.example"}, scheme="ws", websocket=True), "t")


@pytest.fixture
def server(monkeypatch):
    from interface.web_ui import server as module
    monkeypatch.setattr(module, "API_TOKEN", "test-token")
    return module


def test_actual_http_middleware_blocks_before_routes_and_no_wildcard_cors(server):
    # Do not enter TestClient's lifespan: startup launches the owner's background jobs.
    client = TestClient(server.app, base_url="http://127.0.0.1:8888", client=("127.0.0.1", 12345))
    for method in ("get", "post", "options"):
        result = getattr(client, method)("/api/security-probe", headers={"origin": "http://evil.example"})
        assert result.status_code == 403
        assert result.json() == {"error": "foreign origin"}
        assert "access-control-allow-origin" not in result.headers
    assert client.get("/api/security-probe", headers={"host": "evil.example"}).status_code == 401
    result = client.get("/api/security-probe", headers={"origin": "http://127.0.0.1:8888"})
    assert result.status_code == 404
    assert "access-control-allow-origin" not in result.headers


def test_query_token_never_authenticates_or_sets_cookie(server):
    local = TestClient(server.app, base_url="http://127.0.0.1:8888", client=("127.0.0.1", 12345))
    assert "set-cookie" not in local.get("/api/security-probe?token=wrong").headers
    remote = TestClient(server.app, base_url="https://ishtitest.example", client=("192.0.2.1", 12345))
    for token in ("wrong", "test-token"):  # even the REAL key in a URL is refused (leaks via history/referrers)
        result = remote.get(f"/api/security-probe?token={token}", headers={"origin": "https://ishtitest.example"})
        assert result.status_code == 401 and "set-cookie" not in result.headers
    # native clients keep the Bearer path
    ok = remote.get("/api/security-probe", headers={"origin": "https://ishtitest.example",
                                                    "authorization": "Bearer test-token"})
    assert ok.status_code == 404


@pytest.mark.parametrize("path", ["/ws", "/ws/events", "/ws/live"])
def test_actual_sockets_reject_foreign_origin_before_accept(server, path):
    from starlette.websockets import WebSocketDisconnect
    client = TestClient(server.app, base_url="http://127.0.0.1:8888", client=("127.0.0.1", 12345))
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect(path, headers={"origin": "http://evil.example"}):
            pytest.fail("foreign socket was accepted")
    assert exc.value.code == 1008


def test_lifecycle_origin_check_preserves_ipv6_and_refuses_cross_port():
    from ishti.lifecycle_api import foreign_web_page
    conn = connection({"host": "[::1]:8888", "origin": "http://[::1]:8888"}, client="::1")
    assert not foreign_web_page(conn.scope)
    assert foreign_web_page(connection({"origin": "http://127.0.0.1:9999"}).scope)
