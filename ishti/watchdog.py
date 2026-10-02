"""Watchdog (A1) + the kill-switch hotkey (A2), in their own process so both work even if the server hangs.

  python -m ishti.watchdog [port]
- starts the server (python -m ishti <port>) and polls GET /api/health every 10 s
- the server exited -> restart; 3 failed health polls in a row (after startup) -> it's hung: kill its process tree,
  restart. Crash loop (5 restarts in 10 min) -> wait 5 min, log it to NEEDS.md.
- data/stop.flag (stop.bat writes it) -> stop the server and exit instead of restarting
- the global hotkey (config "kill_hotkey", default Ctrl+Alt+Q) -> POST /api/kill; if the server doesn't answer
  within 2 s it is hung: its whole process tree is killed and it restarts clean
On restart the server finds what was in flight (data/inflight.json) and tells the owner what didn't finish."""
import os
import subprocess
import sys
import threading
import time

import requests

from ishti import CONFIG, ROOT

try:  # best-effort failure log (never breaks the module if logging is unavailable)
    from ishti.core.logutil import swallowed
except Exception:  # pragma: no cover
    def swallowed(*_a, **_k):
        return None


DATA = os.path.dirname(CONFIG["memory"]["db_path"])
STOP_FLAG = os.path.join(DATA, "stop.flag")
LOG = os.path.join(DATA, "watchdog.log")
HOTKEY = CONFIG.get("kill_hotkey", "<ctrl>+<alt>+q")
POLL_S, HUNG_AFTER, GRACE_S = 10, 3, 240
STOP_WAIT_S = 50  # a graceful shutdown takes up to lifecycle.SHUTDOWN_HARD_S (40 s) before it force-exits by itself


def log(msg):
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    print(f"[watchdog] {msg}", flush=True)
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError as _exc:
        swallowed('ishti/watchdog.py:log', _exc)


