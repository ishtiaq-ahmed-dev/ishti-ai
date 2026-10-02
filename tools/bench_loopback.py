"""Loopback HTTP cost on the request path (Cycle 4): the governor's own Ollama round-trips.

    venv\\Scripts\\python -m tools.bench_loopback [--json _work/loopback.json]

Every reply makes several loopback calls (vram.ps() after a load/unload, models._keep_alive on every
generation, the health loop, semantic embeddings). This measures them the way the system actually makes
them, so "should we keep connections alive?" is answered with numbers instead of folklore. Needs Ollama
up; it only reads /api/ps and posts a keep_alive - it never generates.
"""
import argparse
import json
import os
import statistics
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default=None)
    ap.add_argument("--runs", type=int, default=25)
    args = ap.parse_args()

    from ishti import models, vram  # noqa: E402  (import cost is not part of the measurement)
    name = vram.OLLAMA
    out = {}

    def bench(label, fn, n):
        xs = []
        for _ in range(n):
            t0 = time.perf_counter()
            fn()
            xs.append((time.perf_counter() - t0) * 1000)
        out[label] = {"median_ms": round(statistics.median(xs), 3), "min_ms": round(min(xs), 3),
                      "max_ms": round(max(xs), 3)}
        print(f"[loopback] {label:26s} median {out[label]['median_ms']:8.3f} ms  "
              f"min {out[label]['min_ms']:7.3f}  max {out[label]['max_ms']:7.3f}  (Ollama at {name})")

    try:  # a failing Ollama must not make this bench lie about latency
        vram.ps(fresh=True)
    except Exception as exc:
        print(f"[loopback] Ollama not reachable ({exc}) - BLOCKED, no numbers")
        return
    bench("vram.ps(fresh=True)", lambda: vram.ps(fresh=True), args.runs)
    bench("models._keep_alive(post)", lambda: models._keep_alive("laya", -1), args.runs)
    print(f"[loopback] governor round-trip total: "
          f"{out['vram.ps(fresh=True)']['median_ms'] + out['models._keep_alive(post)']['median_ms']:.3f} ms per reply")
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(out, f, indent=1)


if __name__ == "__main__":
    main()
