"""Audit regressions: bounded failure paths and resource retention, no real service mutations."""
import asyncio
import io
import sqlite3
import threading
import types

import pytest

from ishti import lifecycle
from ishti.core.runtime_logs import drain, rotate_closed
from ishti.core.sqlite_cache import ThreadConnections


@pytest.fixture
def boot_state(monkeypatch):
    monkeypatch.setattr(lifecycle, "_st", {"state": "starting", "port": 8888, "t0": 0,
        "done": {}, "error": None, "error_stage": None, "detail": {}, "active": set(), "booting": True, "stage_workers": {}})
    monkeypatch.setattr(lifecycle, "_shutdown", {"started": False})
    monkeypatch.setattr(lifecycle, "_publish_stage", lambda _: None)
    monkeypatch.setattr(lifecycle.time, "sleep", lambda _: None)


def test_failure_stops_after_two_attempts_and_never_becomes_ready(boot_state, monkeypatch):
    calls = []
    monkeypatch.setattr(lifecycle, "_run_stage", lambda sid: calls.append(sid) or "failed")
    lifecycle.boot()
    assert calls == ["backend_up", "backend_up"]
    assert not lifecycle._st["done"] and lifecycle._st["error_stage"] == "backend_up"
    assert "retries stopped" in lifecycle._st["error"]


def test_one_retry_recovers(boot_state, monkeypatch):
    answers = iter(["failed", None])
    monkeypatch.setattr(lifecycle, "_run_stage", lambda sid: next(answers))
    assert lifecycle._run_chain(("backend_up",), {})
    assert lifecycle._st["done"]["backend_up"] and not lifecycle._st["error"]


def test_timeout_does_not_duplicate_a_native_initializer(boot_state, monkeypatch):
    gate = threading.Event()
    count = []
    def initialize():
        count.append(1)
        gate.wait(2)
    monkeypatch.setattr(lifecycle, "STAGE_FNS", {"skills_loaded": initialize})
    monkeypatch.setattr(lifecycle, "TIMEOUTS", {"skills_loaded": 0.01})
    try:
        assert not lifecycle._run_chain(("skills_loaded",), {})
        assert lifecycle._run_stage("skills_loaded") is not None
        assert count == [1]
    finally:
        gate.set()
        lifecycle._st["stage_workers"]["skills_loaded"][0].join(2)


def test_terminal_error_answers_waiting_requests_promptly(boot_state):
    lifecycle._st["error"] = "Restart after checking the port."
    assert lifecycle._st["error"] in lifecycle.await_brain(timeout=2)


def test_loop_handler_only_quiets_known_proactor_disconnect():
    loop = asyncio.new_event_loop()
    forwarded = []
    loop.set_exception_handler(lambda loop, context: forwarded.append(context))
    lifecycle.install_loop_handler(loop)
    exc = ConnectionResetError("reset")
    exc.winerror = 10054
    class Handle:
        def __repr__(self):
            return "_ProactorBasePipeTransport._call_connection_lost(...)"
    try:
        loop.call_exception_handler({"exception": exc, "handle": Handle()})
        loop.call_exception_handler({"exception": exc, "handle": object()})
        loop.call_exception_handler({"exception": ValueError("real defect"), "handle": Handle()})
        lifecycle.install_loop_handler(loop)
        assert len(forwarded) == 2
    finally:
        loop.close()


@pytest.mark.parametrize("ct,cmd,health", [(None,["python","-m","ishti"],False),
    (42,["python","other.py"],False), (41,["python","-m","ishti"],False), (42,["python","-m","ishti"],True)])
def test_port_recovery_never_kills_unverified_or_healthy_process(monkeypatch, ct, cmd, health):
    killed = []
    monkeypatch.setattr(lifecycle, "_port_free", lambda _: False)
    monkeypatch.setattr(lifecycle, "_health", lambda _: health)
    monkeypatch.setattr(lifecycle, "registry", lambda **kw: {"server_pid": 123456, "server_ct": ct})
    monkeypatch.setattr(lifecycle, "kill_tree", killed.append)
    monkeypatch.setattr(lifecycle.psutil, "Process", lambda _: types.SimpleNamespace(cmdline=lambda: cmd,
        create_time=lambda: 42, net_connections=lambda **kw: [types.SimpleNamespace(status="LISTEN", laddr=types.SimpleNamespace(port=8888))]))
    with pytest.raises(RuntimeError, match="occupied"):
        lifecycle.prepare_port(8888)
    assert not killed


def test_verified_stale_backend_is_reclaimed_once(monkeypatch):
    killed = []
    answers = iter([False, True])
    monkeypatch.setattr(lifecycle, "_port_free", lambda _: next(answers))
    monkeypatch.setattr(lifecycle, "_health", lambda _: False)
    monkeypatch.setattr(lifecycle, "registry", lambda **kw: {"server_pid": 123456, "server_ct": 42})
    monkeypatch.setattr(lifecycle, "kill_tree", killed.append)
    monkeypatch.setattr(lifecycle.psutil, "Process", lambda _: types.SimpleNamespace(cmdline=lambda: ["python", "-m", "ishti"],
        create_time=lambda: 42, net_connections=lambda **kw: [types.SimpleNamespace(status="LISTEN", laddr=types.SimpleNamespace(port=8888))]))
    lifecycle.prepare_port(8888)
    assert killed == [123456]


