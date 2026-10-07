from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.eval.harness import (
    capture_ollama_ps,
    concise_table,
    detect_dead_end,
    exact_json,
    exact_text,
    numeric,
    run_suites,
)
from tests.eval.run import safe_output_dir


ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(("reply", "expected"), [
    ("", True), ("Sorry, I can't do that.", True), ("Did you mean:", True),
    ("I couldn't find it. Which folder should I search?", False),
    ("No results; try a narrower filename.", False), ("Done.", False),
])
def test_dead_end_detector(reply, expected):
    assert detect_dead_end(reply)["dead_end"] is expected


def test_exact_validators_do_not_coerce_unless_declared():
    assert exact_text("42", "42")
    assert not exact_text(42, "42")
    assert exact_json('{"b": 2, "a": 1}', {"a": 1, "b": 2})
    assert not exact_json("[2, 1]", [1, 2])
    assert numeric("4.001", 4, 0.01)
    assert not numeric("4.1", 4, 0.01)


def test_fast_suites_are_offline_and_report_real_denominators(monkeypatch):
    monkeypatch.setenv("ISHTI_TEST_MODE", "1")
    monkeypatch.setenv("ISHTI_SCRATCH", "1")
    for name in ("dead_end", "correctness", "files", "memory"):
        report = run_suites(name)
        assert report["offline"] is True
        assert report["results"][0]["case_count"] > 0
        for value in report["results"][0]["metrics"].values():
            assert value["status"] in {"measured", "unmeasured"}
            if value["status"] == "measured":
                assert value["denominator"] > 0
        assert name in concise_table(report)


def test_understanding_reports_required_corpus_dimensions(monkeypatch):
    monkeypatch.setenv("ISHTI_TEST_MODE", "1")
    monkeypatch.setenv("ISHTI_SCRATCH", "1")
    suite = run_suites("understanding")["results"][0]
    assert suite["case_count"] >= 150
    counts = suite["metadata"]["coverage_counts"]
    assert all(counts[name] > 0 for name in (
        "english", "urdu", "pashto", "roman_urdu", "mixed", "typo", "vague_ref", "multi_step", "ambiguity"
    ))
    assert suite["metrics"]["argument_accuracy"]["status"] == "unmeasured"


def test_expanded_product_suites_report_matrices_and_isolation(monkeypatch):
    monkeypatch.setenv("ISHTI_TEST_MODE", "1")
    monkeypatch.setenv("ISHTI_SCRATCH", "1")
    monkeypatch.setenv("ISHTI_INTEL_SAFE_FILE_OUTPUTS", "0")
    report = run_suites("all")
    suites = {item["suite"]: item for item in report["results"]}
    scenarios = {row["scenario"] for row in suites["dead_end"]["metadata"]["product_scenarios"]}
    assert scenarios == {"missing_file", "locked_file", "tool_failure", "network_unavailable", "unknown_format"}
    product_rows = suites["dead_end"]["metadata"]["product_scenarios"]
    assert all(row["boundary_reached"] and not row["fallback_executed"] for row in product_rows)
    assert suites["dead_end"]["metrics"]["dependency_boundary_handling_rate"]["value"] == 0.2
    assert suites["dead_end"]["metrics"]["product_failure_handling_rate"]["status"] == "unmeasured"
    assert suites["dead_end"]["metrics"]["orchestrated_recovery_rate"]["status"] == "unmeasured"
    assert suites["correctness"]["metrics"]["product_exact_accuracy"]["denominator"] >= 6
    matrix = suites["files"]["metadata"]["operation_matrix"]
    assert set(matrix) == {"py", "js", "ts", "html", "css", "json", "csv", "txt", "md", "ipynb",
                           "docx", "xlsx", "pdf", "pptx", "doc", "xls"}
    assert all(set(row) == {"create", "read", "edit", "convert", "validate"} for row in matrix.values())
    assert all(cell["status"] in {"measured", "unmeasured", "unsupported"}
               for row in matrix.values() for cell in row.values())
    memory_cases = {row["case"] for row in suites["memory"]["metadata"]["behavior_checks"]}
    assert memory_cases == {"latest_wins", "session_isolation", "process_restart_persistence"}
    assert suites["memory"]["metrics"]["arch2_profile_client_isolation"]["status"] == "unmeasured"
    assert all(row["status"] == "unmeasured" for row in suites["memory"]["metadata"]["unmeasured_checks"])
    overwrite = suites["files"]["metadata"]["overwrite_regressions"]
    assert {row["extension"] for row in overwrite} == {"docx", "pptx", "pdf"}
    assert all(row["overwrite_risk"] for row in overwrite)
    assert suites["files"]["metrics"]["overwrite_defect_rate"]["value"] == 1.0
    assert matrix["json"]["read"]["status"] == "unsupported"
    assert matrix["ipynb"]["read"]["status"] == "unsupported"
    assert matrix["js"]["validate"]["upload_accepted"] is False
    assert suites["files"]["metadata"]["ipynb_support"]["product_execute"].startswith("unmeasured")


