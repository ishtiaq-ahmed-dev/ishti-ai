"""GPU work queue (D2): heavy GPU jobs take turns and only start with enough free VRAM.

Ollama already queues model calls; the danger is a *second* GPU user next to it - Real-ESRGAN next to the 14B
model made Ollama's allocation fail (cudaMalloc out of memory, 27 Sep 01:30). So:
  with gpu.slot(1500, "upscale"): ...
waits for the one heavy-job slot, then for `need_mb` of free VRAM; if memory stays short it asks Ollama to unload
the models that are idle and not pinned (never the planner), then proceeds (or runs anyway after the wait - the
job then may be slow, but nothing hangs)."""
import contextlib
import subprocess
import threading
import time

_heavy = threading.Semaphore(1)


def free_mb():
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=memory.total,memory.used", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=10, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        total, used = (int(x) for x in out.stdout.split(",")[:2])
        return total - used
    except Exception:
        return 1 << 20  # no NVIDIA tools: don't block


def unload_idle_models():
    """Ask Ollama to free the loaded models except the pinned planner (they reload on their next use)."""
    import requests
    from ishti import CONFIG
    base = CONFIG["llm"]["base_url"].rsplit("/v1", 1)[0]
    planner = CONFIG["llm"]["models"]["planner"]
    try:
        loaded = [m["name"] for m in requests.get(f"{base}/api/ps", timeout=5).json().get("models", [])]
    except Exception:
        return []
    freed = [m for m in loaded if m.split(":")[0] != planner.split(":")[0]]
    for m in freed:
        try:
            requests.post(f"{base}/api/generate", json={"model": m, "keep_alive": 0}, timeout=15)
        except Exception:
            pass
    return freed


@contextlib.contextmanager
def slot(need_mb, what="gpu job", wait_s=60):
    with _heavy:
        deadline = time.time() + wait_s
        tried_unload = False
        while free_mb() < need_mb and time.time() < deadline:
            if not tried_unload:
                tried_unload = True
                freed = unload_idle_models()
                if freed:
                    print(f"[gpu] {what}: freed VRAM by unloading {', '.join(freed)}")
                    time.sleep(2)
                    continue
            time.sleep(1)
        yield free_mb()


if __name__ == "__main__":
    order = []

    def job(n):
        with slot(1, f"job {n}", wait_s=1):
            order.append(("start", n))
            time.sleep(0.3)
            order.append(("end", n))
    threads = [threading.Thread(target=job, args=(i,)) for i in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert all(order[i][0] == "start" and order[i + 1] == ("end", order[i][1]) for i in range(0, 6, 2)), order  # one at a time
    print(f"[self-check] gpu OK - heavy jobs take turns; {free_mb()} MB free now")
