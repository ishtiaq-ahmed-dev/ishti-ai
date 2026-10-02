"""tools.web.search.ensure_searxng: Q9 (container never created) and SW-12 (container Up, published port dead after a host
reboot). Docker and HTTP are faked: offline, nothing is started or pulled."""
import types

import pytest

from tools.web import search


class Docker:
    def __init__(self, exists=True, start_rc=0):
        self.calls, self.exists, self.start_rc = [], exists, start_rc

    def __call__(self, *args, timeout=30):
        self.calls.append(args)
        if args[0] == "start" and not self.exists:
            return types.SimpleNamespace(returncode=1, stderr="Error response from daemon: No such container: ishti-searxng", stdout="")
        if args[0] == "start":
            return types.SimpleNamespace(returncode=self.start_rc, stderr="", stdout="")
        return types.SimpleNamespace(returncode=0, stderr="", stdout="")


@pytest.fixture
def world(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    clock = {"t": 0.0}
    real_time = __import__("time").time
    monkeypatch.setattr("time.time", lambda: clock["t"])
    monkeypatch.setattr("time.sleep", lambda s: clock.__setitem__("t", clock["t"] + s))
    return types.SimpleNamespace(clock=clock, mp=monkeypatch, real_time=real_time)


def health(world, answers):
    """requests.get -> 200 once `answers` says so (a callable of the docker calls seen so far)."""
    def get(url, timeout=2):
        if answers():
            return types.SimpleNamespace(status_code=200)
        raise ConnectionError("refused")
    world.mp.setattr(search.requests, "get", get)


def test_healthy_search_touches_nothing(world):
    d = Docker()
    world.mp.setattr(search, "_docker", d)
    health(world, lambda: True)
    assert search.ensure_searxng() is True and d.calls == []


def test_container_up_but_port_dead_is_restarted_once(world):
    d = Docker()
    world.mp.setattr(search, "_docker", d)
    health(world, lambda: any(c[0] == "restart" for c in d.calls))     # only a restart revives the port mapping
    assert search.ensure_searxng(wait_s=60) is True
    assert [c[0] for c in d.calls].count("restart") == 1


def test_missing_container_is_not_created_unless_asked(world, capsys):
    d = Docker(exists=False)
    world.mp.setattr(search, "_docker", d)
    health(world, lambda: False)
    assert search.ensure_searxng(wait_s=30) is False
    assert not any(c[0] == "run" for c in d.calls) and "does not exist" in capsys.readouterr().out


def test_create_builds_a_loopback_only_container_with_a_persistent_secret(world, tmp_path):
    d = Docker()
    world.mp.setattr(search, "_docker", d)
    world.mp.setattr(search, "ROOT_DIR", str(tmp_path))
    assert search.create_searxng() is True
    run = next(c for c in d.calls if c[0] == "run")
    assert "127.0.0.1:8890:8080" in run and "--restart" in run and run[-1] == "searxng/searxng"
    secret = (tmp_path / "data" / "searxng_secret").read_text()
    assert len(secret) == 64 and f"SEARXNG_SECRET={secret}" in run
    d.calls.clear()
    search.create_searxng()
    assert f"SEARXNG_SECRET={secret}" in next(c for c in d.calls if c[0] == "run")    # the secret is stable across runs


def test_create_flag_creates_then_waits_for_health(world, tmp_path):
    d = Docker(exists=False)
    world.mp.setattr(search, "_docker", d)
    world.mp.setattr(search, "ROOT_DIR", str(tmp_path))
    health(world, lambda: any(c[0] == "run" for c in d.calls))
    assert search.ensure_searxng(wait_s=60, create=True) is True
