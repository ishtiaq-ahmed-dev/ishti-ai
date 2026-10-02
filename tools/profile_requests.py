"""Where the time goes in a request: python tools/profile_requests.py [--out _work/profile.json] [--runs 2]

Starts the real backend (private port, own process), waits for it to be ready, runs a fixed set of representative tasks
(each --runs times: the first is "cold", the last "warm") through POST /api/execute_stream, and prints, per task, the
latency and the per-request call ledger (ishti/calltrace.py): LLM calls (role, ms), embedding calls, skill calls with
their plan reason, outbound web calls, and the flags for any call without a route reason.
All tasks are local and read-only: no web, no sends, nothing written to memory on purpose.
"""
import argparse
import json
import os
import statistics
import subprocess
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PORT = 8895
BASE = f"http://127.0.0.1:{PORT}"
TASKS = [
    ("chat-math", "what is 7 plus 5?"),
    ("chat-fact", "tell me a short fun fact about Peshawar"),
    ("reflex-time", "what time is it"),
    ("system-ram", "how much RAM is free right now"),
    ("notes-list", "list my notes"),
    ("code-explain", "explain what a mutex is in two sentences"),
    ("multi-task", "what time is it and also what is 9 plus 4"),
]


def post_stream(prompt, session):
    req = urllib.request.Request(BASE + "/api/execute_stream", data=json.dumps({"prompt": prompt, "session_id": session}).encode(),
                                 headers={"Content-Type": "application/json"})
    first_token = None
    t0 = time.perf_counter()
    result = None
    for line in urllib.request.urlopen(req, timeout=300):
        d = json.loads(line)
        if "token" in d and first_token is None:
            first_token = time.perf_counter() - t0
        if d.get("done"):
            result = d["result"]
    return result, (time.perf_counter() - t0), first_token


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(ROOT, "_work", "profile.json"))
    ap.add_argument("--runs", type=int, default=2)
    ap.add_argument("--label", default="run")
    a = ap.parse_args()
    env = {**os.environ}
    proc = subprocess.Popen([os.path.join(ROOT, "venv", "Scripts", "python.exe"), "-m", "ishti", str(PORT)], cwd=ROOT, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=0x08000000)
    rows = []
    try:
        t0 = time.time()
        while time.time() - t0 < 240:
            try:
                if json.load(urllib.request.urlopen(BASE + "/api/ready", timeout=1)).get("ready"):
                    break
            except Exception:
                time.sleep(0.3)
        boot_s = round(time.time() - t0, 1)
        for name, prompt in TASKS:
            for run in range(a.runs):
                res, wall, first = post_stream(prompt, f"prof-{name}-{run}")
                c = (res or {}).get("calls") or {}
                rows.append({"task": name, "run": run, "wall_s": round(wall, 2), "first_token_s": None if first is None else round(first, 2),
                             "skill": (res or {}).get("skill"), "llm_calls": c.get("llm_calls"), "llm_ms": c.get("llm_ms"),
                             "embed_calls": c.get("embed_calls"), "skill_calls": c.get("skill_calls"), "net_calls": c.get("net_calls"),
                             "flags": c.get("flags"), "calls": [(x["kind"], x["name"], x.get("ms"), x.get("model", "")) for x in c.get("calls", [])],
                             "answer": ((res or {}).get("final_answer") or "")[:60]})
                try:  # PERF-1: resident set vs the 80 % budget, and no partial offload, right after each request
                    v = json.load(urllib.request.urlopen(BASE + "/api/vram", timeout=5))
                    rows[-1]["gpu_used_pct"] = v["gpu"]["used_pct"]
                    rows[-1]["resident"] = [(m["name"], m["size_mb"], m["fully_on_gpu"]) for m in v["models"]]
                    rows[-1]["partial_offload"] = [m["name"] for m in v["models"] if not m["fully_on_gpu"]]
                except Exception:
                    pass
                time.sleep(1)
    finally:
        try:
            urllib.request.urlopen(urllib.request.Request(BASE + "/api/shutdown", method="POST", data=b""), timeout=3)
            proc.wait(45)
        except Exception:
            proc.kill()
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    json.dump({"label": a.label, "boot_s": boot_s, "rows": rows}, open(a.out, "w", encoding="utf-8"), indent=1)
    print(f"boot {boot_s}s")
    for r in rows:
        print(f"{r['task']:13} run{r['run']} {r['wall_s']:6.2f}s ttft={r['first_token_s']} skill={r['skill']} llm={r['llm_calls']}({r['llm_ms']}ms) "
              f"emb={r['embed_calls']} sk={r['skill_calls']} net={r['net_calls']} flags={r['flags']}")
        print("     ", r["calls"], f"gpu={r.get('gpu_used_pct')}% partial={r.get('partial_offload')}")
    for name, _ in TASKS:
        warm = [r["wall_s"] for r in rows if r["task"] == name and r["run"] > 0]
        if warm:
            print(f"median warm {name}: {statistics.median(warm):.2f}s")


if __name__ == "__main__":
    main()
