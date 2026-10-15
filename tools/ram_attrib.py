"""Attribute RAM growth under the standard mixed load to processes and Python call sites.

Usage:
    venv\\Scripts\\python tools/ram_attrib.py <N> <window_s> <tag>

Supervises tools/load_ramp.py in a separate scratch worker and, while it
runs, samples every 2 s:

    system  used_mb / avail_mb / commit_used_mb          (psutil + GlobalMemoryStatusEx)
    procs   pid, name, rss_mb, private_mb, threads       for python|llama|ollama|ishti
                                                         + the 12 largest by RSS + MemCompression
    pygrowth  optional --trace-python, sampler supervisor only, every 30 s

Output: metrics/ram_attrib_<tag>.jsonl (one "s" line per sample, one "pygrowth" line per 30 s)
and metrics/ram_attrib_<tag>_summary.json (per-pid least-squares slope in MB/min).
"""
import ctypes
import json
import os
import re
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

WATCH = re.compile(r"python|llama|ollama|ishti|node", re.I)
OUT_DIR = os.path.join(ROOT, "metrics")


class _MemStatus(ctypes.Structure):
    _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]


def _commit_used_mb():
    """Commit charge in MB (pagefile-backed commit, not working set)."""
    try:
        st = _MemStatus()
        st.dwLength = ctypes.sizeof(_MemStatus)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st))
        return (st.ullTotalPageFile - st.ullAvailPageFile) // (1024 * 1024)
    except Exception:
        return None


def process_role(name, command):
    """Classify explicit app entry points, not every Python job on a shared PC."""
    lower = name.lower()
    if 'ollama' in lower or 'llama-server' in lower:
        return 'model_runtime'
    args = [str(arg).replace('\\', '/').lower() for arg in (command or [])]
    if 'python' in lower and (any(args[i:i + 2] == ['-m', 'ishti'] for i in range(len(args) - 1))
                              or any(arg == 'interface/web_ui/server.py' or arg.endswith('/interface/web_ui/server.py') for arg in args)):
        return 'app'
    if 'ishti' in lower:
        return 'app'
    if any(arg in ('tools/load_ramp.py', 'tools/ram_attrib.py') or arg.endswith('/tools/load_ramp.py')
           or arg.endswith('/tools/ram_attrib.py') for arg in args):
        return 'harness'
    return 'other'


def sample(psutil):
    vm = psutil.virtual_memory()
    from tools.sysmem_probe import sample as sys_sample
    try:
        sys2 = sys_sample()
    except Exception:
        sys2 = None
    procs = []
    try:
        for p in psutil.process_iter(["pid", "name", "num_threads", "memory_info", "create_time", "cmdline"]):
            try:
                name = p.info["name"] or ""
                mi = p.info["memory_info"]
                if mi is None:
                    continue
                procs.append({"pid": p.info["pid"], "name": name,
                              "rss_mb": mi.rss // (1024 * 1024),
                              "private_mb": getattr(mi, "private", 0) // (1024 * 1024),
                              "threads": p.info["num_threads"], "created": p.info["create_time"],
                              "role": process_role(name, p.info["cmdline"])})
            except Exception:
                continue  # exited, or a field vanished between enumeration and sampling
    except Exception as exc:
        from ishti.core.logutil import swallowed
        swallowed("ram_attrib.process_enumeration", exc)
    top = sorted(procs, key=lambda r: r["rss_mb"], reverse=True)[:12]
    keep = {r["pid"] for r in top if not WATCH.search(r["name"])}
    watched = [r for r in procs if WATCH.search(r["name"]) or r["pid"] in keep
               or r["name"] in ("MemCompression", "System")]
    return {"ts": round(time.time(), 1),
            "sys": {"used_mb": vm.used // (1024 * 1024), "avail_mb": vm.available // (1024 * 1024),
                    "commit_used_mb": _commit_used_mb()},
            "kernel": sys2,
            "n_procs": len(procs),
            "procs": sorted(watched, key=lambda r: r["rss_mb"], reverse=True)}


def run_sampler(psutil, out_path, stop, trace_python=False):
    import tracemalloc
    owned_trace = trace_python and not tracemalloc.is_tracing()
    if owned_trace:
        tracemalloc.start(6)
    base = tracemalloc.take_snapshot() if trace_python else None
    next_growth = time.time() + 30
    with open(out_path, "x", encoding="utf-8") as f:
        f.write(json.dumps({"_meta": {"pid": os.getpid(), "started": time.time(),
                                      "trace_python": trace_python, "trace_scope": "sampler supervisor only"}}) + "\n")
        while not stop.is_set():
            try:
                f.write(json.dumps({"s": sample(psutil)}) + "\n")
                f.flush()
                if trace_python and time.time() >= next_growth:
                    next_growth = time.time() + 30
                    cur = tracemalloc.take_snapshot()
                    diff = cur.compare_to(base, "lineno")[:12]
                    base = cur
                    f.write(json.dumps({"pygrowth": [
                        {"file": str(s.traceback[0].filename), "line": s.traceback[0].lineno,
                         "bytes": s.size_diff, "count": s.count_diff} for s in diff]}) + "\n")
                    f.flush()
            except Exception as exc:                      # a broken sampler must not kill the load
                f.write(json.dumps({"err": f"{type(exc).__name__}: {exc}"}) + "\n")
            stop.wait(2.0)
    if owned_trace:
        tracemalloc.stop()


