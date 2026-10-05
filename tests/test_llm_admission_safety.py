"""GPU admission and recovery boundaries, without loading models or services."""
from contextlib import contextmanager
from types import ModuleType, SimpleNamespace
import sys

import pytest

from ishti import llm


@pytest.fixture
def world(monkeypatch):
    import ishti

    events = []

    class GpuBusy(RuntimeError):
        pass

    class APIConnectionError(Exception):
        pass

    @contextmanager
    def busy(model):
        events.append(("enter", model))
        try:
            yield
        finally:
            events.append(("exit", model))

    vram = ModuleType("ishti.vram")
    vram.GpuBusy = GpuBusy
    vram.busy = busy
    vram.release = lambda lease: events.append(("release", lease))

    def admit(model, **kwargs):
        events.append(("admit", model))
        return {"ok": True, "model": model, "lease": 17}

    vram.admit = admit
    models = ModuleType("ishti.models")
    models.used = lambda model: events.append(("used", model))
    lifecycle = ModuleType("ishti.lifecycle")
    lifecycle.start_ollama = lambda: events.append(("restart",)) or True
    for name, module in (("vram", vram), ("models", models), ("lifecycle", lifecycle)):
        monkeypatch.setitem(sys.modules, "ishti." + name, module)
        monkeypatch.setattr(ishti, name, module, raising=False)
    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(APIConnectionError=APIConnectionError))
    monkeypatch.setattr(llm, "_create_call", lambda *args: events.append(("generate",)) or "answer")
    return SimpleNamespace(events=events, vram=vram, lifecycle=lifecycle, connection_error=APIConnectionError)


@pytest.mark.parametrize("error", [RuntimeError("bad telemetry"), ValueError("bad payload")])
def test_unexpected_admission_error_never_generates(monkeypatch, world, error):
    def broken(*args, **kwargs):
        raise error

    monkeypatch.setattr(world.vram, "admit", broken)
    with pytest.raises(world.vram.GpuBusy, match="could not be verified"):
        llm._create(model="synthetic", messages=[])
    assert world.events == [("enter", "synthetic"), ("exit", "synthetic")]


def test_refused_admission_never_generates(monkeypatch, world):
    monkeypatch.setattr(world.vram, "admit", lambda *args, **kwargs: {"ok": False, "queued": "full"})
    with pytest.raises(world.vram.GpuBusy, match="full"):
        llm._create(model="synthetic", messages=[])
    assert world.events == [("enter", "synthetic"), ("exit", "synthetic")]


def test_recovery_reenters_admission_before_generating(monkeypatch, world):
    calls = []

    def generate(args, client):
        world.events.append(("generate",))
        calls.append(args)
        if len(calls) == 1:
            raise world.connection_error()
        return "recovered"

    monkeypatch.setattr(llm, "_create_call", generate)
    assert llm._create(model="synthetic", messages=[]) == "recovered"
    assert world.events == [
        ("enter", "synthetic"), ("admit", "synthetic"), ("used", "synthetic"), ("generate",),
        ("exit", "synthetic"), ("release", 17), ("restart",),
        ("enter", "synthetic"), ("admit", "synthetic"), ("used", "synthetic"), ("generate",),
        ("exit", "synthetic"), ("release", 17),
    ]
    assert all("_recover" not in call and "_role" not in call for call in calls)


def test_recovery_cannot_bypass_new_admission_failure(monkeypatch, world):
    def generate(*args):
        world.events.append(("generate",))
        raise world.connection_error()

    def restart():
        def broken(*args, **kwargs):
            raise RuntimeError("new residency unknown")
        monkeypatch.setattr(world.vram, "admit", broken)
        return True

    monkeypatch.setattr(llm, "_create_call", generate)
    monkeypatch.setattr(world.lifecycle, "start_ollama", restart)
    with pytest.raises(world.vram.GpuBusy):
        llm._create(model="synthetic", messages=[])
    assert world.events.count(("generate",)) == 1
    assert world.events.count(("exit", "synthetic")) == 2
    assert world.events.count(("release", 17)) == 1


def test_stream_holds_and_releases_admission(monkeypatch, world):
    monkeypatch.setattr(llm, "_create_call", lambda *args: iter(["a", "b"]))
    stream = llm._create(model="synthetic", messages=[], stream=True)
    assert ("release", 17) not in world.events
    assert next(stream) == "a"
    stream.close()
    assert world.events[-2:] == [("exit", "synthetic"), ("release", 17)]
