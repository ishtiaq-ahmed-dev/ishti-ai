"""Monitor-Agent for QA prompt 3 (prompt rule: sample every second; evidence only).

Usage:
    venv\\Scripts\\python tools/monitor.py <tag>            # runs until killed; writes metrics/<tag>.jsonl

One JSON line per sample:
    ts, gpu {used_mb,total_mb}, cpu_pct, ram {used_mb,total_mb},
    procs {total,ishti,ollama}, ollama_ok,
    loaded_models [{name,size_vram,size,partial}], partial_offloads (count this sample),
    killswitch_flag, killswitch_since

Notes
- `partial` is set from Ollama /api/ps: any model with size_vram != size is a PARTIAL OFFLOAD
  (prompt invariant: zero partial offloads at all times). Counted per sample at analysis time.
- Model switches are derived at analysis time from consecutive loaded_models name lists
  (tools/analyze_metrics.py), so the monitor itself never touches the governor.
- Every field degrades to None/"?" on its own failure: the monitor must never become the
  load it is measuring.
"""
import datetime
import json
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

OUT_DIR = os.path.join(ROOT, "metrics")
OLLAMA = os.environ.get("ISHTI_OLLAMA_URL", "http://127.0.0.1:11434")

try:
    import psutil
except ImportError:
    psutil = None
try:
    import requests
except ImportError:
    requests = None


def _gpu():
    out = subprocess.run(
        ["nvidia-smi", "--query-gpu=memory.used,memory.total", "--format=csv,noheader,nounits"],
        capture_output=True, text=True, timeout=5,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    used, total = out.stdout.strip().splitlines()[0].split(", ")
    return {"used_mb": int(used), "total_mb": int(total)}


def _ollama_ps():
    """(ok, models) - models is [] when Ollama is unreachable (chaos tests rely on this)."""
    if requests is None:
        return None, []
    try:
        r = requests.get(f"{OLLAMA}/api/ps", timeout=2)
        models = [{"name": m.get("name"), "size_vram": m.get("size_vram"), "size": m.get("size"),
                   "partial": (m.get("size_vram") != m.get("size"))}
                  for m in r.json().get("models", [])]
        return True, models
    except Exception:
        return False, []


def _killswitch():
    """Read-only peek at the live run flag (never creates one)."""
    p = os.path.join(ROOT, "run", "kill_switch.flag")
    try:
        return os.path.exists(p), open(p, encoding="utf-8").read().strip()[:120] if os.path.exists(p) else None
    except OSError:
        return os.path.exists(p), None


def sample():
    s = {"ts": time.time(), "iso": datetime.datetime.now().isoformat(timespec="seconds")}
    try:
        s["gpu"] = _gpu()
    except Exception:
        s["gpu"] = None
    if psutil:
        try:
            s["cpu_pct"] = psutil.cpu_percent(interval=None)
            vm = psutil.virtual_memory()
            s["ram"] = {"used_mb": vm.used // (1024 * 1024), "total_mb": vm.total // (1024 * 1024)}
            names = [p.info["name"] or "" for p in psutil.process_iter(attrs=["name"])]
            s["procs"] = {"total": len(names),
                          "ishti": sum(1 for n in names if "ishti" in n.lower()),
                          "ollama": sum(1 for n in names if "ollama" in n.lower())}
        except Exception:
            s["cpu_pct"] = s["ram"] = s["procs"] = None
    else:
        s["cpu_pct"] = s["ram"] = s["procs"] = None
    s["ollama_ok"], models = _ollama_ps()
    s["loaded_models"] = models
    s["partial_offloads"] = sum(1 for m in models if m.get("partial"))
    s["killswitch_flag"], s["killswitch_since"] = _killswitch()
    return s


def main():
    tag = sys.argv[1] if len(sys.argv) > 1 else "run"
    os.makedirs(OUT_DIR, exist_ok=True)
    out_path = os.path.join(OUT_DIR, f"{tag}.jsonl")
    with open(out_path, "a", encoding="utf-8") as f:
        if f.tell() == 0:  # resumed monitors append; one header comment line max
            f.write(json.dumps({"_meta": {"pid": os.getpid(), "started": time.time()}}) + "\n")
        next_t = time.time()
        while True:
            try:
                f.write(json.dumps(sample()) + "\n")
                f.flush()
            except Exception as exc:  # even a broken monitor must not die silently
                print(f"[monitor] sample failed: {exc}", file=sys.stderr)
            next_t += 1.0
            time.sleep(max(0.0, next_t - time.time()))


if __name__ == "__main__":
    main()
