"""Heldout correction replay is reproducible, truthful, isolated, and never a promotion."""
import copy
import json

import pytest

from ishti import corrections
from tools import correction_eval as evaluator


def test_demo_scores_real_scorer_without_live_stores(monkeypatch):
    def forbidden():
        raise AssertionError("evaluation touched the owner's correction store")

    monkeypatch.setattr(corrections, "_db", forbidden)
    bundle = evaluator.example_bundle()
    saved = copy.deepcopy(bundle)
    report = evaluator.evaluate(bundle)
    assert bundle == saved
    assert report["verdict"] == "eligible_for_review"
    assert report["candidate_passed"] == report["total"]
    assert report["candidate_passed"] > report["baseline_passed"]
    assert report["promotion_authorized"] is False
    assert report["dataset_sha256"] == evaluator.digest(bundle["dataset"])


def test_candidate_cannot_hide_a_regression_with_aggregate_gain():
    bundle = evaluator.example_bundle()
    bundle["candidate"]["rows"][4]["right"] = "files"
    report = evaluator.evaluate(bundle)
    assert report["candidate_passed"] > report["baseline_passed"]
    assert report["verdict"] == "reject"
    assert "english" in report["regressions"]


def test_safety_failure_rejects_candidate_even_when_baseline_also_failed():
    bundle = evaluator.example_bundle()
    for variant in ("baseline", "candidate"):
        bundle[variant]["rows"].append({"request": "use the portal", "right": "browser"})
    report = evaluator.evaluate(bundle)
    assert "conflict" in report["safety_failures"]
    assert "conflict" not in report["regressions"]
    assert report["verdict"] == "reject"


def test_heldout_overlap_is_detected_after_unicode_normalization():
    bundle = evaluator.example_bundle()
    bundle["dataset"]["cases"].append({"id": "contaminated", "request": "ＯＰＥＮ THE PROJECT BROWSER!",
                                       "expected_route": "browser", "split": "holdout"})
    with pytest.raises(ValueError, match="overlaps"):
        evaluator.evaluate(bundle)


def test_missing_lineage_or_holdout_fails_closed():
    bundle = evaluator.example_bundle()
    bundle["candidate"]["parent_version"] = "wrong-baseline"
    with pytest.raises(ValueError, match="parent_version"):
        evaluator.evaluate(bundle)
    bundle = evaluator.example_bundle()
    for case in bundle["dataset"]["cases"]:
        case["split"] = "regression"
    with pytest.raises(ValueError, match="at least one holdout"):
        evaluator.evaluate(bundle)


def test_hashes_reproduce_and_changed_candidates_have_different_identity():
    bundle = evaluator.example_bundle()
    first = evaluator.evaluate(bundle)
    second = evaluator.evaluate(copy.deepcopy(bundle))
    for key in ("scorer_sha256", "dataset_sha256", "baseline_sha256", "candidate_sha256"):
        assert first[key] == second[key]
    bundle["candidate"]["rows"].append({"request": "another correction", "right": "notes"})
    third = evaluator.evaluate(bundle)
    assert third["candidate_sha256"] != first["candidate_sha256"]
    assert third["baseline_sha256"] == first["baseline_sha256"]


def test_cli_roundtrip_and_existing_outputs_are_preserved(tmp_path):
    source = tmp_path / "input.json"
    output = tmp_path / "report.json"
    assert evaluator.main(["--example", "--output", str(source)]) == 0
    original = source.read_bytes()
    assert evaluator.main(["--bundle", str(source), "--output", str(output)]) == 0
    assert json.loads(output.read_text())["promotion_authorized"] is False
    with pytest.raises(SystemExit) as caught:
        evaluator.main(["--bundle", str(source), "--output", str(source)])
    assert caught.value.code == 2
    assert source.read_bytes() == original


@pytest.mark.parametrize("change", [
    lambda b: b["dataset"]["cases"].append(b["dataset"]["cases"][0]),
    lambda b: b["candidate"]["rows"].append({"request": "?!", "right": "browser"}),
    lambda b: b["dataset"]["cases"][0].update(expected_route=23),
])
def test_malformed_input_is_rejected(change):
    bundle = evaluator.example_bundle()
    change(bundle)
    with pytest.raises(ValueError):
        evaluator.evaluate(bundle)
