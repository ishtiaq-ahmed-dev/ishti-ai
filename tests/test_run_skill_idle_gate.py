"""Q10: tests/run_skill.py waits for an idle GPU instead of fighting a live instance (policy a: run QA batches when idle)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import run_skill  # noqa: E402


def snap(used, total=16000):
    return lambda: {"used_mb": used, "total_mb": total}


def test_idle_gpu_starts_at_once():
    ok, g = run_skill.wait_for_idle_gpu(0, sampler=snap(4000))
    assert ok and g["used_mb"] == 4000


def test_unreadable_gpu_does_not_block():
    assert run_skill.wait_for_idle_gpu(0, sampler=lambda: None) == (True, None)


def test_busy_gpu_waits_then_gives_up():
    t = {"now": 0.0}
    sleeps = []
    ok, g = run_skill.wait_for_idle_gpu(25, sampler=snap(14800), sleep=lambda s: (sleeps.append(s), t.update(now=t["now"] + s)),
                                        clock=lambda: t["now"])
    assert ok is False and g["used_mb"] == 14800 and len(sleeps) == 3     # polled every 10 s until the 25 s budget ended


def test_gpu_that_frees_up_during_the_wait_starts_the_batch():
    readings = iter([15000, 14000, 5000])
    t = {"now": 0.0}
    ok, g = run_skill.wait_for_idle_gpu(60, sampler=lambda: {"used_mb": next(readings), "total_mb": 16000},
                                        sleep=lambda s: t.update(now=t["now"] + s), clock=lambda: t["now"])
    assert ok is True and g["used_mb"] == 5000
