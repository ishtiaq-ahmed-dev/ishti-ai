"""Hard kill switch (A2): one call stops EVERYTHING and returns ISHTI to idle - no confirmation, no delay.
Different from barge-in (which pauses to listen and keeps context): this ends speech, model generation, browser
steps, tool subprocesses, recordings and pending yes/no questions.

  kill(reason)   bump the generation, run every registered stop hook, end tool subprocesses
  check()        raise Cancelled if a kill happened since this request started (called in the LLM token loop, before
                 each model call and before each browser step)
  begin()        mark the start of a request (the dispatcher calls it)
Triggers: the global hotkey (watchdog process, so it works even if the server hangs - see ishti/watchdog.py),
POST /api/kill (the dashboard's Stop button), and the in-process hotkey when running without the watchdog."""
import threading
import time

_gen = 0
_last = {"at": 0.0, "reason": ""}
_local = threading.local()
_hooks = []
# tool subprocesses a skill may have started (never the browser's MCP servers, Ollama or the watchdog)
TOOL_PROCESSES = {"ffmpeg.exe", "ffprobe.exe", "realesrgan-ncnn-vulkan.exe", "robocopy.exe", "rg.exe", "winget.exe",
                  "yt-dlp.exe", "7z.exe", "python.exe", "pythonw.exe", "powershell.exe"}


def on_kill(fn):
    """Register a stop hook (talker.stop, end recordings, cancel listeners...). Returns fn (usable as a decorator)."""
    _hooks.append(fn)
    return fn


def begin():
    _local.gen = _gen


def killed():
    """True if the kill switch fired since this thread's request began."""
    return getattr(_local, "gen", _gen) != _gen


def check():
    if killed():
        from ishti.llm import Cancelled
        raise Cancelled("stopped by the kill switch")


def kill(reason="kill switch"):
    """Stop everything now. Returns what was stopped (for the log/reply)."""
    global _gen
    _gen += 1
    _last.update(at=time.time(), reason=reason)
    stopped = []
    for fn in list(_hooks):
        try:
            got = fn()
            if got:
                stopped.append(str(got))
        except Exception as exc:
            stopped.append(f"hook {getattr(fn, '__name__', fn)} failed: {exc}")
    n = end_tool_processes()
    if n:
        stopped.append(f"{n} tool process(es)")
    try:
        from ishti.audit import record
        record("KILL SWITCH", f"{reason}: {', '.join(stopped) or 'nothing was running'}", initiator="owner", skill="killswitch")
    except Exception:
        pass
    return stopped


def end_tool_processes():
    """Terminate FFmpeg/robocopy/code-runner... children of this process (not the MCP browser servers)."""
    try:
        import psutil
        me = psutil.Process()
        victims = [p for p in me.children(recursive=True) if (p.name() or "").lower() in TOOL_PROCESSES
                   and not any((a or "").lower().endswith(("mcp", "@playwright/mcp")) for a in _cmdline(p))]
        for p in victims:
            p.kill()
        psutil.wait_procs(victims, timeout=3)
        return len(victims)
    except Exception:
        return 0


def _cmdline(p):
    try:
        return p.cmdline()
    except Exception:
        return []


def last():
    return dict(_last)


def generation():
    """Bumped by every kill: a waiter compares it to see that a kill happened."""
    return _gen


def _capture():
    from ishti import audit
    return getattr(_local, "gen", None), dict(audit.current())


def _apply(saved):
    from ishti import audit
    gen, ctx = saved
    if gen is not None:
        _local.gen = gen
    audit._local.ctx = ctx


def propagate():
    """Threads and thread pools started while handling a request inherit its kill generation and its audit/scope
    context (which skill, who initiated it) - otherwise deep research's worker threads could be neither stopped by
    the kill switch nor held to the skill's scope. Installed once."""
    import concurrent.futures
    if getattr(threading.Thread, "_ishti", False):
        return
    start, run = threading.Thread.start, threading.Thread.run

    def t_start(self):
        self._ishti_ctx = _capture()
        return start(self)

    def t_run(self):
        if getattr(self, "_ishti_ctx", None):
            _apply(self._ishti_ctx)
        return run(self)
    threading.Thread.start, threading.Thread.run, threading.Thread._ishti = t_start, t_run, True
    submit = concurrent.futures.ThreadPoolExecutor.submit

    def p_submit(self, fn, *a, **k):
        saved = _capture()

        def job():
            prev = _capture()
            _apply(saved)  # pool threads are reused: set the submitter's context for this job only
            try:
                return fn(*a, **k)
            finally:
                _apply(prev)
        return submit(self, job)
    concurrent.futures.ThreadPoolExecutor.submit = p_submit


if __name__ == "__main__":
    import subprocess
    import sys
    from ishti.llm import Cancelled
    begin()
    assert not killed()
    heard = []
    on_kill(lambda: heard.append("talker") or "speech")
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    t0 = time.time()
    stopped = kill("self-test")
    assert killed() and "speech" in stopped and any("tool process" in s for s in stopped), stopped
    assert child.wait(timeout=5) is not None and time.time() - t0 < 5
    try:
        check()
        raise AssertionError("check() didn't raise after a kill")
    except Cancelled:
        pass
    begin()  # the next request starts clean
    check()
    propagate()
    import concurrent.futures
    from ishti.audit import context, current
    seen = {}
    with context(skill="deep_research"):
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            assert pool.submit(lambda: current().get("skill")).result() == "deep_research"  # the pool job knows its skill
        th = threading.Thread(target=lambda: seen.update(skill=current().get("skill"), killed=killed()))
        kill("thread test")
        th.start()
        th.join()
    assert seen == {"skill": "deep_research", "killed": True}, seen  # a worker of a killed request sees the kill
    print(f"[self-check] killswitch OK - hooks ran, child process ended in {time.time() - t0:.2f} s, requests cancelled, next starts clean")
