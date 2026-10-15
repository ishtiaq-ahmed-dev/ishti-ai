"""Swap cost (2026-10-13 RAM findings): a model load must be billed its host-RAM cost, a reload inside
swap_cost_window_s must show up as avoidable waste, and an upshift the machine cannot afford must be
refused BEFORE anything is loaded. Hermetic: the real governor, fakes for Ollama and psutil."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from ishti import vram  # noqa: E402


def _reset(monkeypatch):
    vram._swaps.clear()
    monkeypatch.setattr(vram, "record", lambda rows, **kw: None)
    monkeypatch.setattr(vram, "gpu", lambda: {"name": "gpu", "used_mb": 0, "total_mb": 16000,
                                              "free_mb": 16000, "available": True})


def test_a_model_load_is_billed_and_a_quick_reload_counts_as_waste(monkeypatch):
    """unload -> load of the same model is a round trip: its file size was paid twice."""
    _reset(monkeypatch)
    host = iter([5000, 4800, 5000, 13700])          # unload frees 200, load pays 8700
    monkeypatch.setattr(vram, "_host_mb", lambda: next(host))
    monkeypatch.setattr(vram, "_post", lambda *a, **k: {})
    monkeypatch.setattr(vram, "ps", lambda fresh=False, strict=True: [])

    assert vram.unload("heavy") is True             # resident -> gone: records the free
    monkeypatch.setattr(vram, "ps", lambda fresh=False, strict=True:
                        [{"name": "heavy", "size_mb": 9000, "size_vram_mb": 9000, "full": True,
                          "ctx": 8192, "expires_at": ""}])
    ok, entry = vram.load("heavy", "-1")            # back in: records the cost
    assert ok and entry

    bill = vram.swap_cost()
    assert bill["history"][-1]["to"] == "heavy"
    assert bill["history"][-1]["host_mb"] == 8700
    assert bill["history"][0]["frm"] == "heavy" and bill["history"][0]["host_mb"] == -200
    assert bill["round_trips"] and bill["round_trips"][0]["model"] == "heavy"
    assert bill["waste_mb"] == 8700, "the reload paid the file size a second time"


def test_a_reload_outside_the_window_is_not_reported_as_waste(monkeypatch):
    """A model that was evicted hours ago and then reloaded is normal residency, not churn."""
    _reset(monkeypatch)
    vram._swaps.extend([
        {"at": 1000.0, "to": "", "frm": "heavy", "host_mb": -200, "vram_mb": 0},
        {"at": 1000.0 + 1000, "to": "heavy", "frm": "", "host_mb": 8700, "vram_mb": 9000},
    ])
    assert vram.swap_cost()["round_trips"] == []
    assert vram.swap_cost()["waste_mb"] == 0
    assert len(vram.swap_cost()["history"]) == 2    # the bill is bounded, not a log


def test_an_unaffordable_upshift_is_refused_before_touching_ollama(monkeypatch):
    _reset(monkeypatch)
    calls = []
    monkeypatch.setattr(vram, "_check_running", lambda: None)
    monkeypatch.setattr(vram, "variant", lambda *a, **k: None)
    monkeypatch.setattr(vram, "keep_alive_for", lambda role: "-1")
    monkeypatch.setattr(vram, "_log_switch", lambda *a, **k: None)
    monkeypatch.setattr(vram, "rungs", lambda role: [{"name": "heavy", "model": "heavy"},
                                                      {"name": "light", "model": "light"}])
    monkeypatch.setattr(vram, "need_of", lambda name, ctx=None: (9000, True))
    monkeypatch.setattr(vram, "ps", lambda fresh=False, strict=True:
                        [{"name": "light", "size_mb": 900, "size_vram_mb": 900, "full": True,
                          "ctx": 8192, "expires_at": ""}])
    monkeypatch.setattr(vram, "_headroom_mb", lambda: 5000)   # 900 + 9000 of peak would not fit
    monkeypatch.setattr(vram, "load", lambda name, ka: calls.append(("load", name)) or (True, {}))
    monkeypatch.setattr(vram, "unload", lambda name, wait_s=20: calls.append(("unload", name)) or True)
    monkeypatch.setitem(vram._rung, "planner", 1)
    monkeypatch.setitem(vram._eff, "planner", "light")

    assert vram.switch_role("planner", 0, "back to the better model", cost_check=True) is False
    assert calls == [], "the swap was refused before Ollama was asked to load anything"


def test_the_same_upshift_runs_when_there_is_room(monkeypatch):
    """Same switch, comfortable headroom: it must run - the floor is a safety rail, not a veto."""
    _reset(monkeypatch)
    calls = []
    monkeypatch.setattr(vram, "_check_running", lambda: None)
    monkeypatch.setattr(vram, "variant", lambda *a, **k: None)
    monkeypatch.setattr(vram, "keep_alive_for", lambda role: "-1")
    monkeypatch.setattr(vram, "_log_switch", lambda *a, **k: None)
    monkeypatch.setattr(vram, "rungs", lambda role: [{"name": "heavy", "model": "heavy"},
                                                      {"name": "light", "model": "light"}])
    monkeypatch.setattr(vram, "need_of", lambda name, ctx=None: (9000, True))
    monkeypatch.setattr(vram, "ps", lambda fresh=False, strict=True:
                        [{"name": "light", "size_mb": 900, "size_vram_mb": 900, "full": True,
                          "ctx": 8192, "expires_at": ""}])
    monkeypatch.setattr(vram, "_headroom_mb", lambda: 50_000)
    monkeypatch.setattr(vram, "load", lambda name, ka: calls.append(("load", name)) or (True, {}))
    monkeypatch.setattr(vram, "unload", lambda name, wait_s=20: calls.append(("unload", name)) or True)
    monkeypatch.setitem(vram._rung, "planner", 1)
    monkeypatch.setitem(vram._eff, "planner", "light")

    assert vram.switch_role("planner", 0, "back to the better model", cost_check=True) is True
    assert calls == [("load", "heavy"), ("unload", "light")]


def test_floor_opt_out_and_an_unmeasurable_machine(monkeypatch):
    """swap_free_floor_mb: 0 opts out; a machine that cannot answer is never refused."""
    _reset(monkeypatch)
    monkeypatch.setattr(vram, "_headroom_mb", lambda: 100)          # far too little for any model
    assert vram._too_costly(10 ** 6) is True                        # default floor 2048 refuses
    monkeypatch.setattr(vram, "cfg", lambda: {"swap_free_floor_mb": 0})
    assert vram._too_costly(10 ** 6) is False                       # explicitly opted out
    monkeypatch.setattr(vram, "cfg", lambda: {"swap_free_floor_mb": 2048})
    monkeypatch.setattr(vram, "_headroom_mb", lambda: None)         # cannot measure -> never refuse
    assert vram._too_costly(10 ** 6) is False
