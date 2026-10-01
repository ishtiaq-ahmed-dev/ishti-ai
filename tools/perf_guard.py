"""perf_guard: refuse to keep an optimization that made anything slower (measure -> change -> measure).

    venv\\Scripts\\python -m tools.perf_guard --baseline _work/bench_full_baseline.json [--full]

Runs tools/bench_opt against the CURRENT tree and compares each median with the baseline JSON.
Exit 1 (and a clear table) when any benchmark regresses more than --tolerance (default 15%:
run-to-run noise on this machine measured ~2-6%, so 15% is a real regression, not jitter).
tok/s benchmarks are higher-is-better; latency ones lower-is-better.
"""
import argparse
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

# key -> (human label, higher_is_better)
BENCHES = {
    # key -> (human label, higher_is_better, tolerance) — tolerances above the noise measured in
    # OPTIMIZATION_BASELINE.md (route varies ~20% run-to-run with CPU frequency scaling; TTS ~13%).
    "import_ishti_ms|median": ("import ishti (cold)", False, 0.15),
    "skills_load_ms|median": ("load_skills()", False, 0.10),
    "route_rule_ms|median": ("route() rule match", False, 0.30),
    "reflex_ms|median": ("reflex.answer()", False, 0.50),
    "llm_gen|median_tok_s": ("planner tok/s (warm)", True, 0.15),
    "llm_gen|median_wall_s": ("planner wall s (warm)", False, 0.15),
    "voice_stt_ms|median": ("whisper warm decode", False, 0.15),
    "voice_tts_ms|median": ("piper synthesize", False, 0.25),
}


def value_at(data, key):
    cur = data
    for part in key.split("|"):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--baseline", required=True, help="bench_opt JSON measured on the pre-change tree")
    ap.add_argument("--current", default=None, help="reuse an already-measured current JSON instead of benching now")
    ap.add_argument("--full", action="store_true", help="bench the full set (default: quick set present in the baseline)")
    ap.add_argument("--tolerance", type=float, default=None, help="override the per-benchmark tolerances with one value")
    args = ap.parse_args()

    with open(args.baseline, encoding="utf-8") as f:
        base = json.load(f)
    if args.current:
        with open(args.current, encoding="utf-8") as f:
            cur = json.load(f)
    else:
        quick = [] if args.full else ["--quick"]
        out_json = os.path.join(os.getcwd(), "_work", "perf_guard_current.json")
        cmd = [sys.executable, "-m", "tools.bench_opt", "--json", out_json, *quick]
        print("[perf_guard] benching:", " ".join(cmd))
        r = subprocess.run(cmd, cwd=os.path.dirname(HERE), capture_output=True, text=True)
        sys.stdout.write(r.stdout)
        if r.returncode != 0:
            print(r.stderr[-2000:])
            sys.exit(2)
        with open(out_json, encoding="utf-8") as f:
            cur = json.load(f)

    rows, failed = [], False
    for key, (label, higher_better, tol) in BENCHES.items():
        b, c = value_at(base, key), value_at(cur, key)
        if b is None or c is None:
            rows.append((label, b, c, None, "skip (missing)"))
            continue
        if b == 0:
            rows.append((label, b, c, None, "skip (zero baseline)"))
            continue
        change = (c - b) / b
        worse = (c < b) if higher_better else (c > b)
        tol = args.tolerance if args.tolerance is not None else tol
        failed |= bad
        rows.append((label, b, c, change, ("REGRESSION" if bad else "ok")))

    w = max(len(r[0]) for r in rows) + 2
    print(f"\n{'benchmark'.ljust(w)}{'baseline':>12}{'current':>12}{'change':>10}  verdict")
    for label, b, c, ch, verdict in rows:
        chs = f"{ch:+.1%}" if ch is not None else "-"
        print(f"{label.ljust(w)}{b!s:>12}{c!s:>12}{chs:>10}  {verdict}")
    print("\n[perf_guard] PASS - no regression beyond tolerance" if not failed
          else "\n[perf_guard] FAIL - revert the change or fix the regression before keeping it")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
