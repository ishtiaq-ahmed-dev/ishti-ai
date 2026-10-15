"""Synthetic offline resource churn; never describes real model/voice soak readiness."""
import argparse
import json
import os
from pathlib import Path
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--duration", type=int, default=120)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    if os.environ.get("ISHTI_TEST_MODE") != "1" or os.environ.get("ISHTI_SCRATCH") != "1":
        raise SystemExit("Synthetic harness requires ISHTI_TEST_MODE=1 and ISHTI_SCRATCH=1")
    if not 1 <= args.duration <= 600:
        raise SystemExit("Duration must be 1-600 seconds")
    from ishti import audit
    from ishti.core.memory_probe import Probe
    probe = Probe(args.output)
    errors = []
    turns = 0
    def turn():
        try:
            audit.record("synthetic memory churn", "No owner data or network")
            with audit._db() as c:
                assert c.execute("SELECT COUNT(*) FROM actions").fetchone()[0] > 0
        except Exception as exc:
            errors.append(type(exc).__name__)
    # Warm up before measuring growth; startup imports are not attributed to sustained load.
    for _ in range(10):
        thread = threading.Thread(target=turn)
        thread.start()
        thread.join(5)
    probe.sample()
    started = time.monotonic()
    sampled = started
    while time.monotonic() - started < args.duration:
        workers = [threading.Thread(target=turn) for _ in range(4)]
        for thread in workers:
            thread.start()
        for thread in workers:
            thread.join(5)
            if thread.is_alive():
                raise RuntimeError("Synthetic request did not finish")
        turns += 4
        if time.monotonic() - sampled >= 15:
            probe.sample()
            sampled = time.monotonic()
        time.sleep(0.05)
    probe.sample()
    probe.save()
    samples = list(probe.samples)
    result = {"workload": "synthetic audit/SQLite thread churn; no Ollama/voice/model inference",
              "duration_s": time.monotonic() - started, "turns": turns, "errors": errors,
              "retained_connections": len(audit._conn_cache),
              "rss_drift_bytes": samples[-1]["rss_bytes"] - samples[0]["rss_bytes"],
              "python_drift_bytes": samples[-1]["python_bytes"] - samples[0]["python_bytes"]}
    Path(args.output).with_suffix(".summary.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result))
    if errors or result["retained_connections"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
