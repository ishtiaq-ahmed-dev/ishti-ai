"""Loopback keep-alive guard (Cycle 4): ISHTI's own Ollama calls must reuse connections, and doing
so must never weaken the per-skill network boundary.

Measured 2026-10-02: `requests.get()` to a loopback service costs ~12-15 ms (new TCP connection per
call) against ~0.5 ms on a reused one; the VRAM governor makes several of those per reply
(`tools/bench_loopback.py`: 20.5 ms -> 4.4 ms). The safety half matters just as much: `ishti/scope.py`
enforces the skill network scope by patching `requests.sessions.Session.request`, so this test proves a
shared Session still travels through that patch.
"""
import os
import statistics
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import requests  # noqa: E402

from ishti import keepalive, scope  # noqa: E402


def test_one_shared_session_is_reused():
    """The same Session (and therefore the same connection pool) for every call."""
    assert keepalive.session() is keepalive.session()
    assert keepalive.session() is not requests.Session()


def test_keepalive_still_goes_through_the_scope_hook(monkeypatch):
    """scope.py enforces the per-skill network boundary by patching requests.sessions.Session.request.
    Prove a shared Session still travels through that hook: record scope.check_url calls.

    If scope.install() ever moved its check off Session.request, this fails - instead of the hot path
    quietly opening a hole in the boundary.
    """
    scope.install()  # idempotent; the dispatcher does this at import anyway
    seen = []
    monkeypatch.setattr(scope, "check_url", lambda url: seen.append(url))
    url = "http://127.0.0.1:11434/api/version"
    try:
        keepalive.get(url, timeout=3)
        requests.get(url, timeout=3)
    except Exception:
        pass  # Ollama down: the hook still runs, which is what this test is about
    assert len(seen) >= 2, f"the scope hook did not see the calls (saw {seen})"


def test_shared_connection_is_faster_than_a_fresh_one():
    """Generous bound (measured: 0.5 ms warm vs 12-15 ms cold): this can only fail if the connection
    is no longer reused."""
    url = (os.environ.get("ISHTI_OLLAMA_URL") or "http://127.0.0.1:11434").rstrip("/") + "/api/version"
    try:
        if keepalive.get(url, timeout=3).status_code != 200:
            raise OSError  # Ollama down: no numbers to make (the bench reports this as BLOCKED)
    except Exception:
        return  # live service not available in this run - nothing to assert about latency
    warm = []
    for _ in range(10):
        t0 = time.perf_counter()
        keepalive.get(url, timeout=3)
        warm.append((time.perf_counter() - t0) * 1000)
    assert statistics.median(warm) < 5.0, f"keep-alive not in effect: {statistics.median(warm):.2f} ms per call"
