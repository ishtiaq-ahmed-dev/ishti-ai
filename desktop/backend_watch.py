"""Shared backend-life watcher for the desktop processes (launcher / app window / tray).

Two bugs this fixes (2026-09-29):
1. POST /api/shutdown (or the tray's Stop) stopped the backend but the desktop processes never
   learned about it - the launcher kept a window open on a dead backend and tray icons piled up
   across launches.
2. Worse: clicking Shut down showed the overlay at 100% but the WINDOW STAYED OPEN - the JS ->
   Python close call (pywebview api.force_close) is not reliable from the field. The backend now
   writes run/shutting_down.flag the moment a shutdown begins, and this watcher closes the shell
   on seeing it. Pure filesystem signal: no JavaScript, no HTTP, cannot fail the same way.

Rules:
- The flag closes the shell quickly (the shutdown overlay is showing; 1 s poll, small delay so the
  overlay's final frames are visible).
- A backend that was up and then stays GONE past a grace period also closes the shell (crash case).
- A backend that never comes up fires nothing - the splash screen's error state owns that case.
"""
import os
import threading
import time
import urllib.request

GRACE_S = float(os.environ.get("ISHTI_BACKEND_LOST_GRACE_S", "25"))
FLAG_GRACE_S = float(os.environ.get("ISHTI_FLAG_GRACE_S", "2.5"))   # let the overlay finish gracefully
POLL_S = 2.0
FLAG_POLL_S = 1.0


def health_ok(port=8888, timeout=1.0) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def shutdown_flag_path() -> str:
    """Same path as ishti.lifecycle.SHUTDOWN_FLAG, without importing the package (the desktop shell
    must stay light): %LOCALAPPDATA%\\ISHTI\\run\\shutting_down.flag."""
    local = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(local, "ISHTI", "run", "shutting_down.flag")


def clear_flag() -> None:
    """Delete a previous run's shutdown flag (called by the launcher before a fresh boot)."""
    try:
        os.remove(shutdown_flag_path())
    except OSError:
        pass


def start(on_lost, port=8888, grace_s=None, poll_s=None):
    """Run the watcher in a daemon thread. Returns the thread (for tests)."""
    t = threading.Thread(target=_watch, args=(on_lost, port, float(grace_s or GRACE_S),
                                              float(poll_s or POLL_S)), daemon=True)
    t.start()
    return t


def _flag_present() -> bool:
    try:
        return os.path.isfile(shutdown_flag_path())
    except Exception:
        return False


def _watch(on_lost, port, grace_s, poll_s):
    seen_up = False
    down_since = None
    flag_since = None
    while True:
        if _flag_present():
            flag_since = flag_since or time.time()
            if time.time() - flag_since >= FLAG_GRACE_S:
                try:
                    on_lost()
                except Exception:
                    pass
                return
        elif health_ok(port):
            seen_up = True
            down_since = None
        elif seen_up:
            down_since = down_since or time.time()
            if time.time() - down_since >= grace_s:
                try:
                    on_lost()
                except Exception:
                    pass
                return
        time.sleep(FLAG_POLL_S if flag_since else poll_s)
