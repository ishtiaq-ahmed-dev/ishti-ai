"""QA prompt 3 phase 3: fixed mixed workload at N simultaneous tasks, monitored live.

Usage:
    venv\\Scripts\\python tools/load_ramp.py 4 180          # N=4 tasks, monitor for 180 s, level_4.jsonl

The mixed workload (same every level, proven skills only): weather ask, reminder set,
chat PONG, file search, memory recall, notes add, folders listing, digest, bayes update,
system RAM check. Each task posts its own session to the dispatcher every ~2 s until the
level window closes; latency/first-token/errors recorded per turn. The monitor samples at
1 s in-process (same sampler as tools/monitor.py) -> metrics/ramp_level_<N>.jsonl.
"""
import json
import os
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.environ.setdefault("ISHTI_TEST_MODE", "1")

WORKLOAD = [
    "what's the weather in Peshawar today",
    "remind me in 40 minutes to qa-stretch",
    "Reply with the exact word: PONG",
    "find files named readme in D:/ISHTI/docs",
    "what do you remember about me",
    "note: qa ramp marker {n}",
    "show the top level of D:/ISHTI/docs",
    "bayes: prior 1% sensitivity 99% specificity 95%",
    "how much RAM is free",
    "check the last 24 hours of all my chats and give me a report",
]


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    window_s = int(sys.argv[2]) if len(sys.argv) > 2 else 180
    tag = sys.argv[3] if len(sys.argv) > 3 else str(n)   # label for the metrics file (soak runs reuse a level)
    from tools.monitor import sample
    from ishti import dispatcher

    rows = []          # per-turn latency records
    stop = threading.Event()
    lock = threading.Lock()

    def worker(wid):
        i = 0
        while not stop.is_set():
            text = WORKLOAD[(wid * 3 + i) % len(WORKLOAD)]
            if "{n}" in text:
                text = text.replace("{n}", f"{wid}-{i}")
            sid = f"qa_ramp{n}_w{wid}"
            toks = []
            t0 = time.time()
            err = None
            try:
                res = dispatcher.handle(text, sid, on_token=lambda t: toks.append(time.time()))
                reply = str(res.get("final_answer") or res.get("reply") or "")
                if not reply.strip():
                    err = "empty reply"
            except Exception as exc:
                reply, err = "", f"{type(exc).__name__}: {exc}"
            with lock:
                rows.append({"worker": wid, "turn": i, "text": text[:60],
                             "secs": round(time.time() - t0, 2),
                             "first_token": round(toks[0] - t0, 2) if toks else None,
                             "err": err})
            i += 1
            time.sleep(2.0)

    out_path = os.path.join(ROOT, "metrics", f"ramp_level_{tag}.jsonl")
    threads = [threading.Thread(target=worker, args=(w,), daemon=True) for w in range(n)]
    t0 = time.time()
    with open(out_path, "a", encoding="utf-8") as f:
        f.write(json.dumps({"_meta": {"level": n, "window_s": window_s, "started": t0}}) + "\n")

        def mon():
            while not stop.is_set():
                f.write(json.dumps(sample()) + "\n")
                time.sleep(1.0)

        mt = threading.Thread(target=mon, daemon=True)
        for th in threads:
            th.start()
        mt.start()
        while time.time() - t0 < window_s:
            time.sleep(2)
        stop.set()
        for th in threads:
            th.join(timeout=30)
        mt.join(timeout=5)

    secs = [r["secs"] for r in rows]
    fts = [r["first_token"] for r in rows if r["first_token"]]
    errs = [r for r in rows if r["err"]]
    summary = {
        "level": n, "window_s": window_s, "turns": len(rows),
        "latency_avg_s": round(sum(secs) / len(secs), 2) if secs else None,
        "latency_max_s": max(secs) if secs else None,
        "first_token_avg_s": round(sum(fts) / len(fts), 2) if fts else None,
        "errors": len(errs), "err_samples": [e["err"] for e in errs[:3]],
    }
    with open(os.path.join(ROOT, "metrics", f"ramp_summary_level_{n}.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=1)
    print(json.dumps(summary))


if __name__ == "__main__":
    main()
