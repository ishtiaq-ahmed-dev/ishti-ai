"""Benchmark runner for the local synthetic media corpus (NEEDS.md Item 3).

Honest by construction: a metric is only reported as *measured* when a real
media asset exists and the local model actually produced a hypothesis for it.
Ground truth is never compared against itself, and invoice JSON keys are value
checked against OCR output rather than counted for merely existing.

Measures when assets are present:
1. Multilingual transcript Word Error Rate (WER) and Character Error Rate (CER).
2. Document / invoice OCR key-value extraction accuracy.
3. Processing latency per sample in milliseconds.
Missing assets/failed models are reported as "unmeasured", never as 0% / 100%.
Produces structured artifacts in reports/.
"""
import os
import sys
import json
import time
from typing import List, Dict, Any, Optional

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

DATA_CORPUS = os.path.join(ROOT_DIR, "data", "synthetic_corpus")
REPORT_JSON = os.path.join(ROOT_DIR, "reports", "multilingual_media_benchmark.json")
REPORT_MD = os.path.join(ROOT_DIR, "reports", "multilingual_media_benchmark.md")

AUDIO_EXT = (".wav", ".mp3", ".ogg", ".opus", ".m4a", ".aac")
MEDIA_EXT = (".pdf", ".png", ".jpg", ".jpeg", ".webp")
UNMEASURED = "unmeasured"
INVOICE_FIELDS = ("document_id", "date", "subtotal", "total_amount", "currency", "payment_status")


def levenshtein_distance(ref: List[str], hyp: List[str]) -> int:
    """Computes standard edit distance between two sequences."""
    r_len, h_len = len(ref), len(hyp)
    d = [[0] * (h_len + 1) for _ in range(r_len + 1)]
    for i in range(r_len + 1):
        d[i][0] = i
    for j in range(h_len + 1):
        d[0][j] = j
    for i in range(1, r_len + 1):
        for j in range(1, h_len + 1):
            cost = 0 if ref[i - 1] == hyp[j - 1] else 1
            d[i][j] = min(
                d[i - 1][j] + 1,       # deletion
                d[i][j - 1] + 1,       # insertion
                d[i - 1][j - 1] + cost  # substitution
            )
    return d[r_len][h_len]


def calculate_wer(reference_text: str, hypothesis_text: str) -> float:
    """Computes Word Error Rate: (S + D + I) / N."""
    ref_words = reference_text.strip().split()
    hyp_words = hypothesis_text.strip().split()
    if not ref_words:
        return 0.0 if not hyp_words else 1.0
    dist = levenshtein_distance(ref_words, hyp_words)
    return min(1.0, dist / len(ref_words))


def calculate_cer(reference_text: str, hypothesis_text: str) -> float:
    """Computes Character Error Rate."""
    ref_chars = list(reference_text.replace(" ", ""))
    hyp_chars = list(hypothesis_text.replace(" ", ""))
    if not ref_chars:
        return 0.0 if not hyp_chars else 1.0
    dist = levenshtein_distance(ref_chars, hyp_chars)
    return min(1.0, dist / len(ref_chars))


def _transcribe(path: str) -> Optional[str]:
    """Local speech-to-text hypothesis for a real audio file, or None on failure."""
    try:
        from ishti import voice
        with open(path, "rb") as f:
            return voice.transcribe(f.read()) or ""
    except Exception:
        return None


def _sample_hypothesis(sample: Dict[str, Any]) -> Optional[str]:
    """A real model hypothesis for this sample, only if its audio asset exists."""
    for ext in AUDIO_EXT:
        path = os.path.join(DATA_CORPUS, "audio", str(sample.get("id", "")) + ext)
        if os.path.exists(path):
            return _transcribe(path)
    return None


def _read_invoice_text() -> Optional[str]:
    """OCR/PDF text for the real invoice fixture, or None when absent/failed."""
    for ext in MEDIA_EXT:
        path = os.path.join(DATA_CORPUS, "fixtures", "invoice" + ext)
        if not os.path.exists(path):
            continue
        try:
            from ishti import wa_media_reader
            with open(path, "rb") as f:
                data = f.read()
            kind = "document" if ext == ".pdf" else "image"
            return wa_media_reader.read_bytes(kind, data).get("text", "") or None
        except Exception:
            return None
    return None


