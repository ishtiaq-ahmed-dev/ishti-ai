"""QA prompt 3: turn ramp metrics into the report table + chart-ready CSV.

Usage: venv\\Scripts\\python tools/analyze_metrics.py
Reads metrics/ramp_level_<N>.jsonl, writes metrics/ramp_table.csv + prints the table.
"""
import csv
import glob
import json
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
METRICS = os.path.join(ROOT, "metrics")

rows_out = []
for path in sorted(glob.glob(os.path.join(METRICS, "ramp_summary_level_*.json")),
                   key=lambda p: int(re.search(r"level_(\d+)", p).group(1))):
    level = int(re.search(r"level_(\d+)", path).group(1))
    summary = json.load(open(path, encoding="utf-8"))
    vram_peak = partial_total = samples = 0
    cpu_peak = ram_peak = 0.0
    model_sets = []
    switches = 0
    prev = None
    for line in open(os.path.join(METRICS, f"ramp_level_{level}.jsonl"), encoding="utf-8"):
        try:
            r = json.loads(line)
        except Exception:
            continue
        if "_meta" in r:
            continue
        samples += 1
        if r.get("gpu"):
            vram_peak = max(vram_peak, r["gpu"]["used_mb"])
        if r.get("cpu_pct") is not None:
            cpu_peak = max(cpu_peak, r["cpu_pct"])
        if r.get("ram"):
            ram_peak = max(ram_peak, r["ram"]["used_mb"])
        partial_total += r.get("partial_offloads", 0)
        names = tuple(sorted(m["name"] for m in r.get("loaded_models", [])))
        if names and names != prev:
            if prev is not None:
                switches += 1
            prev = names
    rows_out.append({
        "level": level,
        "turns": summary["turns"],
        "latency_avg_s": summary["latency_avg_s"],
        "latency_max_s": summary["latency_max_s"],
        "first_token_avg_s": summary["first_token_avg_s"],
        "errors": summary["errors"],
        "vram_peak_mb": vram_peak,
        "partial_offload_samples": partial_total,
        "cpu_peak_pct": round(cpu_peak, 1),
        "ram_peak_mb": int(ram_peak),
        "model_switches_observed": switches,
        "monitor_samples": samples,
    })

out_csv = os.path.join(METRICS, "ramp_table.csv")
with open(out_csv, "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=list(rows_out[0].keys()))
    w.writeheader()
    w.writerows(rows_out)
print(json.dumps(rows_out, indent=1))
print("csv:", out_csv)
