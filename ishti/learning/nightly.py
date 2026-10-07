"""Nightly, offline conversion of owner feedback into inference and training artifacts.

This job never trains or promotes a model.  It prepares deterministic routing few-shots
and accepted preference pairs; promotion remains behind orchestrator.finetune's evaluation
gate and explicit command, with its existing rollback function exposed below.
"""
from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, Optional

from ishti.learning import store


def _default_output_dir() -> str:
    return os.path.join(os.path.dirname(os.path.abspath(store.db_path())), "finetune", "intelligence_nightly")


def _write_json(path: str, value: Any) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)


def run(output_dir: Optional[str] = None, client_id: Optional[str] = None) -> Dict[str, Any]:
    """Build routing few-shots and preference JSONL; never train or deploy."""
    folder = os.path.abspath(output_dir or _default_output_dir())
    os.makedirs(folder, exist_ok=True)
    scope_sql, scope_args = "", ()
    if client_id is not None:
        scope_sql, scope_args = " WHERE client_id=?", (str(client_id),)

    hints = store.rows(
        "SELECT prompt,preferred_skill,rejected_skill,evidence,confidence,client_id,source "
        f"FROM routing_hints{scope_sql} AND active=1 ORDER BY updated DESC"
        if scope_sql else
        "SELECT prompt,preferred_skill,rejected_skill,evidence,confidence,client_id,source "
        "FROM routing_hints WHERE active=1 ORDER BY updated DESC",
        scope_args)
    few_shots = [{"prompt": row["prompt"], "skill": row["preferred_skill"],
                  "avoid": row.get("rejected_skill") or "", "client_id": row.get("client_id") or "",
                  "evidence": int(row.get("evidence") or 1)} for row in hints]
    few_shots_path = os.path.join(folder, "routing_few_shots.json")
    _write_json(few_shots_path, few_shots)

    pair_where = " WHERE client_id=?" if client_id is not None else ""
    pairs = store.rows("SELECT * FROM preference_pairs" + pair_where + " ORDER BY ts", scope_args)
    lora_path = os.path.join(folder, "preference_pairs.jsonl")
    with open(lora_path, "w", encoding="utf-8") as handle:
        for row in pairs:
            example = {
                "messages": [
                    {"role": "user", "content": row["prompt"]},
                    {"role": "assistant", "content": row["accepted"]},
                ],
                "rejected": row["rejected"], "reason_type": row["reason_type"],
                "skill": row["skill"], "route": store.js(row["route"], {}),
                "client_id": row.get("client_id") or "",
            }
            handle.write(json.dumps(example, ensure_ascii=False) + "\n")

    metadata = {
        "generated_at": time.time(), "preference_pairs": len(pairs), "routing_hints": len(few_shots),
        "training": "offline_only", "auto_promote": False, "evaluate_before_promote": True,
        "evaluation_hook": "orchestrator.finetune.run_eval",
        "promotion_hook": "orchestrator.finetune.promote",
        "rollback_hook": "orchestrator.finetune.rollback",
        "note": "A candidate must pass the existing evaluation gate; this job never trains or promotes it.",
    }
    metadata_path = os.path.join(folder, "promotion_policy.json")
    _write_json(metadata_path, metadata)
    for row in pairs:
        store.write("UPDATE preference_pairs SET exported=1 WHERE id=?", (row["id"],))
    run_id = store.add("nightly_runs", ts=store.now(), output_dir=folder, pairs=len(pairs), hints=len(few_shots),
                       lora_path=lora_path, few_shots_path=few_shots_path, metadata_path=metadata_path,
                       status="prepared_not_promoted")
    return {"run_id": run_id, "output_dir": folder, "pairs": len(pairs), "hints": len(few_shots),
            "lora_path": lora_path, "few_shots_path": few_shots_path, "metadata_path": metadata_path,
            "auto_promote": False}


def run_if_due(output_dir: Optional[str] = None, now: Optional[float] = None,
               interval_s: float = 86400) -> Dict[str, Any]:
    """Run at most once per night/day; callers may invoke this safely on startup."""
    clock = float(now if now is not None else time.time())
    last = store.row("SELECT ts FROM nightly_runs ORDER BY ts DESC LIMIT 1")
    if last and clock - float(last["ts"] or 0) < float(interval_s):
        return {"status": "not_due", "last_run": float(last["ts"]), "auto_promote": False}
    return run(output_dir=output_dir)


def evaluate_candidate(adapter_dir: str) -> Dict[str, Any]:
    """Use the existing regression gate's evaluator without promoting anything."""
    from orchestrator import finetune
    score, detail = finetune.run_eval(adapter_dir)
    return {"score": score, "detail": detail, "passes_absolute_gate": score >= finetune.MIN_EVAL_SCORE,
            "promoted": False}


def rollback_adapter():
    """Delegate recovery to the existing archived-adapter rollback implementation."""
    from orchestrator import finetune
    return finetune.rollback()


if __name__ == "__main__":
    print(json.dumps(run_if_due(), ensure_ascii=False, indent=2))
