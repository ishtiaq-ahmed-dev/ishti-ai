"""QA harness (QA prompt 1, Step 3): run per-skill suites and track state.

Usage:
    venv\\Scripts\\python tests\\run_skill.py weather            # one skill
    venv\\Scripts\\python tests\\run_skill.py weather,calc --runs 2
    venv\\Scripts\\python tests\\run_skill.py all --runs 1       # every tests/skills/<skill>/
    venv\\Scripts\\python tests\\run_skill.py weather --dry      # show what would run, run nothing

    # machine-readable: write the per-skill detail and a compact aggregate somewhere else, and print
    # the aggregate instead of the table (tools/evidence.py consumes this)
    venv\\Scripts\\python tests\\run_skill.py all --json --out reports/skills.json --summary reports/skills_summary.json

Per run it outputs a readable table + JSON, updates reports/test_state.json
(feature -> status, attempts, last_result, consecutive_green, last_error, timestamp),
enforces a per-test timeout (150 s default: live browser/LLM rows run 40-90 s warm and the old 90 s
wall raced the MCP client's own 90 s timeout with zero margin - 2026-10-16), sets ISHTI_TEST_MODE=1,
samples GPU/VRAM before+after,
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
import xml.etree.ElementTree as ET

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_PATH = os.path.join(ROOT, "reports", "test_state.json")
JUNIT_DIR = os.path.join(ROOT, "reports", "skill_junit")   # gitignored, like every other report


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


MAX_BUSY_PCT = 60   # QA batches start only while the GPU is at most this full (Q10: a live ISHTI or another session holds it)


def wait_for_idle_gpu(max_wait_s, sampler=None, sleep=time.sleep, clock=time.time):
    """Q10 policy: a QA batch must not fight a live instance for VRAM (that produced the GpuBusy failures). Wait up to
    `max_wait_s` for the GPU to drop to MAX_BUSY_PCT, polling; -> (True, snapshot) when it is idle enough (or when nvidia-smi
    cannot be read: no GPU, nothing to protect), else (False, last snapshot)."""
    sampler = sampler or gpu_sample
    deadline = clock() + max_wait_s
    while True:
        g = sampler()
        if g is None or g["used_mb"] * 100 <= g["total_mb"] * MAX_BUSY_PCT:
            return True, g
        if clock() >= deadline:
            return False, g
        sleep(10)


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


def junit_reason(path):
    """The real failure message for a suite, read from its JUnit XML.

    This used to be "the last non-empty stdout line", which for every red suite was pytest's own
    progress marker - "!!! stopping after 1 failures !!!" or "+++ Timeout +++". docs/EVIDENCE.md
    therefore named 22 failing skills and could not say why any one of them failed, so an
    environmental failure (no Ollama on a runner, a busy GPU) looked identical to a real
    regression. That is the one thing an evidence report must not do. pytest already writes the
    message; read it from there, and fall back to the stdout tail only when no JUnit record was
    written at all (a hard kill).
    """
    try:
        root = ET.parse(path).getroot()
    except Exception:
        return ""
    for case in root.iter("testcase"):
        for kind in ("failure", "error"):
            node = case.find(kind)
            if node is None:
                continue
            msg = (node.get("message") or "").strip() or (node.text or "").strip()
            first = next((ln.strip() for ln in msg.splitlines() if ln.strip()), "")
            return f"{case.get('name', '?')}: {first}"[:200]
    return ""


def clear_models():
    """Empty the card before a suite so it loads its own model cold.

    Under ISHTI_TEST_MODE the previous suite's model stays resident for 2m while the next suite loads
    ITS model - two big models on a 16 GB card means slow generation and a per-test timeout that reads
    exactly like a broken skill (2026-10-16: extract, meta, mnemonic, owner, web_fetch all failed at
    the 90 s limit, every one of them starting with >11 GB already held). Reuse the product's own
    unload; a failure here must never fail the batch.
    """
    try:
        sys.path.insert(0, ROOT)
        from ishti import vram
        for m in vram.ps(True):
            vram.unload(m["name"])
    except Exception:
        pass


def run_one(skill, runs, timeout_s, dry):
    suite_dir = os.path.join(ROOT, "tests", "skills", skill)
    if not os.path.isdir(suite_dir):
        return {"feature": skill, "status": "no-suite", "runs": []}
    if dry:
        tests = subprocess.run([sys.executable, "-m", "pytest", suite_dir, "--collect-only", "-q"],
                               capture_output=True, text=True, encoding="utf-8", errors="replace",
                               timeout=60)
        n = len([l for l in tests.stdout.splitlines() if l.startswith("<")]) or \
            sum(1 for l in tests.stdout.splitlines() if "::" in l)
        return {"feature": skill, "status": "dry-run", "would_run_tests": n, "runs": []}

    results = []
    env = dict(os.environ, ISHTI_TEST_MODE="1", PYTHONIOENCODING="utf-8")
    os.makedirs(JUNIT_DIR, exist_ok=True)
    for i in range(runs):
        clear_models()
        gpu_before = gpu_sample()
        t0 = time.time()
        junit_path = os.path.join(JUNIT_DIR, f"{skill}_{i + 1}.xml")
        if os.path.exists(junit_path):
            os.remove(junit_path)   # a stale record from an earlier run must not be read as this one's
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", suite_dir, "-q", "--timeout", str(timeout_s),
             "--no-header", "-x", "--tb=line", f"--junitxml={junit_path}"],  # pytest-timeout self-registers; -p double-registers
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout_s * 4 + 60, env=env, cwd=ROOT)
        dur = round(time.time() - t0, 1)
        tail = [l for l in (proc.stdout or "").splitlines() if l.strip()][-3:]
        reason = junit_reason(junit_path) or (tail[-1] if tail else "")
        # pytest exit 5 is "no tests were collected". An empty suite folder is not a broken skill, so
        # it must not be scored as one - it leaves the denominator (evidence.py counts only graded
        # suites), the same as a folder that does not exist at all.
        if proc.returncode == 5:
            return {"feature": skill, "status": "no-suite", "runs": []}
        passed = proc.returncode == 0
        results.append({
            "run": i + 1, "ok": passed, "exit": proc.returncode, "seconds": dur,
            "gpu_before": gpu_before, "gpu_after": gpu_sample(),
            "tail": tail, "reason": reason,
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
        f["last_error"] = _last_error(res)
    return f


def _last_error(res):
    """The most useful one-liner for a failing skill: the real failure message, not a progress marker."""
    runs = res.get("runs") or []
    if not runs:
        return res["status"][:200]
    last = runs[-1]
    reason = (last.get("reason") or "").strip()
    if reason:
        return reason[:200]
    tail = [t for t in (last.get("tail") or []) if t]
    return (tail[-1] if tail else res["status"])[:200]


def summarize(report):
    """The compact aggregate a machine consumes (--summary, or --json): counts plus the named failures.

    `graded` is the denominator: green + red + SAFETY-FAIL. A `no-suite` folder is not graded, so it
    can neither inflate nor deflate the pass rate - an empty folder is not a passing skill."""
    counts = {}
    for r in report:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    graded = counts.get("green", 0) + counts.get("red", 0) + counts.get("SAFETY-FAIL", 0)
    return {"generated": dt.datetime.now().isoformat(timespec="seconds"), "targets": len(report),
            "green": counts.get("green", 0), "graded": graded, "counts": counts,
            "flagged": [{"skill": r["feature"], "status": r["status"], "last_error": _last_error(r)}
                        for r in report if r["status"] in ("red", "SAFETY-FAIL")]}


def main():
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")  # script only: never at import
    ap = argparse.ArgumentParser(description="ISHTI per-skill QA harness")
    ap.add_argument("targets", help="skill name, comma list, or 'all'")
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--timeout", type=int, default=150, help="per-test timeout seconds")
    ap.add_argument("--dry", action="store_true", help="show what would run, run nothing")
    ap.add_argument("--json", action="store_true", help="print the aggregate JSON instead of the table")
    ap.add_argument("--out", help="write the per-skill detail here (default reports/test_state_last_run.json)")
    ap.add_argument("--summary", help="also write the compact aggregate JSON to this path")
    ap.add_argument("--wait-idle", type=int, default=300, metavar="S",
                    help="wait up to S seconds for the GPU to be at most 60%% full before starting (default 300)")
    ap.add_argument("--force", action="store_true", help="run even if the GPU is busy (results may show GpuBusy failures)")
    args = ap.parse_args()

    os.environ["ISHTI_TEST_MODE"] = "1"          # global safety switch for everything this harness runs
    if not args.dry and not args.force:
        idle, g = wait_for_idle_gpu(args.wait_idle)
        if not idle:
            print(f"GPU busy: {g['used_mb']} of {g['total_mb']} MiB in use (limit {MAX_BUSY_PCT}%). Another ISHTI instance or "
                  f"session is using it, so a QA batch would fail with GpuBusy. Re-run when it is idle, or pass --force.")
            sys.exit(3)
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
                    if r.get("reason"):
                        print(f"        {r['reason'][:150]}")
                    for line in r["tail"]:
                        print(f"        {line[:150]}")

    if not args.dry:
        save_state(state)
    out_path = os.path.abspath(args.out or os.path.join(ROOT, "reports", "test_state_last_run.json"))
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    json.dump(report, open(out_path, "w", encoding="utf-8"), indent=1)
    summary = summarize(report)
    if args.summary:
        os.makedirs(os.path.dirname(os.path.abspath(args.summary)), exist_ok=True)
        json.dump(summary, open(args.summary, "w", encoding="utf-8"), indent=1)
    if args.json:
        print(json.dumps(summary, indent=1))
    else:
        print(f"\nSummary: {summary['green']}/{summary['graded']} graded suites green -> "
              f"state in reports/test_state.json, details in {out_path}")
    sys.exit(0 if summary["graded"] and summary["green"] == summary["graded"] else 1)


if __name__ == "__main__":
    main()