def test_dead_threads_release_sqlite_connections_and_id_reuse_is_impossible(tmp_path):
    cache = ThreadConnections()
    opened = []
    def worker():
        key = (threading.current_thread(), str(tmp_path / "usage.db"))
        c = sqlite3.connect(key[1], check_same_thread=False)
        cache[key] = c
        assert cache.get(key) is c
        opened.append(c)
    for _ in range(50):
        thread = threading.Thread(target=worker)
        thread.start()
        thread.join(2)
    assert len(cache) == 0
    for c in opened:
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            c.execute("SELECT 1")


def test_rotating_child_log_is_bounded_even_with_long_lines(tmp_path):
    path = tmp_path / "ollama.log"
    drain(io.BytesIO((b"x" * 500 + b"\n") * 100), path, limit=2048, backups=2)
    logs = list(tmp_path.glob("ollama.log*"))
    assert len(logs) == 3 and all(p.stat().st_size < 2048 for p in logs)


def test_crash_rotation_preserves_recent_diagnostics(tmp_path):
    path = tmp_path / "crash.log"
    path.write_text("original diagnostic")
    rotate_closed(path, limit=1)
    assert (tmp_path / "crash.log.1").read_text() == "original diagnostic"


def test_health_probe_does_not_issue_an_external_search(monkeypatch):
    from ishti import health
    seen = []
    monkeypatch.setattr(health.keepalive, "get", lambda url, **kw: seen.append(url) or types.SimpleNamespace(ok=True, status_code=200))
    assert health._searx()[0] and seen[0].endswith("/healthz")
    assert "q=" not in seen[0]


def test_stdout_without_newlines_does_not_grow_forever():
    stream = lifecycle._Stream(20, None)
    stream.write("x" * 50_000)
    assert len(stream.buf) <= 16_384


def test_real_unregistered_loopback_listener_is_preserved(monkeypatch):
    import socket
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        monkeypatch.setattr(lifecycle, "registry", lambda **kw: {})
        with pytest.raises(RuntimeError, match="occupied"):
            lifecycle.prepare_port(port)
        assert listener.fileno() >= 0


def test_real_stale_test_backend_can_be_reclaimed(tmp_path, monkeypatch):
    import os
    from pathlib import Path
    import subprocess
    import sys
    import time
    # A tiny fake module with ISHTI's launch argv. Only this test's child can be stopped.
    (tmp_path / "ishti.py").write_text(
        "from http.server import HTTPServer,BaseHTTPRequestHandler\n"
        "from pathlib import Path\n"
        "import os,json\n"
        "class Handler(BaseHTTPRequestHandler):\n"
        " def do_GET(self): self.send_response(503);self.end_headers()\n"
        " def log_message(self,*a): return\n"
        "server=HTTPServer(('127.0.0.1',0),Handler)\n"
        "Path('port.txt').write_text(json.dumps({'port':server.server_address[1],'pid':os.getpid()}))\n"
        "server.serve_forever()\n", encoding="utf-8")
    env = dict(os.environ, PYTHONPATH=str(tmp_path), PYTHONDONTWRITEBYTECODE="1")
    proc = subprocess.Popen([sys.executable, "-B", "-m", "ishti"], cwd=tmp_path, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=lifecycle.NOWIN)
    try:
        deadline = time.monotonic() + 10
        while not (tmp_path / "port.txt").exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        import json
        record = json.loads((tmp_path / "port.txt").read_text())
        port, server_pid = record["port"], record["pid"]
        assert server_pid in {proc.pid, *(p.pid for p in lifecycle.psutil.Process(proc.pid).children(recursive=True))}
        ct = lifecycle.created(server_pid)
        monkeypatch.setattr(lifecycle, "registry", lambda **kw: {"server_pid": server_pid, "server_ct": ct})
        lifecycle.prepare_port(port)
        assert not lifecycle._alive(server_pid) and lifecycle._port_free(port)
    finally:
        if "server_pid" in locals() and lifecycle._alive(server_pid):
            lifecycle.kill_tree(server_pid)
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)


def test_serve_uses_uvicorn_public_runner_and_installs_handler_on_its_loop(monkeypatch):
    import uvicorn
    seen = []
    class Server:
        def __init__(self, config):
            self.config = config
        def run(self):
            seen.append("public-run")
            asyncio.run(self.serve())
        async def serve(self):
            handler = asyncio.get_running_loop().get_exception_handler()
            assert getattr(handler, "_ishti_disconnect_handler", False)
            seen.append("installed-on-active-loop")
    monkeypatch.setattr(uvicorn, "Server", Server)
    monkeypatch.setattr(lifecycle, "_server", {"uvicorn": None})
    monkeypatch.setattr(lifecycle, "_shutdown", {"started": False})
    monkeypatch.setattr(lifecycle, "prepare_port", lambda port: None)
    lifecycle.serve(None, 8888)
    assert seen == ["public-run", "installed-on-active-loop"]
