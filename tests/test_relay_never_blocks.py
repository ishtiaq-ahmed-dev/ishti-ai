"""WP-COUCOU acceptance: the relay is fire-and-forget inside a hard budget (COU-13).

The agent's hook path calls send(); it must NEVER block the agent beyond budget_ms:
  R-1 no overlay listening  -> gives up inside the budget, returns False, no raise;
  R-2 overlay slow/busy     -> same: gives up inside the budget while the overlay
                               is mid-processing; the overlay itself is unharmed;
  R-3 garbage/edge inputs   -> bool out, never an exception;
  R-4 concurrent senders    -> every caller returns within its budget, nobody hangs.

Run: venv\\Scripts\\python -m pytest tests/test_relay_never_blocks.py -q
"""
import os
import sys
import threading
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

pytest.importorskip("win32pipe", reason="pywin32 not installed - overlay relay is Windows-only")

from ishti import overlay_pipe as op  # noqa: E402

SLACK_MS = 250          # scheduling noise allowed on top of the declared budget


def _serve_thread(got, stop, max_msgs=None):
    t = threading.Thread(target=op.serve, args=(got.append, stop, max_msgs), daemon=True)
    t.start()
    return t


def test_no_overlay_gives_up_inside_budget_and_returns_false():
    t0 = time.monotonic()
    result = op.send(b'{"hook_event_name":"Stop"}', budget_ms=150)
    elapsed_ms = (time.monotonic() - t0) * 1000
    assert result is False
    assert elapsed_ms < 150 + SLACK_MS, f"relay overran budget: {elapsed_ms:.0f} ms"


def test_slow_overlay_never_blocks_the_relay():
    """Overlay takes 600 ms per message; the relay must not wait for processing -
    and a second send during the stall must give up inside ITS budget (False, fast),
    leaving the overlay free to finish message 1 and accept message 3 later."""
    stop = threading.Event()
    got, release = [], threading.Event()

    def slow_on_message(m):
        got.append(m)
        release.wait(2)                     # simulated slow overlay work

    t = threading.Thread(target=op.serve, args=(slow_on_message, stop), daemon=True)
    t.start()
    try:
        assert op.send(b"first", budget_ms=2000), "send #1 must reach the live overlay"
        # overlay now busy for up to 600 ms; this send must give up, not queue forever
        t0 = time.monotonic()
        result = op.send(b"second", budget_ms=150)
        elapsed_ms = (time.monotonic() - t0) * 1000
        assert result is False, "no free instance while overlay is busy -> False, not a block"
        assert elapsed_ms < 150 + SLACK_MS, f"relay overran budget during slow overlay: {elapsed_ms:.0f} ms"
        release.set()
        time.sleep(0.3)                     # let the overlay recover and reopen the instance
        assert op.send(b"third", budget_ms=2000), "overlay must still be healthy afterwards"
        deadline = time.monotonic() + 3
        while len(got) < 2 and time.monotonic() < deadline:
            time.sleep(0.05)
        assert got[0] == b"first" and b"third" in got, got
    finally:
        release.set()
        stop.set()
        t.join(timeout=3)
    assert not t.is_alive()


def test_send_returns_bool_never_raises_for_edge_inputs():
    # no server, zero budget, oversized, empty payload - all must be plain bools
    assert op.send(b"", budget_ms=0) is False
    assert op.send(b"nobody-listening", budget_ms=1) is False
    assert op.send(b"x" * (op.MAX_MSG + 1), budget_ms=0) is False


def test_concurrent_sends_all_return_within_budget_and_none_hang():
    stop, got = threading.Event(), []
    t = _serve_thread(got, stop, max_msgs=8)
    results, errors = [], []

    def worker(i):
        try:
            results.append(op.send(f"msg-{i}".encode(), budget_ms=3000))
        except Exception as exc:            # contract: never raises
            errors.append(exc)

    try:
        t0 = time.monotonic()
        threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
        for th in threads:
            th.start()
        for th in threads:
            th.join(timeout=10)
            assert not th.is_alive(), "a relay caller hung past its join"
        wall_ms = (time.monotonic() - t0) * 1000
        assert errors == [], errors
        assert results == [True] * 8, results
        assert wall_ms < 10 * 1000
        deadline = time.monotonic() + 3
        while len(got) < 8 and time.monotonic() < deadline:
            time.sleep(0.05)
        assert sorted(got) == sorted(f"msg-{i}".encode() for i in range(8)), got
    finally:
        stop.set()
        t.join(timeout=3)
    assert not t.is_alive()
