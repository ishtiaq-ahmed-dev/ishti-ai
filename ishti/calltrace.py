"""Local request lifecycle accounting, including bounded background work.

No prompt/body is retained. Foreground response accounting is a snapshot; RECENT
keeps evolving lifecycle totals by request_id while queued learning finishes.
"""
import collections
import contextlib
import logging
import os
import threading
import time
import uuid

log = logging.getLogger("ishti.calltrace")
_tl = threading.local()
RECENT = collections.deque(maxlen=200)


def begin(request=""):
    budget = max(1, min(600, float(os.environ.get("ISHTI_TURN_TIMEOUT_S", "90"))))
    _tl.background = False
    _tl.rec = {"t0": time.perf_counter(), "deadline": time.monotonic()+budget,
               "request_id": uuid.uuid4().hex, "request": "[content omitted]", "calls": [], "flags": [],
               "lock": threading.RLock(), "pending": 0, "recent": None}


def current():
    return getattr(_tl, "rec", None)


def bind(rec, background=False):
    _tl.rec, _tl.background = rec, background


def remaining():
    rec = current()
    if rec is None or getattr(_tl, "background", False):
        return None
    left = rec["deadline"] - time.monotonic()
    if left <= 0:
        raise TimeoutError("This request reached its time budget; unfinished work was not reported as complete.")
    return left


def carry(fn):
    rec = current()
    background = getattr(_tl, "background", False)
    def run(*a, **k):
        prev, phase = current(), getattr(_tl, "background", False)
        bind(rec, background)
        try:
            return fn(*a, **k)
        finally:
            bind(prev, phase)
    return run


def _totals(rec):
    calls = rec["calls"]
    by = collections.Counter(c["kind"] for c in calls)
    usage = [c for c in calls if c["kind"] == "usage"]
    return {"request_id": rec["request_id"], "llm_calls": by["llm"], "skill_calls": by["skill"],
            "net_calls": by["net"], "embed_calls": by["embed"],
            "llm_ms": sum(c["ms"] or 0 for c in calls if c["kind"] == "llm"),
            "input_tokens": sum(c.get("input_tokens", 0) for c in usage) if usage else None,
            "output_tokens": sum(c.get("output_tokens", 0) for c in usage) if usage else None,
            "cached_tokens": sum(c.get("cached_tokens", 0) for c in usage) if usage else None,
            "usage_reports": len(usage), "usage_complete": by["llm"] > 0 and len(usage) == by["llm"],
            "background_calls": sum(bool(c.get("background")) for c in calls),
            "background_pending": rec["pending"], "lifecycle_complete": rec["pending"] == 0,
            "lifecycle_ms": round((time.perf_counter()-rec["t0"])*1000)}


def _refresh(rec):
    if rec.get("recent") is not None:
        rec["recent"].update(_totals(rec))


def pending(delta, rec=None):
    rec = rec or current()
    if rec is not None:
        with rec["lock"]:
            rec["pending"] = max(0, rec["pending"]+delta)
            _refresh(rec)


def note(kind, name, ms=None, **info):
    rec = current()
    if rec is not None:
        with rec["lock"]:
            rec["calls"].append({"kind": kind, "name": name,
                                 "at_ms": round((time.perf_counter()-rec["t0"])*1000),
                                 "ms": None if ms is None else round(ms),
                                 "background": getattr(_tl, "background", False), **info})
            _refresh(rec)


@contextlib.contextmanager
def timed(kind, name, **info):
    start = time.perf_counter()
    try:
        yield
    finally:
        note(kind, name, (time.perf_counter()-start)*1000, **info)


def flag(message):
    log.warning("call without plan justification: %s", message)
    rec = current()
    if rec is not None:
        with rec["lock"]:
            rec["flags"].append(message)


def end():
    rec = current()
    bind(None)
    if rec is None:
        return None
    with rec["lock"]:
        out = {**_totals(rec), "calls": list(rec["calls"]), "flags": list(rec["flags"]),
               "total_ms": round((time.perf_counter()-rec["t0"])*1000), "request": rec["request"]}
        rec["recent"] = {k: v for k, v in out.items() if k != "calls"}
        RECENT.append(rec["recent"])
        return out
