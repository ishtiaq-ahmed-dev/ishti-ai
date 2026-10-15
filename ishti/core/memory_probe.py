"""Opt-in attribution of Python, process-tree and system memory; no prompt or object contents."""
from collections import deque
import json
import os
from pathlib import Path
import threading
import time
import tracemalloc

import psutil


class Probe:
    def __init__(self, path):
        self.path = Path(path)
        self.samples = deque(maxlen=360)
        self.growth = deque(maxlen=10)
        self.baseline = None
        if not tracemalloc.is_tracing():
            tracemalloc.start(6)

    def sample(self):
        process = psutil.Process()
        children = []
        for child in process.children(recursive=True):
            try:
                children.append({"pid": child.pid, "rss_bytes": child.memory_info().rss})
            except psutil.Error:
                continue  # exited between enumeration and sampling
        current, peak = tracemalloc.get_traced_memory()
        snapshot = tracemalloc.take_snapshot()
        if self.baseline is not None:
            self.growth.append({"at": time.time(), "allocators": [
                {"file": str(s.traceback[0].filename), "line": s.traceback[0].lineno,
                 "bytes": s.size_diff, "count": s.count_diff}
                for s in snapshot.compare_to(self.baseline, "lineno")[:20]]})
        self.baseline = snapshot
        self.samples.append({"at": time.time(), "pid": os.getpid(), "rss_bytes": process.memory_info().rss,
                             "python_bytes": current, "python_peak_bytes": peak,
                             "children": children, "system_used_bytes": psutil.virtual_memory().used})
        return self.samples[-1]

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".tmp")
        temp.write_text(json.dumps({"schema": 1, "samples": list(self.samples), "growth": list(self.growth),
            "limits": "Python allocations exclude native/GPU memory. System RAM includes unrelated processes. Profiling adds overhead."}, indent=2), encoding="utf-8")
        temp.replace(self.path)


def start(path, stopping):
    """Caller must explicitly enable ISHTI_MEMORY_PROFILE=1. One sample per 30 seconds."""
    probe = Probe(path)
    def loop():
        while not stopping():
            try:
                probe.sample()
                probe.save()
            except (OSError, RuntimeError, psutil.Error) as exc:
                import logging
                logging.getLogger(__name__).warning("Memory profiling unavailable: %s", type(exc).__name__)
                return
            time.sleep(30)
    thread = threading.Thread(target=loop, daemon=True, name="memory-profile")
    thread.start()
    return thread
