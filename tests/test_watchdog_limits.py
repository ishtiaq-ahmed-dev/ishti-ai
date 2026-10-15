"""Bounded supervisor recovery and native splash errors; no real owner process is touched."""
import asyncio
import types

from starlette.requests import Request

from ishti import lifecycle, watchdog


def test_supervisor_retries_once_then_persists_one_terminal_error(monkeypatch):
    starts, stopped, records = [], [], []
    monkeypatch.setattr(watchdog, "log", lambda message: None)
    monkeypatch.setattr(lifecycle, "registry", lambda update=None, **kw: records.append(update) or {})
    monkeypatch.setattr("ishti.skills.log_need", lambda *a: None)
    wd = watchdog.Watchdog(8888)
    monkeypatch.setattr(wd, "start", lambda: starts.append(1))
    monkeypatch.setattr(wd, "kill_tree", lambda: stopped.append(1))
    assert wd.restart("test crash") is True
    assert wd.restart("test repeated crash") is False
    assert len(starts) == 1 and len(stopped) == 2
    assert "retries stopped" in records[-1]["boot_error"]


def test_native_splash_reads_terminal_supervisor_error(monkeypatch):
    from desktop.app_window import MainApi
    import requests
    monkeypatch.setattr(requests, "get", lambda *a, **kw: (_ for _ in ()).throw(requests.ConnectionError()))
    monkeypatch.setattr(lifecycle, "registry", lambda **kw: {"boot_error": "Check the occupied port and retry."})
    result = MainApi().boot_state()
    assert result["stage"] == "backend_up" and result["error"] and not result["ready"]


def test_owner_retry_does_not_spawn_a_duplicate_supervisor(monkeypatch):
    monkeypatch.setattr(lifecycle, "registry", lambda **kw: {"boot_error": "failed", "watchdog_pid": 123, "watchdog_ct": 42})
    monkeypatch.setattr(lifecycle, "_alive", lambda *a: True)
    monkeypatch.setattr(watchdog.subprocess, "Popen", lambda *a, **kw: (_ for _ in ()).throw(AssertionError("duplicate spawn")))
    assert watchdog.owner_retry()["retried"] is False


def test_explicit_owner_retry_spawns_only_the_canonical_supervisor(monkeypatch):
    records, launches = [], []
    monkeypatch.setattr(lifecycle, "registry", lambda update=None, **kw: records.append(update) or {"boot_error": "failed"})
    monkeypatch.setattr(lifecycle, "_alive", lambda *a: False)
    monkeypatch.setattr(lifecycle, "created", lambda pid: 42)
    monkeypatch.setattr(watchdog.subprocess, "Popen", lambda args, **kw: launches.append(args) or types.SimpleNamespace(pid=123))
    assert watchdog.owner_retry()["retried"]
    assert launches[0][1:] == ["-m", "ishti.watchdog", "8888"]
    assert records[-1]["watchdog_pid"] == 123 and records[-1]["boot_error"] is None


def test_boot_retry_refuses_an_initializer_that_is_still_running(monkeypatch):
    monkeypatch.setattr(lifecycle, "_shutdown", {"started": False})
    monkeypatch.setattr(lifecycle, "_st", {"boot_thread": None, "stage_workers": {"memory_loaded": (types.SimpleNamespace(is_alive=lambda: True), {})}})
    assert not lifecycle.retry_boot()["retried"]


def test_retry_endpoint_rejects_remote_and_foreign_origins(monkeypatch):
    from ishti.lifecycle_api import retry_ready
    monkeypatch.setattr(lifecycle, "retry_boot", lambda: {"retried": True})
    def request(ip, origin):
        return Request({"type": "http", "method": "POST", "scheme": "http", "path": "/api/ready/retry",
            "server": ("127.0.0.1", 8888), "client": (ip, 1234), "headers": [(b"host", b"127.0.0.1:8888"), (b"origin", origin)]})
    assert asyncio.run(retry_ready(request("127.0.0.1", b"http://127.0.0.1:8888"))).status_code == 200
    assert asyncio.run(retry_ready(request("10.1.1.1", b"http://127.0.0.1:8888"))).status_code == 403
    assert asyncio.run(retry_ready(request("127.0.0.1", b"https://evil.example"))).status_code == 403
