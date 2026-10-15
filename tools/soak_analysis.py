"""QA prompt 3 phase 5: soak drift analysis over metrics/ramp_level_<tag>.jsonl.

Splits the window into first-quarter vs last-quarter and reports the drift of VRAM used,
process count (ISHTI's own python), and error rate. Evidence-only: prints measured numbers.
"""
import glob
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
METRICS = os.path.join(ROOT, "metrics")


def load(tag):
    rows = []
    for line in open(os.path.join(METRICS, f"ramp_level_{tag}.jsonl"), encoding="utf-8"):
        try:
            r = json.loads(line)
            if "ts" in r:
                rows.append(r)
        except Exception:
            pass
    return rows


def q(rows, k):
    vals = []
    for r in rows:
        if r.get("gpu") and k == "vram":
            vals.append(r["gpu"]["used_mb"])
        elif r.get("procs") is not None and k == "procs":
            vals.append(r["procs"]["total"])
        elif r.get("ram") and k == "ram":
            vals.append(r["ram"]["used_mb"])
    return vals


def avg(v):
    return round(sum(v) / len(v), 1) if v else None


def main(tag):
    rows = load(tag)
    if len(rows) < 20:
        print(f"not enough samples ({len(rows)})")
        return
    n = len(rows) // 4
    first, last = rows[:n], rows[-n:]
    dur = round(rows[-1]["ts"] - rows[0]["ts"], 0)
    out = {"tag": tag, "duration_s": dur, "samples": len(rows),
           "attribution": "ram_used_mb and procs_total are SYSTEM-wide, not ISHTI RSS. They cannot establish a process leak."}
    for k, name in (("vram", "vram_used_mb"), ("ram", "ram_used_mb"), ("procs", "procs_total")):
        a, b = avg(q(first, k)), avg(q(last, k))
        out[name] = {"first_quarter_avg": a, "last_quarter_avg": b,
                     "drift": round(b - a, 1) if a is not None and b is not None else None}
    partial = sum(r.get("partial_offloads", 0) for r in rows)
    ollama_gaps = sum(1 for r in rows if r.get("ollama_ok") is False)
    out["partial_offload_samples"] = partial
    out["ollama_unreachable_samples"] = ollama_gaps
    print(json.dumps(out, indent=1))
    with open(os.path.join(METRICS, f"soak_analysis_{tag}.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "soak_8min")
