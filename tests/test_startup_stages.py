"""Startup (LAUNCH-4/5, PERF-5/6): independent boot stages run side by side, every stage reports pending/loading/done/error,
and a request that arrives before the brain is loaded is queued with a plain explanation - never failed, never silent.
Offline: the stage functions are replaced by sleeps, nothing is started."""
import threading
import time

import pytest

from ishti import lifecycle as L


@pytest.fixture
def fresh(monkeypatch):
    monkeypatch.setitem(L._st, "done", {})
    monkeypatch.setitem(L._st, "error", None)
    monkeypatch.setitem(L._st, "error_stage", None)
    monkeypatch.setitem(L._st, "active", set())
    monkeypatch.setitem(L._st, "booting", False)
    monkeypatch.setitem(L._st, "state", "starting")
    monkeypatch.setitem(L._st, "t0", time.time())
    monkeypatch.setitem(L._shutdown, "started", False)
    monkeypatch.setattr(L, "_write_boot_metrics", lambda *a: None)
    monkeypatch.setattr(L, "ollama_up", lambda: True)
    yield


def test_chains_cover_every_stage_once():
    flat = [s for c in L.CHAINS for s in c]
    assert sorted(flat + ["backend_up"]) == sorted(s for s, _ in L.STAGES if s != "ready")


def test_independent_stages_overlap_and_dependent_ones_stay_ordered(fresh, monkeypatch):
    order = []

    def stage(name, secs):
        def run():
            order.append(("start", name))
            time.sleep(secs)
            order.append(("end", name))
        return run
    fns = {"backend_up": stage("backend_up", 0), "ollama_ready": stage("ollama_ready", 0.4), "memory_loaded": stage("memory_loaded", 0.3),
           "skills_loaded": stage("skills_loaded", 0.5), "planner_warm": stage("planner_warm", 0.3), "voice_ready": stage("voice_ready", 0.1)}
    monkeypatch.setattr(L, "STAGE_FNS", fns)
    t = time.time()
    L.boot()
    took = time.time() - t
    sequential = 0.4 + 0.3 + 0.5 + 0.3 + 0.1
    assert took < sequential * 0.75, f"stages did not overlap: {took:.2f}s vs {sequential:.2f}s sequential"
    pos = {e: i for i, e in enumerate(order)}
    assert pos[("end", "ollama_ready")] < pos[("start", "planner_warm")]          # the model needs the engine first
    assert pos[("end", "skills_loaded")] < pos[("start", "voice_ready")]
    assert pos[("start", "memory_loaded")] < pos[("end", "ollama_ready")]          # ...while memory loads meanwhile
    snap = L.ready_snapshot()
    assert [s["id"] for s in snap["stages"]] == [s for s, _ in L.STAGES]          # documented order is unchanged
    assert snap["ready"] is True and all(s["status"] == "done" for s in snap["stages"])


def test_stage_status_pending_loading_error(fresh, monkeypatch):
    L._st["done"]["backend_up"] = True
    L._st["active"].add("ollama_ready")
    st = {s["id"]: s["status"] for s in L.ready_snapshot()["stages"]}
    assert st["backend_up"] == "done" and st["ollama_ready"] == "loading" and st["planner_warm"] == "pending"
    L._st["error"], L._st["error_stage"] = "boom", "planner_warm"
    assert {s["id"]: s["status"] for s in L.ready_snapshot()["stages"]}["planner_warm"] == "error"


def test_request_during_boot_is_queued_with_a_plain_message(fresh):
    L._st["booting"] = True
    L._st["done"].update(backend_up=True, ollama_ready=True, memory_loaded=True)
    said = []
    out = {}
    th = threading.Thread(target=lambda: out.update(r=L.await_brain(said.append, timeout=10)))
    th.start()
    time.sleep(0.5)
    assert th.is_alive(), "the request must wait, not fail"
    assert said and "still starting up" in said[0] and "the skills" in said[0] and "the main model" in said[0] and "seconds" in said[0]
    L._st["done"].update(skills_loaded=True, planner_warm=True)
    th.join(3)
    assert out["r"] is None and len(said) == 1


def test_request_after_boot_or_without_boot_is_not_delayed(fresh):
    t = time.time()
    assert L.await_brain(lambda m: pytest.fail("no message expected")) is None        # no boot running (unit tests, tools)
    L._st["booting"] = True
    L._st["done"].update({s: True for s in L.BRAIN_STAGES})
    assert L.await_brain(lambda m: pytest.fail("no message expected")) is None
    assert time.time() - t < 0.5


def test_wait_that_runs_out_says_what_went_wrong(fresh):
    L._st["booting"] = True
    L._st["error"] = "The assistant's main model couldn't be loaded onto the graphics card."
    msg = L.await_brain(None, timeout=0.3)
    assert msg and "not finished starting" in msg and "main model" in msg and "not lost" in msg
