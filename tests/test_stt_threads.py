"""Regression test for the 2026-09-27 audit's P0: whisper was decoded from two threads (the live partials ran on their
own worker, the final decode on the command thread), and a CTranslate2 model decoded by more than one thread makes the
whole process fail fast (0xC0000409 in ucrtbase - Windows logs it as an "Application Error" and the exit code is
non-zero, so every quit/stop/restart looked like a crash to the watchdog). The fix routes every decode through
voice.stt_call(), which runs it on one dedicated thread.

This test checks the invariant (one decode thread, results and errors crossing the queue intact) and then the real
thing end to end: the two self-checks that used to exit with a fail-fast code are run in subprocesses and must exit 0.
Run:  venv\\Scripts\\python tests\\test_stt_threads.py
"""
import os
import subprocess
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = os.path.join(ROOT, "venv", "Scripts", "python.exe")
sys.path.insert(0, ROOT)

from ishti import voice  # noqa: E402

# ---- 1. stt_call runs the work on one worker thread, wherever it is called from ---------------------------------
seen, done = [], []


def job(tag):
    seen.append((tag, threading.get_ident(), threading.current_thread().name))
    return tag * 2


results = []
threads = [threading.Thread(target=lambda i=i: results.append(voice.stt_call(lambda: job(f"t{i}")))) for i in range(3)]
for t in threads:
    t.start()
for t in threads:
    t.join()
assert sorted(results) == ["t0t0", "t1t1", "t2t2"], results
assert len({tid for _, tid, _ in seen}) == 1, seen  # one thread ran all three
assert seen[0][2] == "stt", seen  # and it is the named STT worker, not a caller
assert voice.stt_call(lambda: job("main"))[0] == "m"  # called from the main thread too
assert len(voice.DECODE_THREADS) == 1, voice.DECODE_THREADS

# the worker is reused, results come back, and an exception is re-raised in the caller (not swallowed)
done.append(voice.stt_call(lambda: job("reuse")))
assert done and len(voice.DECODE_THREADS) == 1
try:
    voice.stt_call(lambda: 1 / 0)
    raise AssertionError("an exception inside stt_call must reach the caller")
except ZeroDivisionError:
    pass

# a decode that itself calls stt_call runs inline: no deadlock, no second thread
nested = voice.stt_call(lambda: voice.stt_call(lambda: job("nested")))
assert nested == "nestednested" and len(voice.DECODE_THREADS) == 1


# ---- 2. the self-checks that used to fail fast must exit 0 (raw Windows exit code) ------------------------------
def exit_code(module, timeout=600):
    """Run a module self-check in its own process group. On timeout the WHOLE tree is killed with taskkill /T:
    a grandchild that outlives its parent is exactly the orphan a test run must never leave behind."""
    p = subprocess.Popen([PY, "-m", module], cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         text=True, encoding="utf-8", errors="replace",
                         creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
    try:
        out, _ = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"], capture_output=True, text=True, timeout=60,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            p.wait(timeout=30)
        except subprocess.TimeoutExpired:
            pass
        raise
    return p.returncode, out


def no_audio_device():
    """True when this PC has no input that actually delivers audio (data/audio_devices.json records the
    probe). `ishti.wake` waits for sound that never comes on such a machine - a mic-less box used to block the
    whole test run for 2 x 600 s and look like a hang, so it is bounded and skipped with a reason instead."""
    import json
    try:
        with open(os.path.join(ROOT, "data", "audio_devices.json"), encoding="utf-8") as f:
            return not (json.load(f).get("usable_for_voice") or [])
    except (OSError, ValueError):
        return False


skipped = []
for module in ("ishti.wake", "ishti.voice"):
    try:
        code, out = exit_code(module, timeout=90 if module == "ishti.wake" else 300)
    except subprocess.TimeoutExpired:
        assert no_audio_device(), (f"{module} did not finish within its budget although this PC has a working "
                                   "input - that is a real failure, not a missing microphone")
        skipped.append(module)
        continue
    assert code == 0, f"{module} exited {code} ({hex(code & 0xFFFFFFFF)}); a fail-fast is 0xC0000409\n{out[-800:]}"
    assert "[self-check]" in out, f"{module} did not finish its self-check\n{out[-500:]}"
print(f"[self-check] stt threads OK - every decode on one thread, exceptions cross the queue, "
      f"ishti.wake and ishti.voice exit 0 (no 0xC0000409 fail-fast)"
      + (f"; SKIPPED {', '.join(skipped)} - no audio input on this PC (data/audio_devices.json)" if skipped else ""))
