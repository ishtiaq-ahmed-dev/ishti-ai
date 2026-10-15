"""System-level memory counters that no per-process sampler can see (kernel pool, cache).

Usage: venv\\Scripts\\python tools/sysmem_probe.py <tag> [interval_s]
Writes metrics/sysmem_<tag>.jsonl, one line per sample, until killed.

Why: a soak can grow "used RAM" without any process growing - that is kernel non-paged pool
(GPU/KMDF driver and handle leaks), paged pool or the system cache. This probe separates
those from user-space working sets so attribution is not guesswork.
Source of the numbers: GetPerformanceInfo (all values in pages).
"""
import ctypes
import datetime
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class PERF_INFORMATION(ctypes.Structure):
    # cb (DWORD) + nine SIZE_T page counts + SIZE_T page size, then three DWORD counters.
    # An all-c_ulong struct is rejected by GetPerformanceInfo with WinError 24 (bad length).
    _fields_ = ([("cb", ctypes.c_ulong)]
                + [(name, ctypes.c_size_t) for name in
                   ("CommitTotal", "CommitLimit", "CommitPeak", "PhysicalTotal", "PhysicalAvailable",
                    "SystemCache", "KernelTotal", "KernelPaged", "KernelNonpaged", "PageSize")]
                + [("HandleCount", ctypes.c_ulong), ("ProcessCount", ctypes.c_ulong),
                   ("ThreadCount", ctypes.c_ulong)])


def sample():
    import psutil
    pi = PERF_INFORMATION()
    pi.cb = ctypes.sizeof(PERF_INFORMATION)
    ok = ctypes.windll.psapi.GetPerformanceInfo(ctypes.byref(pi), pi.cb)
    if not ok:
        raise ctypes.WinError()
    mb = lambda pages: pages * pi.PageSize // (1024 * 1024)
    vm = psutil.virtual_memory()
    return {"iso": datetime.datetime.now().isoformat(timespec="seconds"), "ts": round(time.time(), 1),
            "vm_used_mb": vm.used // (1024 * 1024), "vm_avail_mb": vm.available // (1024 * 1024),
            "commit_mb": mb(pi.CommitTotal), "commit_limit_mb": mb(pi.CommitLimit),
            "phys_avail_mb": mb(pi.PhysicalAvailable), "system_cache_mb": mb(pi.SystemCache),
            "kernel_paged_mb": mb(pi.KernelPaged), "kernel_nonpaged_mb": mb(pi.KernelNonpaged),
            "kernel_total_mb": mb(pi.KernelTotal), "handles": pi.HandleCount,
            "procs": pi.ProcessCount, "threads": pi.ThreadCount}


def main():
    tag = sys.argv[1] if len(sys.argv) > 1 else "run"
    interval = float(sys.argv[2]) if len(sys.argv) > 2 else 5.0
    out_dir = os.path.join(ROOT, "metrics")
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"sysmem_{tag}.jsonl")
    with open(path, "a", encoding="utf-8") as f:
        while True:
            try:
                f.write(json.dumps(sample()) + "\n")
                f.flush()
            except Exception as exc:
                print(f"[sysmem] sample failed: {type(exc).__name__}: {exc}", file=sys.stderr)
            time.sleep(interval)


if __name__ == "__main__":
    main()