def run_benchmark() -> Dict[str, Any]:
    os.makedirs(os.path.dirname(REPORT_JSON), exist_ok=True)
    results: Dict[str, Any] = {
        "timestamp": time.time(),
        "date": time.strftime("%Y-%m-%d %H:%M:%S"),
        "corpus": DATA_CORPUS,
        "languages": {},
        "ocr_document": {},
        "summary": {}
    }

    measured_samples = 0
    total_wer = 0.0
    total_cer = 0.0
    total_latency_ms = 0.0

    for lang in ("en", "ur", "ps", "ru"):
        path = os.path.join(DATA_CORPUS, "transcripts", f"ground_truth_{lang}.json")
        if not os.path.exists(path):
            continue
        with open(path, "r", encoding="utf-8") as f:
            corpus = json.load(f)
        lang_samples = corpus.get("samples", [])
        lang_wer = lang_cer = lang_latency = 0.0
        measured = 0
        sample_results = []
        for sample in lang_samples:
            ref_text = sample["text"]
            t0 = time.perf_counter()
            hyp_text = _sample_hypothesis(sample)
            elapsed_ms = (time.perf_counter() - t0) * 1000
            row = {"id": sample["id"], "category": sample["category"], "measured": hyp_text is not None,
                   "latency_ms": round(elapsed_ms, 2), "entities_count": len(sample.get("entities", {}))}
            if hyp_text is not None:
                wer, cer = calculate_wer(ref_text, hyp_text), calculate_cer(ref_text, hyp_text)
                lang_wer += wer
                lang_cer += cer
                lang_latency += elapsed_ms
                measured += 1
                row.update({"wer": round(wer, 4), "cer": round(cer, 4)})
            else:
                row.update({"wer": UNMEASURED, "cer": UNMEASURED})
            sample_results.append(row)
        count = len(lang_samples)
        measured_samples += measured
        total_wer += lang_wer
        total_cer += lang_cer
        total_latency_ms += lang_latency
        results["languages"][lang] = {
            "name": corpus.get("name"),
            "sample_count": count,
            "measured_count": measured,
            "avg_wer": round(lang_wer / measured, 4) if measured else UNMEASURED,
            "avg_cer": round(lang_cer / measured, 4) if measured else UNMEASURED,
            "avg_latency_ms": round(lang_latency / measured, 2) if measured else UNMEASURED,
            "samples": sample_results
        }

    inv_path = os.path.join(DATA_CORPUS, "fixtures", "invoice_ground_truth.json")
    if os.path.exists(inv_path):
        with open(inv_path, "r", encoding="utf-8") as f:
            inv_gt = json.load(f)
        t0 = time.perf_counter()
        ocr_text = _read_invoice_text()
        ocr_time_ms = (time.perf_counter() - t0) * 1000
        if ocr_text is None:
            results["ocr_document"] = {
                "document_id": inv_gt.get("document_id"),
                "evaluated_fields": list(INVOICE_FIELDS),
                "field_match_rate": UNMEASURED,
                "measured": False,
                "reason": "No invoice image/PDF fixture or OCR hypothesis was available."
            }
        else:
            flat = ocr_text.replace(" ", "").replace(",", "").lower()
            matched = sum(1 for field in INVOICE_FIELDS
                          if field in inv_gt and str(inv_gt[field]).replace(" ", "").replace(",", "").lower() in flat)
            results["ocr_document"] = {
                "document_id": inv_gt.get("document_id"),
                "evaluated_fields": list(INVOICE_FIELDS),
                "field_match_rate": round(matched / len(INVOICE_FIELDS), 4),
                "matched_fields": matched,
                "measured": True,
                "latency_ms": round(ocr_time_ms, 2)
            }

    results["summary"] = {
        "total_languages": len(results["languages"]),
        "total_samples": sum(d["sample_count"] for d in results["languages"].values()),
        "measured_samples": measured_samples,
        "mean_wer": round(total_wer / measured_samples, 4) if measured_samples else UNMEASURED,
        "mean_cer": round(total_cer / measured_samples, 4) if measured_samples else UNMEASURED,
        "mean_latency_ms": round(total_latency_ms / measured_samples, 2) if measured_samples else UNMEASURED,
        "accuracy_verdict": "measured" if measured_samples else "unmeasured - no synthetic audio assets and no live model run"
    }

    with open(REPORT_JSON, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    def pct(value: Any) -> str:
        return f"{value * 100:.1f}%" if isinstance(value, (int, float)) else str(value)

    summary = results["summary"]
    md = [
        "# Multilingual Media & Synthetic Ground Truth Benchmark Report",
        f"\n**Evaluated:** {results['date']}",
        f"**Corpus Location:** `{DATA_CORPUS}`",
        f"**Verdict:** {summary['accuracy_verdict']}\n",
        "## Summary Metrics\n",
        f"- **Total Languages Tested:** {summary['total_languages']} (English, Urdu, Pashto, Roman Urdu)",
        f"- **Total Ground Truth Samples:** {summary['total_samples']}",
        f"- **Samples With A Real Model Hypothesis:** {summary['measured_samples']}",
        f"- **Mean Word Error Rate (WER):** {pct(summary['mean_wer'])}",
        f"- **Mean Character Error Rate (CER):** {pct(summary['mean_cer'])}",
        f"- **Mean Processing Latency:** {summary['mean_latency_ms']} ms\n",
        "## Language Breakdown\n",
        "| Language | Code | Samples | Measured | Mean WER | Mean CER | Latency (ms) |",
        "|---|---|---|---|---|---|---|"
    ]
    for code, data in results["languages"].items():
        md.append(f"| {data['name']} | `{code}` | {data['sample_count']} | {data['measured_count']} | "
                  f"{pct(data['avg_wer'])} | {pct(data['avg_cer'])} | {data['avg_latency_ms']} |")

    ocr = results.get("ocr_document") or {}
    md.extend([
        "\n## Document OCR & Structure Understanding\n",
        f"- **Target Document:** `{ocr.get('document_id')}`",
        f"- **Key-Value Match Rate:** {pct(ocr.get('field_match_rate'))}",
        f"- **Measured:** {ocr.get('measured')}"
    ])
    if ocr.get("reason"):
        md.append(f"- **Note:** {ocr['reason']}")

    with open(REPORT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")

    return results


if __name__ == "__main__":
    res = run_benchmark()
    print("Benchmark complete!")
    print(json.dumps(res["summary"], indent=2))
