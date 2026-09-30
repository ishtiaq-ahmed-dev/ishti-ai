"""FIX-12 regression: travel.run() must gate on match() and decline, never open the browser.

run() had no matcher gate: ANY input - including the empty edge-case row and the
prompt-injection row - drove the real Chrome profile to Google Travel and waited on results.
Unmatched requests must decline at the door; only matched flights/hotels rows reach the page.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def test_travel_declines_unmatched_shapes():
    from ishti.skills.travel import skill
    for row in ("", " ", "hello there", "what's the weather in Dubai", "book a flight to Dubai",
                "remind me about my flight", "x" * 500):
        out = skill.run(row, {})
        assert out is None or (isinstance(out, dict) and str(out.get("reply", "")).strip()), \
            f"travel.run({row[:20]!r}) must decline or reply honestly, got {out!r}"
        if isinstance(out, dict):
            # the honest decline names what it does accept - and never claims results
            low = str(out.get("reply", "")).lower()
            assert "flights" in low or "hotels" in low, f"decline should say what it does: {low[:120]}"


def test_travel_still_accepts_matched_rows():
    from ishti.skills.travel import skill
    for row in ("find flights from Peshawar to Dubai", "search hotels in Dubai from 5 to 8 October"):
        assert skill.match(row), f"matched row must still match: {row!r}"


def test_travel_run_off_switch_not_needed_for_decline():
    """The decline path returns without any browser call: fast (< 2 s), no Playwright involved."""
    import time
    from ishti.skills.travel import skill
    t0 = time.time()
    out = skill.run("", {})
    took = time.time() - t0
    assert out is None or isinstance(out, dict), out
    assert took < 2.0, f"decline took {took:.1f}s - it must not be waiting on a browser"
