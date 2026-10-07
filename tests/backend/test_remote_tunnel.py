from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading

import pytest
from fastapi.testclient import TestClient

from ishti import tunnel


class FakeProcess:
    def __init__(self):
        self.pid = 424242
        self.stdout = iter(())
        self.ended = False

    def poll(self):
        return 0 if self.ended else None

    def terminate(self):
        self.ended = True

    def kill(self):
        self.ended = True

    def wait(self, timeout=None):
        self.ended = True
        return 0


@pytest.fixture
def fake_agent():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = json.dumps({"tunnels": [{"public_url": "https://owner-test.ngrok-free.app"}]}).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

        def log_message(self, *_args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/api"
    finally:
        server.shutdown(); server.server_close(); thread.join(3)


def test_manager_starts_from_fake_agent_api_and_stops_owned_child(tmp_path, fake_agent, monkeypatch):
    commands, records, process = [], [], FakeProcess()

    def factory(command, **kwargs):
        commands.append((command, kwargs))
        return process

    monkeypatch.setattr(tunnel.audit, "record", lambda *args, **kwargs: records.append((args, kwargs)))
    manager = tunnel.TunnelManager(api_base=fake_agent, process_factory=factory, ngrok_path="C:/fake/ngrok.exe",
                                   state_path=tmp_path / "tunnel.json", allow_test=True)
    started = manager.start(8890, timeout_s=2)
    assert started == {"running": True, "public_url": "https://owner-test.ngrok-free.app",
                       "port": 8890, "last_error": ""}
    assert commands[0][0] == ["C:/fake/ngrok.exe", "http", "127.0.0.1:8890", "--log=stdout"]
    assert commands[0][1]["creationflags"] == getattr(tunnel.subprocess, "CREATE_NO_WINDOW", 0)
    assert json.loads((tmp_path / "tunnel.json").read_text(encoding="utf-8"))["public_url"].startswith("https://")
    assert manager.stop()["running"] is False and process.ended
    assert not (tmp_path / "tunnel.json").exists()
    assert [entry[0][0] for entry in records] == ["remote tunnel started", "remote tunnel stopped"]


def test_tunnel_error_messages_are_plain_and_never_echo_tokens():
    assert tunnel._safe_error(["authentication failed: authtoken SECRET-VALUE"]) == (
        "ngrok is not authenticated; add your authtoken with the ngrok CLI, then retry")
    assert tunnel._safe_error(["failed with ERR_NGROK_4018 and token SECRET"]) == (
        "ngrok could not start (ERR_NGROK_4018); review the local ngrok configuration")


def test_live_start_is_blocked_in_test_mode(tmp_path):
    manager = tunnel.TunnelManager(ngrok_path="C:/fake/ngrok.exe", state_path=tmp_path / "state.json")
    with pytest.raises(tunnel.TunnelError, match="disabled in test mode"):
        manager.start(8890)


def test_http_start_is_loopback_only_but_status_and_stop_are_authenticated(monkeypatch):
    from interface.web_ui import server
    from ishti.channels import policy
    calls = []
    monkeypatch.setattr(server, "API_TOKEN", "owner-test-key")
    monkeypatch.setattr(tunnel, "start", lambda port: calls.append(("start", port)) or
                        {"running": True, "public_url": "https://test.ngrok-free.app", "port": port, "last_error": ""})
    monkeypatch.setattr(tunnel, "status", lambda: {"running": True, "public_url": "https://test.ngrok-free.app"})
    monkeypatch.setattr(tunnel, "stop", lambda: calls.append(("stop", 0)) or {"running": False, "public_url": ""})
    monkeypatch.setattr(policy, "save", lambda value: value)
    local = TestClient(server.app, base_url="http://127.0.0.1:8888", client=("127.0.0.1", 50000))
    assert local.post("/api/remote/tunnel/start", json={"port": 8890}).status_code == 200
    remote = TestClient(server.app, base_url="https://abc.ngrok-free.app", client=("127.0.0.1", 50001), headers={
        "host": "abc.ngrok-free.app", "origin": "https://abc.ngrok-free.app", "x-forwarded-for": "203.0.113.9",
        "x-forwarded-proto": "https", "authorization": "Bearer owner-test-key"})
    assert remote.post("/api/remote/tunnel/start", json={"port": 8890}).status_code == 403
    assert remote.get("/api/remote/tunnel").status_code == 200
    assert remote.post("/api/remote/tunnel/stop").status_code == 200
    assert calls == [("start", 8890), ("stop", 0)]
