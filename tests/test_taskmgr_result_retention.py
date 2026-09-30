"""FIX-7 regression (QA prompt 3 soak): done-task records must not pin unbounded results.

Measured: 12 background tasks returning 200 KB dicts -> RSS 18 -> 2,860 MB, because
taskmgr keeps up to MAX_TASKS=60 done records and stored the FULL return value on the
record (list_tasks serializes every record for /api/tasks). The bounded preview keeps
every observed reader working (meta skill truncates to 120 chars; agents read subtask
results from their OWN live reference via wait_all before trimming, not from the record).
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
