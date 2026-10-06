"""Laya cache misses and concurrent startup cannot download or alter offline policy."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import ModuleType, SimpleNamespace
import os
import sys

import pytest

from ishti import router


@pytest.fixture
def world(monkeypatch, tmp_path):
    checkpoint = tmp_path / "cache" / "typed-decisions"
    for name in ("rl_agent_config.json", "model.safetensors", "tokenizer/tokenizer_config.json", "encoder/config.json"):
        path = checkpoint / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}")
    events = []

    class Router:
        def __init__(self, **kwargs):
            events.append(("construct", kwargs))
            self.models = {"typed-decisions": ("synthetic/repo", "typed-decisions")}

        def load(self, name):
            events.append(("load", name, self.models[name]))
            return SimpleNamespace(model=SimpleNamespace(half=lambda: events.append(("half",))))

        def predict(self, *args, **kwargs):
            events.append(("predict",))
            return {"answers": {"skill": {"probabilities": {"chat": 0.9}}, "is_chat": {"noul": 0.9},
                                "domain": {"choice": "general"}, "needs_tools": {"noul": 0},
                                "is_sensitive": {"noul": 0}}}

    def snapshot_download(**kwargs):
        events.append(("snapshot", kwargs))
        return str(checkpoint.parent)

    package = ModuleType("laya")
    package.Router = Router
    submodule = ModuleType("laya.router")
    submodule.normalise_name = lambda name: name
    monkeypatch.setitem(sys.modules, "laya", package)
    monkeypatch.setitem(sys.modules, "laya.router", submodule)
    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: False)))
    hub = SimpleNamespace(snapshot_download=snapshot_download)
    monkeypatch.setitem(sys.modules, "huggingface_hub", hub)
    monkeypatch.setattr(router, "_laya", None)
    monkeypatch.setattr(router, "laya_error", "")
    monkeypatch.setattr(router, "_cfg", {"use_laya": True, "laya_model": "typed-decisions",
                                         "min_confidence": 0.5, "default_skill": "chat"})
    return SimpleNamespace(checkpoint=checkpoint, events=events, hub=hub)


@pytest.mark.parametrize("offline", [None, "0", "1"])
def test_load_uses_cache_only_without_altering_environment(monkeypatch, world, offline):
    if offline is None:
        monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    else:
        monkeypatch.setenv("HF_HUB_OFFLINE", offline)
    before = dict(os.environ)
    assert router.laya() is not None
    assert os.environ == before
    assert ("snapshot", {"repo_id": "synthetic/repo", "local_files_only": True}) in world.events
    assert ("load", "typed-decisions", str(world.checkpoint)) in world.events


def test_parallel_startup_publishes_one_complete_router(world):
    barrier = Barrier(8)

    def load(_):
        barrier.wait(timeout=5)
        return router.laya()

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(load, range(8)))
    assert results[0] is not None and all(result is results[0] for result in results)
    assert sum(event[0] == "construct" for event in world.events) == 1
    assert sum(event[0] == "load" for event in world.events) == 1


def test_missing_checkpoint_does_not_retry_online(monkeypatch, world):
    calls = []

    def missing(**kwargs):
        calls.append(kwargs)
        raise FileNotFoundError("not cached")

    monkeypatch.setattr(world.hub, "snapshot_download", missing)
    before = dict(os.environ)
    assert router.laya() is None
    assert router.laya() is None
    assert calls == [{"repo_id": "synthetic/repo", "local_files_only": True}]
    assert "not cached" in router.laya_error
    assert os.environ == before
    assert not any(event[0] == "load" for event in world.events)


def test_incomplete_bundle_never_calls_model_loader(world):
    (world.checkpoint / "encoder/config.json").unlink()
    assert router.laya() is None
    assert "incomplete" in router.laya_error
    assert not any(event[0] == "load" for event in world.events)


def test_prediction_does_not_mutate_environment(monkeypatch, world):
    from ishti import corrections
    monkeypatch.setattr(corrections, "learned", lambda text: None)
    monkeypatch.setattr(router, "_disabled_flags", frozenset)
    monkeypatch.setattr(router, "questions", lambda skills: {})
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    skills = {"chat": SimpleNamespace(status="ready", name="chat", manifest={"route": "chat"},
                                      matches=lambda text: False)}
    before = dict(os.environ)
    assert router.route("a synthetic question", skills)["source"] == "laya"
    assert os.environ == before
