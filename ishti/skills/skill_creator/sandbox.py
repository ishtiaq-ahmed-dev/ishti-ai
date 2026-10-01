"""Run one AI-generated script under a hard budget: a per-run Windows Job Object (RESEARCH.md R2, tested live).

What this enforces, measured on this PC (R2 §2): one subprocess, 512 MB process memory / 1 GB job memory,
<=8 processes, 90 s of user CPU time (100-ns ticks), a 120 s wall-clock cap in the parent, a cleaned
environment (no PYTHONPATH/PYTHONSTARTUP/proxy vars), a pinned cwd and CREATE_NO_WINDOW. The process is
assigned to the job IMMEDIATELY after Popen, which closes the spawn race; on timeout the whole job is
terminated (no orphan) and the pipes are drained.

What this does NOT protect against (R2 "what it does NOT protect against", stated, not hidden):
- the network is NOT restricted - `net=False` does not block anything; the return dict says so;
- filesystem writes are NOT jailed - cwd is just the starting folder; scope.py is what guards real writes;
- an attacker who can schedule a task/WMI outside the job escapes it; and a script that catches MemoryError
  is only stopped by the CPU/wall limits. This is resource containment, not a security boundary.

pywin32 (win32job/win32api, pywin32 312 in this venv) is required for the hard caps. Without it the module
falls back to plain subprocess + timeout + `taskkill /F /T` and flags hard_limits=False so the caller can
report that the caps were NOT enforced - it never pretends the job object ran.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time

CREATE_NO_WINDOW = 0x08000000
CREATE_NEW_PROCESS_GROUP = 0x00000200
PROCESS_SET_QUOTA = 0x0100
PROCESS_TERMINATE = 0x0001

KILL_ON_JOB_CLOSE = 0x2000
PROCESS_MEMORY = 0x100
JOB_MEMORY = 0x200
ACTIVE_PROCESS = 0x8
JOB_TIME = 0x4

TICKS_PER_SECOND = 10_000_000  # 100-ns ticks (SetInformationJobObject: PerJobUserTimeLimit)
CLEAN_VARS = ("PYTHONPATH", "PYTHONSTARTUP", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
              "http_proxy", "https_proxy", "all_proxy", "no_proxy")


def _job_api():
    """(win32api, win32job) or (None, None), printing the exact reason the caps will not be enforced."""
    try:
        import win32api
        import win32job
        return win32api, win32job
    except ImportError as exc:
        print(f"[sandbox] pywin32 unavailable ({type(exc).__name__}: {exc}): falling back to plain "
              f"subprocess + timeout + taskkill - the hard memory/CPU/process caps are NOT enforced")
        return None, None


def clean_env(base=None) -> dict:
    """No PYTHONPATH/PYTHONSTARTUP, no proxy variables (R2 §1): the child starts from a known environment."""
    env = dict(os.environ if base is None else base)
    for var in CLEAN_VARS:
        env.pop(var, None)
    return env


def _decode(raw) -> str:
    return (raw or b"").decode("utf-8", errors="replace")


def run_script(path, *, timeout_s=120, mem_mb=512, net=False, write_dir=None) -> dict:
    """Run `python <path>` once under the Job-Object caps and return the raw outcome.

    Keys: rc, stdout, stderr, peak_mb (job accounting), killed (timeout -> job terminated), wall_s,
    hard_limits (True = the Job Object enforced the caps; False = pywin32 missing, only timeout+taskkill),
    net_restricted (always False - and the note says so), net_note, job (why, when the fallback ran).
    `net` is accepted for callers that want to record intent; nothing is blocked today (R2).
    """
    path = os.path.abspath(str(path))
    if not os.path.isfile(path):
        raise FileNotFoundError(f"no script to run: {path}")
    cwd = os.path.abspath(write_dir or os.path.dirname(path))
    env = clean_env()
    win32api, win32job = _job_api()
    job = None
    if win32job:
        job = win32job.CreateJobObject(None, "")
        info = win32job.QueryInformationJobObject(job, win32job.JobObjectExtendedLimitInformation)
        basic = info["BasicLimitInformation"]
        basic["LimitFlags"] |= (KILL_ON_JOB_CLOSE | PROCESS_MEMORY | JOB_MEMORY | ACTIVE_PROCESS | JOB_TIME)
        info["ProcessMemoryLimit"] = int(mem_mb) * 1024 * 1024
        info["JobMemoryLimit"] = int(mem_mb) * 2 * 1024 * 1024
        basic["ActiveProcessLimit"] = 8
        basic["PerJobUserTimeLimit"] = 90 * TICKS_PER_SECOND
        win32job.SetInformationJobObject(job, win32job.JobObjectExtendedLimitInformation, info)

    t0 = time.perf_counter()
    p = subprocess.Popen([sys.executable, path], cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         creationflags=CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP)
    assigned = False
    if job:
        try:  # IMMEDIATELY after Popen: a child spawned before assignment would not inherit the caps
            handle = win32api.OpenProcess(PROCESS_SET_QUOTA | PROCESS_TERMINATE, False, p.pid)
            win32job.AssignProcessToJobObject(job, handle)
            assigned = True
        except Exception as exc:
            print(f"[sandbox] job assignment failed ({type(exc).__name__}: {exc}): hard caps NOT enforced "
                  f"for pid {p.pid}; falling back to timeout + taskkill")
    killed = False
    try:
        out, err = p.communicate(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        killed = True
        if assigned:
            win32job.TerminateJobObject(job, 1)  # the whole job/process tree, not just the direct child
        else:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)], capture_output=True,
                           creationflags=CREATE_NO_WINDOW)
        try:
            out, err = p.communicate(timeout=15)  # drain the pipes so nothing blocks on a full buffer
        except subprocess.TimeoutExpired:
            p.kill()
            out, err = p.communicate()
    peak = 0
    if assigned:
        try:
            info = win32job.QueryInformationJobObject(job, win32job.JobObjectExtendedLimitInformation)
            peak = int(info.get("PeakProcessMemoryUsed") or 0)
        except Exception as exc:
            print(f"[sandbox] could not read job accounting ({type(exc).__name__}: {exc}): peak_mb stays 0")
        win32api.CloseHandle(job)  # KILL_ON_JOB_CLOSE: closing also kills any stray child
    return {
        "rc": p.returncode,
        "stdout": _decode(out),
        "stderr": _decode(err),
        "peak_mb": round(peak / (1024 * 1024), 1) if peak else 0.0,
        "killed": killed,
        "wall_s": round(time.perf_counter() - t0, 2),
        "hard_limits": bool(assigned),
        "net_restricted": False,
        "net_note": ("network NOT restricted - the sandbox caps memory/CPU/processes/time only (RESEARCH.md R2); "
                     "use the skill's manifest scope for network policy"),
        "job": "win32job Job Object" if assigned else "fallback: no Job Object (pywin32 unavailable)",
    }


if __name__ == "__main__":
    import tempfile

    if _job_api()[1] is None:
        print("[self-check] sandbox SKIPPED - pywin32/win32job is not installed here, so the 512 MB job cap "
              "cannot be proven; the fallback (subprocess + timeout + taskkill, hard_limits=False) is all that runs")
        raise SystemExit(0)

    scratch = tempfile.mkdtemp(prefix="ishti_sandbox_")
    hog = os.path.join(scratch, "hog.py")
    with open(hog, "w", encoding="utf-8") as f:
        f.write('chunks = []\n'
                'try:\n'
                '    for _ in range(40):\n'
                '        chunks.append(bytearray(64 * 1024 * 1024))\n'
                'except MemoryError:\n'
                '    print("MEMORY_ERROR")\n'
                '    raise SystemExit(3)\n'
                'print("UNEXPECTED_SUCCESS")\n')
    res = run_script(hog)  # asks for ~2.5 GB; the 512 MB cap must stop it
    assert res["hard_limits"], res
    assert "UNEXPECTED_SUCCESS" not in res["stdout"], res
    assert res["rc"] != 0, res
    assert 0 < res["peak_mb"] <= 1024, res  # job accounting shows the cap held

    tok = os.path.join(scratch, "tok.py")
    with open(tok, "w", encoding="utf-8") as f:
        f.write('print("SBX-TOKEN-OK")\n')
    res2 = run_script(tok)
    assert res2["rc"] == 0 and "SBX-TOKEN-OK" in res2["stdout"] and res2["hard_limits"], res2
    assert res2["net_restricted"] is False and "NOT restricted" in res2["net_note"], res2
    print(f"[self-check] sandbox OK - 2.5 GB allocation died under the 512 MB cap (rc={res['rc']}, peak "
          f"{res['peak_mb']} MB, hard limits on); a normal script ran clean (rc={res2['rc']}) and the result "
          f"states the network is NOT restricted")
