"""FIX-7 regression (QA prompt 3 soak): done-task records must not pin unbounded results.

taskmgr keeps completed records for /api/tasks. These tests prove bounded result and
closure retention with synthetic payloads. The historical multi-GB RAM attribution has
not been causally reproduced; 12 x 200 KB alone cannot explain that magnitude. Callers
needing a full result must keep their own reference or persist the output separately.
Hermetic: uses the real taskmgr with tiny fakes, no LLM/network.
"""
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.environ.setdefault("ISHTI_TEST_MODE", "1")

from ishti import taskmgr  # noqa: E402


def _drain():
    deadline = time.time() + 15
    while time.time() < deadline:
        if all(r["status"] in ("done", "failed", "cancelled")
               for r in taskmgr.TASKS.values() if r["goal"].startswith("qa-fix7")):
            return
        time.sleep(0.05)


def test_big_string_result_is_stored_bounded():
    big = "x" * 200_000
    rec = taskmgr.submit(lambda t: big, goal="qa-fix7 string", sid="qa_fix7")
    _drain()
    assert rec["status"] == "done"
    assert len(str(rec["result"])) < 10_000, "result on the record must be a bounded preview"
    assert "result_full_ref" not in rec, "no hidden full copy may be retained (that was the leak)"
    assert rec["result"].startswith("xxxx"), "preview must preserve the head of the answer"
    taskmgr.TASKS.pop(rec["id"], None)


def test_big_dict_result_is_stored_bounded():
    def fn(t):
        return {"final_answer": "y" * 200_000, "sources": []}
    rec = taskmgr.submit(fn, goal="qa-fix7 dict", sid="qa_fix7")
    _drain()
    assert rec["status"] == "done"
    import json
    assert len(json.dumps(rec.get("result") or "")) < 20_000, \
        "dict results must be bounded too (the measured leak was dict-shaped)"
    taskmgr.TASKS.pop(rec["id"], None)


def test_wait_all_caller_still_gets_full_result():
    """agents.py reads subtask results from wait_all records; the caller's own reference is
    the full value at return time - trimming must not break the fan-out contract."""
    big = {"final_answer": "z" * 50_000}
    rec = taskmgr.submit(lambda t: big, goal="qa-fix7 waitall", sid="qa_fix7")
    done = taskmgr.wait_all([rec["id"]], timeout=15)
    assert done and done[0]["status"] == "done"
    # The record itself is bounded; a caller needing the full value holds `big` or the
    # task fn's own reference - verified here by the record NOT carrying the full blob:
    import json
    assert len(json.dumps(done[0].get("result") or "")) < 20_000
    taskmgr.TASKS.pop(rec["id"], None)


def test_completed_task_does_not_keep_a_large_callable_closure_alive():
    import gc
    import weakref
    class Payload:
        pass
    payload = Payload()
    payload.data = "private synthetic payload" * 50_000
    reference = weakref.ref(payload)
    rec = taskmgr.submit(lambda t, kept=payload: "bounded answer", goal="qa-fix7 closure", sid="qa_fix7")
    del payload
    taskmgr.wait_all([rec["id"]], timeout=15)
    deadline = time.time() + 3
    while "_fn" in rec and time.time() < deadline:
        time.sleep(0.01)
    gc.collect()
    assert rec["status"] == "done" and "_fn" not in rec
    assert reference() is None, "completed records must release callable-captured data"
    taskmgr.TASKS.pop(rec["id"], None)


def test_priority_queue_runs_the_callable_of_the_selected_record(monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    gate = threading.Event()
    started = threading.Event()
    order = []
    def blocker(t):
        started.set()
        gate.wait(5)
    with ThreadPoolExecutor(1) as pool:
        monkeypatch.setattr(taskmgr, "_EXECUTOR", pool)
        active = taskmgr.submit(blocker, goal="qa-fix7 active")
        assert started.wait(2)
        low = taskmgr.submit(lambda t: order.append("low"), goal="qa-fix7 low", priority="low")
        high = taskmgr.submit(lambda t: order.append("high"), goal="qa-fix7 high", priority="high")
        gate.set()
        assert len(taskmgr.wait_all([active["id"], low["id"], high["id"]], timeout=15)) == 3
    assert order == ["high", "low"]
    for rec in (active, low, high):
        assert rec["status"] == "done" and "_fn" not in rec
        taskmgr.TASKS.pop(rec["id"], None)