def slopes(rows):
    """Per (pid, name): least-squares MB/min from first to last appearance."""
    seen = {}
    for r in rows:
        for p in r["s"]["procs"]:
            seen.setdefault((p["pid"], p["name"], p.get("created")), []).append((r["s"]["ts"], p))
    out = []
    for (pid, name, created), pts in seen.items():
        if len(pts) < 3 or pts[-1][0] - pts[0][0] < 20:
            continue
        t0 = pts[0][0]
        xs = [t - t0 for t, _ in pts]
        n = len(xs)
        xm = sum(xs) / n
        for field in ("rss_mb", "private_mb"):
            ys = [p[field] for _, p in pts]
            ym = sum(ys) / n
            den = sum((x - xm) ** 2 for x in xs) or 1.0
            mb_min = sum((x - xm) * (y - ym) for x, y in zip(xs, ys)) / den * 60.0
            if field == "rss_mb":
                row = {"pid": pid, "name": name, "created": created, "role": pts[0][1].get("role"), "rss_first": ys[0], "rss_last": ys[-1],
                       "rss_delta_mb": ys[-1] - ys[0], "rss_mb_min": round(mb_min, 1),
                       "threads_first": pts[0][1]["threads"], "threads_last": pts[-1][1]["threads"]}
            else:
                row["priv_first"] = ys[0]
                row["priv_last"] = ys[-1]
                row["priv_delta_mb"] = ys[-1] - ys[0]
                row["priv_mb_min"] = round(mb_min, 1)
        out.append(row)
    out.sort(key=lambda r: -r["rss_delta_mb"])
    return out


def system_slopes(rows):
    series = [(r["s"]["ts"], r["s"]["sys"]) for r in rows]
    if not series:
        return {"status": "UNMEASURED", "reason": "no attributed samples"}
    t0 = series[0][0]
    out = {}
    kernel = [(r["s"]["ts"], r["s"].get("kernel") or {}) for r in rows]
    first_k = next((k for _, k in kernel if k), {})
    last_k = next((k for _, k in reversed(kernel) if k), {})
    if first_k and last_k:
        out["kernel"] = {key: {"first": first_k.get(key), "last": last_k.get(key),
                               "delta": (last_k.get(key) or 0) - (first_k.get(key) or 0)}
                         for key in ("kernel_nonpaged_mb", "kernel_paged_mb", "system_cache_mb",
                                     "commit_mb", "handles", "threads")}
    for key in ("used_mb", "avail_mb", "commit_used_mb"):
        pts = [(t - t0, s[key]) for t, s in series if s.get(key) is not None]
        if len(pts) < 3:
            continue
        n = len(pts)
        xm = sum(x for x, _ in pts) / n
        ym = sum(y for _, y in pts) / n
        den = sum((x - xm) ** 2 for x, _ in pts) or 1.0
        out[key] = {"first": pts[0][1], "last": pts[-1][1], "delta": pts[-1][1] - pts[0][1],
                    "mb_min": round(sum((x - xm) * (y - ym) for x, y in pts) / den * 60.0, 1),
                    "peak": max(y for _, y in pts)}
    return out


def main(argv=None):
    import psutil
    from tools import load_ramp as ramp
    argv = list(sys.argv[1:] if argv is None else argv)
    trace_python = '--trace-python' in argv
    if trace_python:
        argv.remove('--trace-python')
    args = ramp.arguments(argv)
    config, path = ramp.reserve_run(args)
    tag = config['tag']
    out_path = os.path.join(config['output_dir'], f"ram_attrib_{tag}.jsonl")
    stop = threading.Event()
    th = threading.Thread(target=run_sampler, args=(psutil, out_path, stop, trace_python),
                          daemon=True, name="ram-attrib")
    th.start()
    t0 = time.monotonic()
    try:
        workload = ramp.supervise(config, path, args)
    finally:
        stop.set()
        th.join(timeout=10)
    rows = []
    with open(out_path, encoding="utf-8") as stream:
        for line in stream:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if "s" in row:
                rows.append(row)
    from tools.qa.ram_gate import evaluate
    summary = {"tag": tag, "level": args.level, "window_s": args.window_s,
               "wall_s": round(time.monotonic() - t0, 1), "samples": len(rows),
               "system": system_slopes(rows), "procs": slopes(rows), "out": out_path,
               "workload": workload, "app_gate": evaluate(rows),
               "trace_scope": "sampler supervisor only" if trace_python else "disabled"}
    with open(os.path.join(config['output_dir'], f"ram_attrib_{tag}_summary.json"), "x", encoding="utf-8") as stream:
        json.dump(summary, stream, indent=2)
    print(json.dumps(summary, indent=2))
    return 0 if workload['status'] == 'completed' and not workload['errors'] and workload['turns'] else 2


if __name__ == "__main__":
    raise SystemExit(main())
