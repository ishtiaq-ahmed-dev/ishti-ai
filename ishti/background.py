"""Bounded best-effort post-turn work. No unbounded thread-per-turn fanout.

These tasks are non-authorizing learning/diagnostics, not a durable action queue.
Essential actions belong in the run/workflow store. Queue overflow is observable.
"""
import atexit
import collections
import logging
import queue
import threading
import time

_queue = queue.Queue(maxsize=32)
_lock = threading.Lock()
_threads = []
_stats = collections.Counter()
_closing = False
log = logging.getLogger(__name__)


def _worker():
    from ishti import audit, calltrace
    while True:
        item = _queue.get()
        if item is None:
            _queue.task_done()
            return
        fn, args, context, ledger, queued_at = item
        try:
            with audit.context(**context):
                calltrace.bind(ledger, background=True)
                calltrace.note("background_queue", "wait", (time.monotonic() - queued_at) * 1000)
                fn(*args)
            with _lock:
                _stats["completed"] += 1
        except Exception:
            with _lock:
                _stats["failed"] += 1
            log.exception("Post-turn background job failed")
        finally:
            calltrace.pending(-1, ledger)
            calltrace.bind(None)
            _queue.task_done()


def submit(fn, *args):
    from ishti import audit, calltrace
    global _closing
    with _lock:
        if _closing:
            _stats["rejected_shutdown"] += 1
            return False
        if not _threads:
            for index in range(2):
                thread = threading.Thread(target=_worker, name=f"ishti-background-{index}", daemon=True)
                _threads.append(thread)
                thread.start()
    calltrace.pending(1)
    try:
        _queue.put_nowait((fn, args, dict(audit.current()), calltrace.current(), time.monotonic()))
    except queue.Full:
        calltrace.pending(-1)
        with _lock:
            _stats["rejected_full"] += 1
        log.warning("Post-turn queue full; optional learning job skipped")
        return False
    with _lock:
        _stats["submitted"] += 1
    return True


def snapshot():
    with _lock:
        return {**_stats, "queued": _queue.qsize(), "capacity": _queue.maxsize,
                "workers": len(_threads), "closing": _closing}


def drain(timeout=2):
    deadline = time.monotonic() + timeout
    while _queue.unfinished_tasks and time.monotonic() < deadline:
        time.sleep(0.01)
    return not _queue.unfinished_tasks


def shutdown():
    global _closing
    with _lock:
        _closing = True
    if not drain():
        log.warning("Shutdown left optional learning jobs unfinished: %s", snapshot())


atexit.register(shutdown)
