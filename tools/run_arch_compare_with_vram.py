"""Run tools/arch_compare.py (the same 20 rows through arch1, then arch2) while sampling the GPU every 2 s.

Reports, per architecture phase: checks passed, mean/median/max seconds (from arch_compare itself) and the VRAM side - the
peak share of the 16 GB card and every sample in which Ollama showed a model that was NOT 100 % on the GPU (a partial
offload, the hard failure of the VRAM governor). Prompt 6 step 9 / PERF-1: the resident set stays under 80 % per architecture.
    venv\\Scripts\\python tools\\run_arch_compare_with_vram.py   (ISHTI stopped; takes 15-30 minutes)"""
import os
import re
import subprocess
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
phase = {"now": "boot"}
peak, partial, total_mb = {}, {}, [16303]


def smi():
    out = subprocess.run(["nvidia-smi", "--query-gpu=memory.used,memory.total", "--format=csv,noheader,nounits"], capture_output=True, text=True).stdout
    used, tot = (int(x) for x in out.strip().splitlines()[0].split(","))
    total_mb[0] = tot
    return used


def ollama_partial():
    out = subprocess.run(["ollama", "ps"], capture_output=True, text=True).stdout.splitlines()[1:]
    return [ln.split()[0] for ln in out if ln.strip() and "100% GPU" not in ln]


def sampler(stop):
    while not stop.is_set():
        try:
            p = phase["now"]
            peak[p] = max(peak.get(p, 0), smi())
            for m in ollama_partial():
                partial.setdefault(p, set()).add(m)
        except Exception:
            pass
        stop.wait(2)


def main():
    env = {**os.environ, "ISHTI_TEST_MODE": "1", "PYTHONIOENCODING": "utf-8"}
    stop = threading.Event()
    threading.Thread(target=sampler, args=(stop,), daemon=True).start()
    proc = subprocess.Popen([sys.executable, os.path.join(ROOT, "tools", "arch_compare.py"), "--reset"], cwd=ROOT, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
    lines = []
    for ln in proc.stdout:
        lines.append(ln)
        m = re.match(r"=== phase (\w+)", ln)
        if m:
            phase["now"] = m.group(1).lower()
        if ln.startswith(("=== SUMMARY", "arch1:", "arch2:", "failed checks")):
            print(ln.rstrip(), flush=True)
    proc.wait()
    stop.set()
    for p, mx in peak.items():
        print(f"VRAM {p}: peak {mx} MiB = {100 * mx / total_mb[0]:.1f}% of {total_mb[0]} MiB; partial offloads seen: {sorted(partial.get(p, [])) or 'none'}")
    open(os.path.join(ROOT, "_work", "arch_compare_run.log"), "w", encoding="utf-8").write("".join(lines))
    sys.exit(proc.returncode)


if __name__ == "__main__":
    main()
