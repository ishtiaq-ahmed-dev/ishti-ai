"""Offline, read-only correction replay; no models, live skills, or owner stores.

    python tools/correction_eval.py
    python tools/correction_eval.py --example > _work/correction-example.json
    python tools/correction_eval.py --bundle _work/correction-example.json --output _work/correction-report.json

Bundles contain schema_version, dataset {version, cases}, baseline {version, rows},
and candidate {version, parent_version, rows}. Rows are complete snapshots ordered
newest first, with request/right strings. A case has id, request, expected_route
(skill name or null for abstention), split (regression/holdout), optional safety,
and optional available_skills. This evaluates only the correction stage of routing.
Candidate selection, T2 approval, real skill execution and activation are out of scope.
Exit 0 means eligible for review; 1 means rejected; 2 means invalid input/output.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
import re
import sys
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ishti import corrections  # noqa: E402

SCHEMA_VERSION = "1.0.0"
SCORER_VERSION = "unicode-corrections-v1"
_SKILL = re.compile(r"[a-z][a-z0-9_-]*")


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def example_bundle():
    """Illustrative synthetic data; not a statistically validated benchmark."""
    baseline = [
        {"request": "open the project browser", "right": "browser"},
        {"request": "use the portal", "right": "browser"},
        {"request": "use the portal", "right": "files"},
    ]
    additions = [
        {"request": "آج شہر کا موسم بتاؤ", "right": "weather"},
        {"request": "آج شہر کا موسم بتائیں", "right": "weather"},
        {"request": "زما د نن ورځې ټول یادښتونه وښایه", "right": "notes"},
        {"request": "زما د نن ورځې یادښتونه راوښایه", "right": "notes"},
    ]
    cases = [
        {"id": "english", "request": "OPEN THE PROJECT BROWSER!", "expected_route": "browser", "split": "regression"},
        {"id": "conflict", "request": "use the portal", "expected_route": None, "split": "regression", "safety": True},
        {"id": "disabled", "request": "open the project browser", "expected_route": None,
         "split": "regression", "safety": True, "available_skills": ["chat"]},
        {"id": "punctuation", "request": "?!...", "expected_route": None, "split": "holdout", "safety": True},
        {"id": "urdu", "request": "آج شہر کا موسم بتا دو", "expected_route": "weather", "split": "holdout"},
        {"id": "pashto", "request": "زما د نن ورځې یادښتونه وښایه", "expected_route": "notes", "split": "holdout"},
        {"id": "unrelated", "request": "یو بالکل بېل کار", "expected_route": None, "split": "holdout", "safety": True},
        {"id": "one_vote", "request": "open the project browzer", "expected_route": None, "split": "holdout"},
    ]
    return {"schema_version": SCHEMA_VERSION,
            "dataset": {"version": "synthetic-demo-v1", "cases": cases},
            "baseline": {"version": "baseline-demo-v1", "rows": baseline},
            "candidate": {"version": "candidate-demo-v1", "parent_version": "baseline-demo-v1",
                           "rows": additions + copy.deepcopy(baseline)}}


def _version(value):
    return isinstance(value, str) and 0 < len(value.strip()) <= 120


def _skill(value):
    return isinstance(value, str) and bool(_SKILL.fullmatch(value))


def validate(bundle):
    """Reject malformed or contaminated holdouts before any scoring."""
    if not isinstance(bundle, dict) or bundle.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"schema_version must be {SCHEMA_VERSION}")
    for key in ("dataset", "baseline", "candidate"):
        if not isinstance(bundle.get(key), dict) or not _version(bundle[key].get("version")):
            raise ValueError(f"{key} requires a nonempty version")
    baseline, candidate, dataset = (bundle[k] for k in ("baseline", "candidate", "dataset"))
    if candidate.get("parent_version") != baseline["version"] or candidate["version"] == baseline["version"]:
        raise ValueError("candidate requires a distinct version and the matching baseline parent_version")
    training_keys = set()
    for key in ("baseline", "candidate"):
        rows = bundle[key].get("rows")
        if not isinstance(rows, list) or len(rows) > 2000:
            raise ValueError(f"{key}.rows must be a list of at most 2000 correction rows")
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("request"), str) or len(row["request"]) > 500:
                raise ValueError(f"{key} correction request must be a string of at most 500 characters")
            if not corrections.norm(row["request"]) or not _skill(row.get("right")):
                raise ValueError(f"{key} correction requires meaningful request text and a skill name")
            training_keys.add(corrections.norm(row["request"]))
    cases = dataset.get("cases")
    if not isinstance(cases, list) or not 1 <= len(cases) <= 500:
        raise ValueError("dataset.cases must contain 1 to 500 cases")
    seen = set()
    for case in cases:
        if not isinstance(case, dict) or not _version(case.get("id")) or case["id"] in seen:
            raise ValueError("case IDs must be nonempty and unique")
        seen.add(case["id"])
        if not isinstance(case.get("request"), str) or len(case["request"]) > 2000:
            raise ValueError("case request must be a string of at most 2000 characters")
        if "expected_route" not in case or not (case["expected_route"] is None or _skill(case["expected_route"])):
            raise ValueError("expected_route must be a skill name or null (abstain)")
        if case.get("split") not in ("regression", "holdout") or not isinstance(case.get("safety", False), bool):
            raise ValueError("case requires regression/holdout split and a boolean safety flag")
        available = case.get("available_skills")
        if available is not None and (not isinstance(available, list) or not all(_skill(s) for s in available)):
            raise ValueError("available_skills must be a list of skill names")
        if available is not None and case["expected_route"] is not None and case["expected_route"] not in available:
            raise ValueError("expected_route must be available")
        if case["split"] == "holdout" and corrections.norm(case["request"]) in training_keys:
            raise ValueError(f"holdout {case['id']} overlaps a normalized correction request")
    if not any(c["split"] == "holdout" for c in cases) or not any(c.get("safety") for c in cases):
        raise ValueError("dataset requires at least one holdout and one safety case")


def evaluate(bundle):
    """Compare immutable in-memory snapshots; never read/write correction databases."""
    validate(bundle)
    rows = {key: [(r["request"], r["right"]) for r in bundle[key]["rows"]]
            for key in ("baseline", "candidate")}
    results = []
    for case in bundle["dataset"]["cases"]:
        rec = {k: case[k] for k in ("id", "request", "split", "expected_route")}
        rec["safety"] = case.get("safety", False)
        for variant in ("baseline", "candidate"):
            fixed = corrections.learned_from_rows(case["request"], rows[variant])
            # Mirrors router.route's availability check, without loading skills,
            # consulting Settings or executing a model/routing fallback.
            available = case.get("available_skills")
            actual = fixed if available is None or fixed in available else None
            rec[f"{variant}_route"] = actual
            rec[f"{variant}_ok"] = actual == case["expected_route"]
        results.append(rec)
    regressions = [r["id"] for r in results if r["baseline_ok"] and not r["candidate_ok"]]
    safety_failures = [r["id"] for r in results if r["safety"] and not r["candidate_ok"]]
    holdout_failures = [r["id"] for r in results if r["split"] == "holdout" and not r["candidate_ok"]]
    eligible = not (regressions or safety_failures or holdout_failures)
    return {
        "schema_version": SCHEMA_VERSION, "kind": "correction_replay_report",
        "scope": "correction-stage-only; no live stores, skills, models or promotion",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "scorer_version": SCORER_VERSION,
        "scorer_sha256": hashlib.sha256(Path(corrections.__file__).read_bytes()).hexdigest(),
        "dataset_version": bundle["dataset"]["version"], "dataset_sha256": digest(bundle["dataset"]),
        "baseline_version": bundle["baseline"]["version"], "baseline_sha256": digest(bundle["baseline"]),
        "candidate_version": bundle["candidate"]["version"], "candidate_sha256": digest(bundle["candidate"]),
        "parent_version": bundle["candidate"]["parent_version"],
        "total": len(results),
        "baseline_passed": sum(r["baseline_ok"] for r in results),
        "candidate_passed": sum(r["candidate_ok"] for r in results),
        "regressions": regressions, "safety_failures": safety_failures, "holdout_failures": holdout_failures,
        "verdict": "eligible_for_review" if eligible else "reject",
        "promotion_authorized": False, "results": results,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--bundle", type=Path, help="versioned synthetic dataset and correction snapshots as JSON")
    source.add_argument("--example", action="store_true", help="emit the illustrative input bundle instead of evaluating")
    parser.add_argument("--output", type=Path, help="write a new JSON artifact; existing files are never overwritten")
    args = parser.parse_args(argv)
    try:
        if args.bundle:
            with args.bundle.open("rb") as handle:
                raw = handle.read(4_000_001)
            if len(raw) > 4_000_000:
                raise ValueError("input bundle exceeds 4 MB")
            bundle = json.loads(raw)
        else:
            bundle = example_bundle()
        payload = bundle if args.example else evaluate(bundle)
        encoded = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        if args.output:
            with args.output.open("x", encoding="utf-8") as handle:
                handle.write(encoded)
        else:
            print(encoded, end="")
    except (OSError, ValueError, TypeError) as exc:
        parser.error(str(exc))
    return 0 if args.example or payload["verdict"] == "eligible_for_review" else 1


if __name__ == "__main__":
    raise SystemExit(main())
