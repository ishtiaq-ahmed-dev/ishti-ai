"""Per-request ledger of every model, embedding, skill and web call - and the plan reason behind each skill call.

Why it exists (Prompt 6, PERF-7/8): "where does the time go" and "was that call needed" must be answerable from data, not
guesses, and the rule "no call without a concrete reason in the current plan" must be enforced in code, not in a doc.
dispatcher.handle() opens a ledger per request (thread-local), llm.chat / semantic.embed / dispatcher._run / the web
scope hook add to it, and the result carries `calls`: a summary with counts, per-call timings and `flags`.

A skill call is JUSTIFIED when the route decision names why (rule match, Laya pick, the owner's yes, a correction, a
plan step, an escalation - dispatcher.why()). One without a reason is flagged here, logged, and counted in the summary;
tests/test_calltrace.py makes the simple cases (one chat, one skill) fail if they start fanning out.
Background work after the answer (memory writes, learning) runs in its own threads and is not part of the ledger:
the owner does not wait for it.
"""
import collections
import contextlib
import logging
import threading
import time

log = logging.getLogger("ishti.calltrace")
_tl = threading.local()
RECENT = collections.deque(maxlen=200)     # summaries of the last requests (profiling, /api diagnostics)


def begin(request=""):
    _tl.rec = {"t0": time.perf_counter(), "request": request[:80], "calls": [], "flags": []}


def current():
    return getattr(_tl, "rec", None)


def bind(rec):
    """Attach a ledger to this thread (work fanned out to a worker thread keeps reporting into its request's ledger)."""
    _tl.rec = rec


def carry(fn):
    """Wrap `fn` so that, when run in another thread, it reports into the ledger of the thread that wrapped it."""
    rec = current()

    def run(*a, **k):
        bind(rec)
        try:
            return fn(*a, **k)
        finally:
            _tl.rec = None
    return run


def note(kind, name, ms=None, **info):
    rec = current()
    if rec is not None:
        rec["calls"].append({"kind": kind, "name": name, "at_ms": round((time.perf_counter() - rec["t0"]) * 1000),
                             "ms": None if ms is None else round(ms), **info})


@contextlib.contextmanager
def timed(kind, name, **info):
    t = time.perf_counter()
    try:
        yield
    finally:
        note(kind, name, (time.perf_counter() - t) * 1000, **info)


def flag(message):
    rec = current()
    log.warning("call without plan justification: %s", message)
    if rec is not None:
        rec["flags"].append(message)


def end():
    """Close this thread's ledger -> summary dict (None when none was open)."""
    rec = getattr(_tl, "rec", None)
    _tl.rec = None
    if rec is None:
        return None
    calls = rec["calls"]
    by = collections.Counter(c["kind"] for c in calls)
    out = {"calls": calls, "flags": rec["flags"], "llm_calls": by["llm"], "skill_calls": by["skill"], "net_calls": by["net"],
           "embed_calls": by["embed"], "llm_ms": sum(c["ms"] or 0 for c in calls if c["kind"] == "llm"),
           "total_ms": round((time.perf_counter() - rec["t0"]) * 1000), "request": rec["request"]}
    RECENT.append({k: v for k, v in out.items() if k != "calls"})
    return out


if __name__ == "__main__":      # self-check
    begin("hi")
    note("skill", "chat", 5, why="rule")
    with timed("llm", "planner", model="m"):
        pass
    flag("x called with no reason")
    carried = carry(lambda: note("embed", "nomic", 1))
    t = threading.Thread(target=carried)
    t.start()
    t.join()
    s = end()
    assert s["skill_calls"] == 1 and s["llm_calls"] == 1 and s["embed_calls"] == 1 and s["flags"] and current() is None
    print("calltrace OK")
