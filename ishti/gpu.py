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
    """Ask Ollama to free the loaded models except pinned models (they reload on their next use)."""
    from ishti import CONFIG, keepalive, models
    base = CONFIG["llm"]["base_url"].rsplit("/v1", 1)[0]
    try:
        loaded = [m["name"] for m in keepalive.get(f"{base}/api/ps", timeout=5).json().get("models", [])]
    except Exception:
        return []
    freed = [m for m in loaded if not models.is_pinned(m)]
    for m in freed:
        try:
            models.unload_model(m, reason="gpu_slot_demand")
        except Exception:
            pass
    return freed


@contextlib.contextmanager
def slot(need_mb, what="gpu job", wait_s=60, for_voice=False):
    """One heavy job at a time, placed by the VRAM governor (ishti/vram.py): idle models are unloaded and the planner goes
    a rung lighter to make room; if it still doesn't fit within wait_s the job gets vram.GpuBusy with the plain reason
    (it is never run 'anyway' - that is what made Ollama split a model between GPU and CPU)."""
    from ishti import vram
    if not _heavy.acquire(timeout=wait_s):  # bounded: the voice pipeline must not sit behind an upscale for minutes
        raise vram.GpuBusy("The graphics card is busy with another heavy job")
    try:
        res = vram.reserve(need_mb, what, wait_s, for_voice)
        if not res["ok"]:
            raise vram.GpuBusy("The graphics card is busy: " + res["queued"])
        vram._cache.pop("gpu", None)
        before = vram.gpu()["used_mb"]
        try:
            yield free_mb()
        finally:
            vram._cache.pop("gpu", None)
            vram.learn_job(what, vram.gpu()["used_mb"] - before)  # what this job really took, for next time
            vram.release(res["lease"])
    finally:
        _heavy.release()


if __name__ == "__main__":
    order = []

    def job(n):
        with slot(1, f"job {n}", wait_s=20):
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
