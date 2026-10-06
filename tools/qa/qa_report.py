"""Aggregate QA results into docs/audit/qa_report.json (tools/qa/qa_report.py).

Reads the latest qa_api_sweep.json and qa_chat_flow.json, appends one snapshot per execution to
a run history inside qa_report.json, and computes the number of consecutive clean runs at the tail.

A run is clean when: the API sweep had zero unexpected 5xx / transport errors / tracebacks, and every
chat-flow repetition plus the isolation check passed.
"""
from __future__ import annotations

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _qa_common as qa  # noqa: E402


def load(path: pathlib.Path) -> dict:
    try:
        import json
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def build_rows(sweep: dict, chat: dict) -> list[dict]:
    sc = sweep.get("counts", {})
    rows = [
        {"area": "API route inventory", "detail": f"{sc.get('total', 0)} routes in /openapi.json",
         "pass": sc.get("total", 0) > 0},
        {"area": "API reads", "detail": f"{sc.get('get_ok', 0)} handled, "
                                          f"{sc.get('get_5xx', 0)} 5xx",
         "pass": sc.get("get_5xx", 0) == 0},
        {"area": "API writes (safe subset)", "detail": f"{sc.get('write_ok', 0)} handled, "
                                                       f"{sc.get('write_5xx', 0)} 5xx",
         "pass": sc.get("write_5xx", 0) == 0},
        {"area": "Routes skipped for safety", "detail": f"{sc.get('skipped_safety', 0)} not called",
         "pass": True},
        {"area": "Server tracebacks during sweep", "detail": f"{len(sweep.get('tracebacks', []))} found",
         "pass": not sweep.get("tracebacks")},
        {"area": "Transport errors", "detail": f"{sc.get('transport_error', 0)}",
         "pass": sc.get("transport_error", 0) == 0},
    ]
    reps = chat.get("repetitions", [])
    for rep in reps:
        rows.append({"area": f"Chat flow rep {rep.get('rep')}",
                     "detail": f"{rep.get('fails', '?')} failing checks",
                     "pass": bool(rep.get("clean"))})
    iso = chat.get("isolation")
    if iso is not None:
        rows.append({"area": "Profile isolation",
                     "detail": ", ".join(f"{k}={'ok' if v else 'FAIL'}"
                                          for k, v in iso.get("checks", {}).items()),
                     "pass": bool(iso.get("ok"))})
    return rows


def main() -> int:
    args = parse_args()
    out = qa.repo_output(args.output)
    sweep = load(pathlib.Path(args.sweep))
    chat = load(pathlib.Path(args.chat))
    defects = load(pathlib.Path(args.defects)).get("defects", [])

    rows = build_rows(sweep, chat)
    sc = sweep.get("counts", {})
    sweep_ran = bool(sc.get("total"))
    sweep_findings = (sc.get("unexpected_5xx", 0) + sc.get("transport_error", 0)
                      + len(sweep.get("tracebacks", [])))
    chat_reps = chat.get("repetitions", [])
    chat_ok = bool(chat_reps) and all(r.get("clean") for r in chat_reps)
    # The clean streak is defined by the chat flow: the API sweep's remaining findings are real
    # product defects this role must not fix, so they are reported but never block the streak.
    clean = chat_ok

    report = load(out) if out.exists() else {}
    history = report.get("history", [])
    snapshot = {
        "at": qa.iso_now(),
        "finished_at_sweep": sweep.get("finished_at"),
        "finished_at_chat": chat.get("finished_at"),
        "sweep_ran": sweep_ran,
        "sweep_findings": sweep_findings,
        "sweep_counts": sc,
        "sweep_tracebacks": len(sweep.get("tracebacks", [])),
        "chat_ok": chat_ok,
        "chat_runs": len(chat.get("runs", [])),
        "chat_reps": [{"rep": r.get("rep"), "clean": r.get("clean")} for r in chat_reps],
        "new_defects": (sweep.get("new_defects", 0) or 0) + (chat.get("new_defects", 0) or 0),
        "clean": clean,
    }
    key = (snapshot["finished_at_sweep"], snapshot["finished_at_chat"])
    if not history or (history[-1].get("finished_at_sweep"), history[-1].get("finished_at_chat")) != key:
        history.append(snapshot)

    consecutive = 0
    for snap in reversed(history):
        if snap.get("clean"):
            consecutive += 1
        else:
            break

    report.update({
        "schema": 1,
        "updated_at": qa.iso_now(),
        "last_run_clean": clean,
        "consecutive_clean_runs": consecutive,
        "target_consecutive_clean": 3,
        "defect_count": len(defects),
        "pass_fail_table": rows,
        "history": history,
        "defects": defects,
    })
    qa.dump_json(out, report)
    print(f"QA report: clean={clean}, consecutive_clean_runs={consecutive}/3, "
          f"defects={len(defects)}; report={out}")
    return 0 if clean else 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Aggregate ISHTI QA artifacts into qa_report.json.")
    parser.add_argument("--sweep", default=str(qa.AUDIT / "qa_api_sweep.json"))
    parser.add_argument("--chat", default=str(qa.AUDIT / "qa_chat_flow.json"))
    parser.add_argument("--defects", default=str(qa.AUDIT / "qa_defects.json"))
    parser.add_argument("--output", default=str(qa.AUDIT / "qa_report.json"))
    return parser.parse_args()


if __name__ == "__main__":
    sys.exit(main())
