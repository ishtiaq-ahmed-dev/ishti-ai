"""SC-23: the skill_creator feasibility gate asks the VRAM governor (ishti.vram.plan) instead of comparing free VRAM
to a fixed 4096 MB. Everything external (Ollama tags, nvidia-smi, the governor) is faked: offline and GPU-free."""
import types

import pytest

from ishti import vram
from ishti.skills.skill_creator import design


@pytest.fixture
def world(monkeypatch):
    """A card with only 965 MB free (the measured SC-23 case) and one installed coder rung."""
    import requests
    import subprocess
    monkeypatch.setattr(requests, "get", lambda *a, **k: types.SimpleNamespace(
        json=lambda: {"models": [{"name": "ishti-qwen2.5-coder:14b"}]}))
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: types.SimpleNamespace(stdout="965, 16303\n"))
    monkeypatch.setattr(vram, "rungs", lambda role: [{"name": "ishti-qwen2.5-coder:14b"}])
    monkeypatch.setattr(vram, "installed", lambda name: True)
    return monkeypatch


def test_governor_can_place_the_coder_although_little_is_free_right_now(world):
    world.setattr(vram, "plan", lambda model, role=None, **k: {
        "actions": [{"do": "unload", "model": "gemma3:4b"}], "target": {"model": model}, "need_mb": 9922})
    out = design.vram_check("make a skill")
    assert out["ok"] is True and out["free_mb"] == 965, out           # the old fixed threshold said "no" here
    assert out["plan"] == ["unload: gemma3:4b"] and out["need_mb"] == 9922


def test_governor_refusal_is_reported_with_its_reason(world):
    world.setattr(vram, "plan", lambda *a, **k: None)
    world.setattr(vram, "why_not", lambda model, ctx=None: "needs about 9.7 GB and only 0.9 GB is free")
    out = design.vram_check("make a skill")
    assert out["ok"] is False and "only 0.9 GB is free" in out["note"], out


def test_unreadable_governor_degrades_to_the_static_check_and_says_so(world):
    def boom(*a, **k):
        raise vram.GpuBusy("Ollama did not answer")
    world.setattr(vram, "plan", boom)
    out = design.vram_check("make a skill")
    assert out["ok"] is False and "static free-memory check" in out["note"], out   # 965 < 4096


def test_no_coder_rung_installed_is_not_ok(world):
    world.setattr(vram, "installed", lambda name: False)
    out = design.vram_check("make a skill")
    assert out["ok"] is False and out["coder"] == ""
