"""JEV-17 acceptance: spoken-duration parsing in the reminders skill (WP-JEV).

Carries the measured before/after: the same fixed 26-utterance set parsed by the pre-merge parser
(BEFORE: 10/26 correct = 38.5%, 2 wrong, 14 missed - raw output in _work/duration_before.json,
committed numbers in TEST_RESULTS.md) and by the merged parser (AFTER: 26/26 = 100%).

Run: venv\\Scripts\\python -m pytest tests/test_duration_parser.py -q
"""
import datetime as dt
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from ishti.skills.reminders import skill as R  # noqa: E402

M, H = 60, 3600
SET = [
    ("remind me in 20 minutes to call Ali", 20 * M),
    ("set a timer for half an hour", 30 * M),
    ("remind me in an hour to check the oven", 60 * M),
    ("set a timer for 90 seconds", 90),
    ("remind me in 10 mins to flip the chicken", 10 * M),
    ("remind me in 3 hours to move the car", 3 * H),
    ("set a timer for 2 sec", 2),
    ("remind me in 45 seconds to check the pasta", 45),
    ("set a timer for twenty five minutes", 25 * M),
    ("remind me in twenty minutes to stand up", 20 * M),
    ("set a timer for a couple of minutes", 2 * M),
    ("remind me in a few minutes to check the oven", 3 * M),
    ("remind me in an hour and a half to leave", 90 * M),
    ("set a timer for five minutes", 5 * M),
    ("remind me in ten minutes to reply", 10 * M),
    ("set a timer for fifteen minutes", 15 * M),
    ("remind me in thirty minutes to pause", 30 * M),
    ("set a timer for forty five seconds", 45),
    ("remind me in two hours to wrap up", 2 * H),
    ("set a timer for a minute", 60),
    ("remind me in half an hour to check the wash", 30 * M),
    ("set a timer for sixty seconds", 60),
    ("remind me in an hour 30 minutes to go", 90 * M),
    ("set a timer for 1 and a half hours", 90 * M),
    ("remind me in twenty minutes", 20 * M),
    ("set a timer for three quarters of an hour", 45 * M),
]
NOW = dt.datetime(2026, 10, 2, 12, 0)


@pytest.mark.parametrize("text, want", SET)
def test_fixed_set_parses_to_exact_duration(text, want):
    got = R.when(text, NOW)
    assert got is not None, f"unparsed: {text}"
    assert int((got - NOW).total_seconds()) == want, text


def test_at_forms_unchanged():
    """The new branch must not touch clock-time parsing (regression row)."""
    assert R.when("remind me at 5pm to stretch", NOW).hour == 17
    assert R.when("remind me tomorrow at 9 to pay rent", NOW) == dt.datetime(2026, 10, 3, 9, 0)
    assert R.when("remind me at 5 to stretch", NOW).hour == 17


def test_no_duration_falls_through():
    """No in/for/after clause -> None (caller falls back to AT then the LLM, exactly as before)."""
    assert R._duration_seconds("remind me to water the plants") is None
    assert R._duration_seconds("remind me for mum at 5pm") is None
    assert R.when("remind me to water the plants", NOW) is None


def test_corrections_are_not_hallucinated_quantities():
    """Phrases without a real quantity never invent a duration."""
    assert R._duration_seconds("set a timer after the movie starts") is None
    assert R._duration_seconds("remind me for the meeting") is None


def test_unit_variants():
    assert R._duration_seconds("in 1 hour") == 3600
    assert R._duration_seconds("for 2 hrs") == 7200
    assert R._duration_seconds("in 500 milliseconds") is None  # ms is not a unit: falls through honestly
    assert R._duration_seconds("in 3 days") == 3 * 86400
