"""Loopback keep-alive guard (Cycle 4): ISHTI's own Ollama calls must reuse connections, and doing
so must never weaken the per-skill network boundary.

Measured 2026-10-02: `requests.get()` to a loopback service costs ~12-15 ms (new TCP connection per
call) against ~0.5 ms on a reused one; the VRAM governor makes several of those per reply
(`tools/bench_loopback.py`: 20.5 ms -> 4.4 ms). The safety half matters just as much: `ishti/scope.py`
enforces the skill network scope by patching `requests.sessions.Session.request`, so this test proves a
shared Session still travels through that patch.
"""
import os
import statistics
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import requests  # noqa: E402
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import pytest

from ishti import keepalive, scope  # noqa: E402


def test_one_shared_session_is_reused():
    """The same Session (and therefore the same connection pool) for every call."""
    assert keepalive.session() is keepalive.session()
    assert keepalive.session() is not requests.Session()


def test_keepalive_still_goes_through_the_scope_hook(monkeypatch):
    """scope.py enforces the per-skill network boundary by patching requests.sessions.Session.request.
    Prove a shared Session still travels through that hook: record scope.check_url calls.

    If scope.install() ever moved its check off Session.request, this fails - instead of the hot path
    quietly opening a hole in the boundary.
    """
    scope.install()  # idempotent; the dispatcher does this at import anyway
    seen = []
    monkeypatch.setattr(scope, "check_url", lambda url: seen.append(url))
    def fake_send(session, request, **kwargs):
        response = requests.Response()
        response.status_code = 200
        response._content = b'{}'
        return response
    monkeypatch.setattr(requests.sessions.Session, 'send', fake_send)
    url = "http://127.0.0.1:11434/api/version"
    try:
        keepalive.get(url, timeout=3)
        requests.get(url, timeout=3)
    except Exception:
        pass  # Ollama down: the hook still runs, which is what this test is about
    assert len(seen) >= 2, f"the scope hook did not see the calls (saw {seen})"


@pytest.fixture
def loopback_service():
    addresses = []
    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'
        def do_GET(self):
            addresses.append(self.client_address)
            self.send_response(200)
            self.send_header('Content-Length', '2')
            self.end_headers()
            self.wfile.write(b'{}')
        def log_message(self, *args):
            return None
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}', addresses
    finally:
        keepalive.session().close()
        server.shutdown()
        server.server_close()
        worker.join(3)


def test_shared_connection_is_reused_instead_of_opening_a_fresh_one(loopback_service):
    """Count actual TCP client addresses on an owned fixture; no live Ollama or timing noise."""
    url, addresses = loopback_service
    for _ in range(5):
        assert keepalive.get(url, timeout=3).status_code == 200
    assert len(set(addresses)) == 1
    addresses.clear()
    for _ in range(5):
        assert requests.get(url, timeout=3).status_code == 200
    assert len(set(addresses)) == 5