class Watchdog:
    def __init__(self, port, cmd=None):
        self.port = port
        self.cmd = cmd or [sys.executable, "-m", "ishti", str(port)]
        self.proc = None
        self.restarts = []
        self.hard_killed = threading.Event()

    def url(self, path):
        return f"http://127.0.0.1:{self.port}{path}"

    @staticmethod
    def _out():
        r"""What the server prints before its own logging starts (import errors, crashes at launch), in the shared log folder
        %LOCALAPPDATA%\ISHTI\logs\launch.log; the last run kept as .1. Once running, the server logs to ishti.log itself."""
        from ishti import lifecycle
        path = os.path.join(lifecycle.LOG_DIR, "launch.log")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if os.path.isfile(path) and os.path.getsize(path) > 5_000_000:
            os.replace(path, path + ".1")
        return open(path, "a", encoding="utf-8", buffering=1)

    def start(self):
        env = {**os.environ, "ISHTI_WATCHDOG": "1", "ISHTI_WATCHDOG_PID": str(os.getpid()), "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"}  # a file, not cp1252
        flags = 0
        if os.name == "nt":
            flags = (
                subprocess.CREATE_NO_WINDOW
                | subprocess.DETACHED_PROCESS
                | subprocess.CREATE_NEW_PROCESS_GROUP
            )
        out = self._out()
        self.proc = subprocess.Popen(
            self.cmd,
            cwd=ROOT,
            env=env,
            creationflags=flags,
            stdout=out,
            stderr=subprocess.STDOUT,
        )
        out.close()  # the server holds its own handle
        self.started = time.time()
        log(f"server started (pid {self.proc.pid}) on port {self.port}")

    def healthy(self):
        try:
            return requests.get(self.url("/api/health"), timeout=5).ok
        except requests.RequestException:
            return False

    def kill_tree(self):
        if not self.proc or self.proc.poll() is not None:
            return
        import psutil
        try:
            root = psutil.Process(self.proc.pid)
            procs = root.children(recursive=True) + [root]
            for p in procs:
                p.kill()
            psutil.wait_procs(procs, timeout=10)
        except psutil.NoSuchProcess:
            pass

    def kill_switch(self):
        """The hotkey: ask the server to stop everything; if it can't answer, it's hung - kill and restart it."""
        try:
            r = requests.post(self.url("/api/kill"), json={"reason": "hotkey"}, timeout=2)
            log(f"kill switch: {r.json().get('stopped') if r.ok else r.status_code}")
            return "stopped"
        except (requests.RequestException, ValueError):
            log("kill switch: the server didn't answer in 2 s - killing it (it will restart clean)")
            self.hard_killed.set()
            self.kill_tree()
            return "restarted"

    def listen_hotkey(self):
        try:
            from pynput import keyboard
            keyboard.GlobalHotKeys({HOTKEY: self.kill_switch}).start()
            log(f"kill switch hotkey: {HOTKEY}")
        except Exception as exc:
            log(f"kill switch hotkey unavailable ({exc}); use the Stop button")

    def restart(self, why):
        now = time.time()
        self.restarts = [t for t in self.restarts if now - t < 600] + [now]
        log(f"restarting: {why}")
        try:
            from ishti.skills import log_need
            log_need("error", "watchdog", why, f"server restarted ({len(self.restarts)} in 10 min)")
        except Exception as _exc:
            swallowed('ishti/watchdog.py:Watchdog.restart', _exc)
        if len(self.restarts) >= 5:
            log("crash loop: 5 restarts in 10 minutes - waiting 5 minutes before trying again")
            time.sleep(300)
            self.restarts.clear()
        self.kill_tree()
        self.start()

    def stop_gracefully(self):
        """A stop was requested (data/stop.flag): ask the server to shut down properly and give it time to flush its data
        and exit on its own; only then kill what is left. (Killing at once skipped the data flush.)"""
        try:
            requests.post(self.url("/api/shutdown"), json={}, timeout=3)  # a no-op if the server started the shutdown itself
        except requests.RequestException as _exc:
            swallowed('ishti/watchdog.py:Watchdog.stop_gracefully', _exc)
        end = time.time() + STOP_WAIT_S
        while self.proc.poll() is None and time.time() < end:
            time.sleep(0.5)
        if self.proc.poll() is None:
            log(f"the server did not exit within {STOP_WAIT_S} s - killing its process tree")
        self.kill_tree()

    def run(self, once=False):
        from ishti import lifecycle
        if os.path.exists(STOP_FLAG):
            os.remove(STOP_FLAG)
        lifecycle.adopt_job()  # everything this watchdog starts dies with it, even if it is killed
        self.listen_hotkey()
        self.start()
        misses, last_poll = 0, time.time()
        while True:
            time.sleep(1)  # 1 s ticks: a stop or a crash is noticed at once; the health poll stays every POLL_S
            if os.path.exists(STOP_FLAG):
                log("stop requested - shutting the server down")
                self.stop_gracefully()
                try:
                    os.remove(STOP_FLAG)
                except OSError:
                    pass
                return
            if self.proc.poll() is not None:
                why = "hard kill from the kill switch" if self.hard_killed.is_set() else f"server exited (code {self.proc.returncode})"
                self.hard_killed.clear()
                self.restart(why)
                misses, last_poll = 0, time.time()
            elif time.time() - last_poll >= POLL_S:
                last_poll = time.time()
                if time.time() - self.started > GRACE_S:
                    misses = 0 if self.healthy() else misses + 1
                    if misses >= HUNG_AFTER:
                        self.restart(f"server not answering for {misses * POLL_S} s (hung)")
                        misses = 0
            if once:
                return


if __name__ == "__main__" and len(sys.argv) > 1 and sys.argv[1] == "--self-test":
    import tempfile
    # a fake "server" that crashes after 1 s: the watchdog must notice and restart it
    fake = os.path.join(tempfile.mkdtemp(), "fake_server.py")
    with open(fake, "w") as f:
        f.write("import time; time.sleep(1); raise SystemExit(3)\n")
    LOG = os.path.join(tempfile.mkdtemp(), "watchdog.log")  # the test never writes the real log
    wd = Watchdog(59998, cmd=[sys.executable, fake])
    POLL_S = 2
    import ishti.skills
    ishti.skills.log_need = lambda *a: None  # the test's fake crash must not reach the real NEEDS.md
    wd.start()
    first = wd.proc.pid
    time.sleep(2)
    assert wd.proc.poll() == 3
    wd.restart("self-test crash")
    assert wd.proc.pid != first and wd.proc.poll() is None
    assert wd.kill_switch() == "restarted" and wd.hard_killed.is_set()  # no server answering: treated as hung
    time.sleep(0.5)
    assert wd.proc.poll() is not None  # the tree was killed
    print("[self-check] watchdog OK - crash detected and restarted, unanswered kill switch kills the hung server")
elif __name__ == "__main__":
    Watchdog(int(sys.argv[1]) if len(sys.argv) > 1 else 8888).run()
