"""One shared keep-alive HTTP session for ISHTI's own loopback calls (Cycle 4).

Measured 2026-10-02 on this machine (`tools/bench_loopback.py`): ISHTI's own Ollama round-trips cost
**15.5 ms** (`vram.ps(fresh=True)`) and **5.1 ms** (`models._keep_alive` POST) because
`requests.get/post` opens a new TCP connection every single call - **~20 ms of every reply** before the
model has written a single token. With the connection reused, the same round-trips answer in well under a
millisecond.

Safety, checked before shipping: `ishti/scope.py` enforces the per-skill network boundary (and the
kill-switch checkpoint) by patching `requests.sessions.Session.request` - the one choke point *every*
request passes through, keep-alive or not. A shared Session therefore does not weaken the boundary: the
same `check_url()` runs on every call. Nothing here mutates session-level state (no session headers,
cookies or auth; timeouts and payloads stay per-call), and the urllib3 pool behind the Session is
thread-safe, which matters because ISHTI runs requests in parallel.

A per-thread Session would be useless here: the dispatcher starts a new thread per request, so the
connection would never be reused across replies.
"""
import os
import threading

import requests

_SESSION = None
_LOCK = threading.Lock()


def session() -> requests.Session:
    """The shared Session, created once (pool sized for ISHTI's parallel requests)."""
    global _SESSION
    if _SESSION is None:
        with _LOCK:
            if _SESSION is None:
                s = requests.Session()
                adapter = requests.adapters.HTTPAdapter(pool_connections=16, pool_maxsize=32, max_retries=0)
                s.mount("http://", adapter)
                s.mount("https://", adapter)
                _SESSION = s
    return _SESSION


def get(url, **kwargs):
    """requests.get on the shared connection pool."""
    return session().get(url, **kwargs)


def post(url, **kwargs):
    """requests.post on the shared connection pool."""
    return session().post(url, **kwargs)


if __name__ == "__main__":
    import os
    import statistics
    import time

    url = (os.environ.get("ISHTI_OLLAMA_URL") or "http://127.0.0.1:11434").rstrip("/") + "/api/version"
    fresh, warm = [], []
    for _ in range(15):
        t0 = time.perf_counter()
        requests.get(url, timeout=5)
        fresh.append((time.perf_counter() - t0) * 1000)
        t0 = time.perf_counter()
        get(url, timeout=5)
        warm.append((time.perf_counter() - t0) * 1000)
    assert requests.get(url, timeout=5).status_code == get(url, timeout=5).status_code == 200
    print(f"[self-check] keepalive OK - requests.get median {statistics.median(fresh):.2f} ms vs "
          f"shared session {statistics.median(warm):.2f} ms over the same URL")
