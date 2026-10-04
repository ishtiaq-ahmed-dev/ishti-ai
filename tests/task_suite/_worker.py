"""One task, one subprocess: drives the real dispatcher for the task suite.

Run from tests/task_suite/run_tasks.py (not directly). Prints one JSON line.
ISHTI_TEST_MODE=1: T2 actions are blocked at the yes-gate, so the suite can never
send, delete or buy anything for real while still exercising the whole path.
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
os.environ["ISHTI_TEST_MODE"] = "1"


def main():
    text = sys.argv[1]
    sid = sys.argv[2] if len(sys.argv) > 2 else None
    from ishti import dispatcher
    try:
        res = dispatcher.handle(text, session_id=sid, channel="text") or {}
        out = {"ok": True, "reply": str(res.get("final_answer") or ""), "skill": res.get("skill"),
               "route": (res.get("route") or {}).get("skill"), "source": (res.get("route") or {}).get("source"),
               "ms": res.get("latency_ms")}
    except BaseException as exc:  # noqa: BLE001 - a crashed worker IS a test result
        out = {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:300]}", "reply": "", "skill": None}
    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