def test_safe_output_candidate_removes_all_measured_overwrite_defects(monkeypatch):
    monkeypatch.setenv("ISHTI_TEST_MODE", "1")
    monkeypatch.setenv("ISHTI_SCRATCH", "1")
    monkeypatch.setenv("ISHTI_INTEL_SAFE_FILE_OUTPUTS", "1")
    suite = run_suites("files")["results"][0]
    assert suite["metrics"]["overwrite_defect_rate"]["value"] == 0.0
    assert all(not row["overwrite_risk"] for row in suite["metadata"]["overwrite_regressions"])


def test_repeat_aggregates_median_metrics(monkeypatch):
    monkeypatch.setenv("ISHTI_TEST_MODE", "1")
    monkeypatch.setenv("ISHTI_SCRATCH", "1")
    report = run_suites("dead_end", repeat=3)
    assert len(report["runs"]) == 3
    assert report["aggregate"]["median_latency_ms"] >= 0
    metric = report["aggregate"]["metrics"]["dead_end"]["detector_accuracy"]
    assert metric == {"status": "measured", "median_value": 1.0, "measured_runs": 3, "total_runs": 3}


def test_ollama_capture_unavailable_is_unmeasured(monkeypatch):
    def unavailable(*_args, **_kwargs):
        raise OSError("synthetic unavailable")

    monkeypatch.setattr("urllib.request.urlopen", unavailable)
    result = capture_ollama_ps()
    assert result["status"] == "unmeasured"
    assert "unavailable" in result["reason"].lower()


def test_runner_writes_json_and_table_to_caller_path(tmp_path):
    out = ROOT / "tests" / "eval" / "_runner_test_output"
    out.mkdir(exist_ok=True)
    env = dict(os.environ, ISHTI_TEST_MODE="0", ISHTI_SCRATCH="0")
    proc = subprocess.run(
        [sys.executable, str(ROOT / "tests" / "eval" / "run.py"), "dead_end", "--repeat", "2",
         "--output", str(out)],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=30,
    )
    try:
        assert proc.returncode == 0, proc.stderr
        data = json.loads((out / "eval_dead_end.json").read_text(encoding="utf-8"))
        assert data["environment"] == {"ISHTI_TEST_MODE": "1", "ISHTI_SCRATCH": "1"}
        assert data["repeat"] == 2 and len(data["runs"]) == 2
        assert (out / "eval_dead_end.txt").read_text(encoding="utf-8").startswith("suite")
    finally:
        for path in out.glob("eval_dead_end.*"):
            path.unlink()
        out.rmdir()


def test_output_path_must_remain_in_repo(tmp_path):
    with pytest.raises(ValueError, match="inside the repository"):
        safe_output_dir(str(tmp_path))
