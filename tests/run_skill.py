"""QA harness (QA prompt 1, Step 3): run per-skill suites and track state.

Usage:
    venv\\Scripts\\python tests\\run_skill.py weather            # one skill
    venv\\Scripts\\python tests\\run_skill.py weather,calc --runs 2
    venv\\Scripts\\python tests\\run_skill.py all --runs 1       # every tests/skills/<skill>/
    venv\\Scripts\\python tests\\run_skill.py weather --dry      # show what would run, run nothing

Per run it outputs a readable table + JSON, updates TEST_STATE.json
(feature -> status, attempts, last_result, consecutive_green, last_error, timestamp),
enforces a per-test timeout, sets ISHTI_TEST_MODE=1, samples GPU/VRAM before+after,
and asserts the audit log shows no real T2 side effects during the run.
"""
import argparse
import datetime as dt
import io
import json
import os
import subprocess
import sys
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_PATH = os.path.join(ROOT, "TEST_STATE.json")


def gpu_sample():
    """One VRAM snapshot (MiB used/total) or None - never fails the run."""
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=8)
        used, total = out.stdout.strip().splitlines()[0].split(", ")
        return {"used_mb": int(used), "total_mb": int(total)}
    except Exception:
        return None


def load_state():
    try:
        return json.load(open(STATE_PATH, encoding="utf-8"))
    except Exception:
        return {}


def save_state(state):
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=1)


def audit_forbidden_actions(since_ts):
    """True if the audit log gained a real-world T2 confirmation during the run window."""
    sys.path.insert(0, ROOT)
    from ishti import audit
    bad = []
    for r in audit.query(since=since_ts, limit=10_000):
        action = (r[3] or "")
        if "TEST-MODE BLOCK" in action or action.startswith(("run ", "chat digest run")):
            continue
        if r[6] and str(r[6]).lower() in ("t2", "t3") and r[5]:  # tier T2/T3 with a confirm timestamp
            bad.append(action[:80])
    return bad


def run_one(skill, runs, timeout_s, dry):
    suite_dir = os.path.join(ROOT, "tests", "skills", skill)
    if not os.path.isdir(suite_dir):
        return {"feature": skill, "status": "no-suite", "runs": []}
    if dry:
        tests = subprocess.run([sys.executable, "-m", "pytest", suite_dir, "--collect-only", "-q"],
                               capture_output=True, text=True, timeout=60)
        n = len([l for l in tests.stdout.splitlines() if l.startswith("<")]) or \
            sum(1 for l in tests.stdout.splitlines() if "::" in l)
        return {"feature": skill, "status": "dry-run", "would_run_tests": n, "runs": []}

    results = []
    env = dict(os.environ, ISHTI_TEST_MODE="1", PYTHONIOENCODING="utf-8")
    for i in range(runs):
        gpu_before = gpu_sample()
        t0 = time.time()
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", suite_dir, "-q", "--timeout", str(timeout_s),
             "--no-header", "-x", "--tb=line"],      # pytest-timeout self-registers; -p double-registers
            capture_output=True, text=True, timeout=timeout_s * 4 + 60, env=env, cwd=ROOT)
        dur = round(time.time() - t0, 1)
        tail = [l for l in (proc.stdout or "").splitlines() if l.strip()][-3:]
        passed = proc.returncode == 0
        results.append({
            "run": i + 1, "ok": passed, "exit": proc.returncode, "seconds": dur,
            "gpu_before": gpu_before, "gpu_after": gpu_sample(),
            "tail": tail,
        })
    ok_all = all(r["ok"] for r in results)
    return {"feature": skill, "status": "green" if ok_all else "red", "runs": results}


def update_state(state, res):
    f = state.setdefault(res["feature"], {"attempts": 0, "consecutive_green": 0})
    f["attempts"] += 1
    f["last_result"] = res["status"]
    f["timestamp"] = dt.datetime.now().isoformat(timespec="seconds")
    if res["status"] == "green":
        f["consecutive_green"] = f.get("consecutive_green", 0) + 1
        f["last_error"] = None
    else:
        f["consecutive_green"] = 0
        tails = [t for r in res.get("runs", []) for t in (r.get("tail") or []) if t]
        f["last_error"] = ("; ".join(tails[-1:]) or res["status"])[:200]
    return f


def main():
    ap = argparse.ArgumentParser(description="ISHTI per-skill QA harness")
    ap.add_argument("targets", help="skill name, comma list, or 'all'")
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--timeout", type=int, default=90, help="per-test timeout seconds")
    ap.add_argument("--dry", action="store_true", help="show what would run, run nothing")
    ap.add_argument("--json", action="store_true", help="JSON output only")
    args = ap.parse_args()

    os.environ["ISHTI_TEST_MODE"] = "1"          # global safety switch for everything this harness runs
    names = sorted(os.listdir(os.path.join(ROOT, "tests", "skills"))) \
        if args.targets == "all" else [t.strip() for t in args.targets.split(",") if t.strip()]
    state = load_state()
    report = []

    for name in names:
        since = time.time()
        res = run_one(name, args.runs, args.timeout, args.dry)
        if not args.dry:
            forbidden = audit_forbidden_actions(since)
            if forbidden:
                res["status"] = "SAFETY-FAIL"
                res["forbidden_actions"] = forbidden
            update_state(state, res)
        report.append(res)
        if not args.json:
            mark = {"green": "PASS", "red": "FAIL", "dry-run": "DRY "}.get(res["status"], res["status"].upper())
            print(f"[{mark:>5}] {name:<22} runs={len(res['runs'])} "
                  f"{(res['runs'] or [{}])[-1].get('seconds', '-')}s")
            for r in res.get("runs", []):
                if not r["ok"]:
                    for line in r["tail"]:
                        print(f"        {line[:150]}")

    if not args.dry:
        save_state(state)
    out_path = os.path.join(ROOT, "TEST_STATE_last_run.json")
    json.dump(report, open(out_path, "w", encoding="utf-8"), indent=1)
    green = sum(1 for r in report if r["status"] == "green")
    if not args.json:
        print(f"\nSummary: {green}/{len(report)} green -> state in TEST_STATE.json, details in {os.path.basename(out_path)}")
    sys.exit(0 if green == len(report) and report else 1)


if __name__ == "__main__":
    main()
