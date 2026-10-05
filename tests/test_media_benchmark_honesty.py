"""The media benchmark must measure, not compare ground truth to itself (NEEDS Item 3)."""
import importlib.util
import json
import os

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SPEC = importlib.util.spec_from_file_location(
    "benchmark_multilingual_media",
    os.path.join(ROOT, "tools", "qa", "benchmark_multilingual_media.py"))
bench = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bench)


def test_wer_and_cer_are_real_edit_distances():
    assert bench.calculate_wer("a b c d", "a b c d") == 0.0
    assert bench.calculate_wer("a b c d", "a b x d") == 0.25
    assert bench.calculate_cer("abcd", "abxd") == 0.25
    assert bench.calculate_wer("", "") == 0.0
    assert bench.calculate_wer("", "spurious") == 1.0


def test_missing_audio_assets_report_unmeasured_not_zero(monkeypatch, tmp_path):
    corpus = tmp_path / "corpus"
    (corpus / "transcripts").mkdir(parents=True)
    (corpus / "fixtures").mkdir()
    (corpus / "transcripts" / "ground_truth_en.json").write_text(json.dumps({
        "name": "English Ground Truth Corpus",
        "samples": [{"id": "en_01", "category": "task", "text": "Schedule a meeting with Bilal.", "entities": {}}],
    }), encoding="utf-8")
    (corpus / "fixtures" / "invoice_ground_truth.json").write_text(
        json.dumps({"document_id": "INV-1", "date": "2026-10-01", "subtotal": 1.0,
                    "total_amount": 1.05, "currency": "PKR", "payment_status": "PAID"}), encoding="utf-8")
    monkeypatch.setattr(bench, "DATA_CORPUS", str(corpus))
    monkeypatch.setattr(bench, "REPORT_JSON", str(tmp_path / "report.json"))
    monkeypatch.setattr(bench, "REPORT_MD", str(tmp_path / "report.md"))
    monkeypatch.setattr(bench, "_transcribe", lambda _path: pytest.fail("no audio asset exists"))
    monkeypatch.setattr(bench, "_read_invoice_text", lambda: None)

    result = bench.run_benchmark()
    assert result["summary"]["measured_samples"] == 0
    assert result["summary"]["mean_wer"] == "unmeasured"
    assert result["summary"]["accuracy_verdict"].startswith("unmeasured")
    assert result["languages"]["en"]["samples"][0]["wer"] == "unmeasured"
    assert result["ocr_document"]["measured"] is False
    assert result["ocr_document"]["field_match_rate"] == "unmeasured"


def test_real_hypothesis_produces_a_measured_wer(monkeypatch, tmp_path):
    corpus = tmp_path / "corpus"
    (corpus / "transcripts").mkdir(parents=True)
    (corpus / "audio").mkdir()
    (corpus / "audio" / "en_01.wav").write_bytes(b"RIFF synthetic")
    (corpus / "transcripts" / "ground_truth_en.json").write_text(json.dumps({
        "name": "English", "samples": [{"id": "en_01", "category": "task", "text": "one two three four", "entities": {}}],
    }), encoding="utf-8")
    monkeypatch.setattr(bench, "DATA_CORPUS", str(corpus))
    monkeypatch.setattr(bench, "REPORT_JSON", str(tmp_path / "report.json"))
    monkeypatch.setattr(bench, "REPORT_MD", str(tmp_path / "report.md"))
    monkeypatch.setattr(bench, "_transcribe", lambda _path: "one two four")

    result = bench.run_benchmark()
    assert result["summary"]["measured_samples"] == 1
    assert result["summary"]["mean_wer"] == 0.25
