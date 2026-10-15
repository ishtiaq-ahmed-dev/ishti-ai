"""Recoverable observation runner. SQLite transactions serialize app/standalone ticks."""
import os
import sqlite3
import threading
import time

from .engine import cycle
from .store import Store

_stop = threading.Event()
_thread = None
_lock = threading.Lock()


def run(stop=None, store=None, poll_seconds=30, max_ticks=None, runner="standalone"):
    stop, store = stop or threading.Event(), store or Store()
    ticks = 0
    while not stop.is_set():
        try:
            if store.path.exists() and os.environ.get("ISHTI_BUSINESS_DESK", "1") != "0":
                cycle(store=store, runner=runner)
        except (OSError, ValueError, RuntimeError, sqlite3.Error):
            # Never log source content or exception strings that can contain business data/paths.
            try:
                with store.transaction() as state:
                    state["last_error"] = "Observation failed. Check storage/key access and approved source availability."
                    state["heartbeat"] = time.time()
            except (OSError, ValueError, RuntimeError, sqlite3.Error):
                # Propagate storage failure to the standalone task supervisor; no sensitive logs.
                raise RuntimeError("Business storage is unavailable; observation stopped.") from None
        ticks += 1
        if max_ticks is not None and ticks >= max_ticks:
            break
        stop.wait(poll_seconds)


def start():
    global _thread
    with _lock:
        if _thread and _thread.is_alive():
            return
        _stop.clear()
        _thread = threading.Thread(target=run, kwargs={"stop": _stop, "runner": "app"},
                                   name="ishti-business-observer", daemon=True)
        _thread.start()


def stop():
    _stop.set()
    if _thread:
        _thread.join(timeout=3)
