"""A scheduled task that runs long after its time says so (an 08:00 brief at 18:00 is a catch-up); one on time doesn't.
Uses the per-test scratch facts store (conftest), no model."""
import threading
import time

from ishti.facts import get_facts
from ishti.skills.reminders import skill as reminders


def _fire(due):
    reminders._db()
    f = get_facts()
    with f.lock, f.db:
        f.db.execute("INSERT INTO reminders(due, text, created, repeat, task) VALUES(?,?,?,?,?)",
                     (due, "give me my brief", time.time(), "daily", 1))
    heard, done = [], threading.Event()
    reminders.fire_due(lambda m: (heard.append(m), done.set()), now=time.time(), run_task=lambda t: "BRIEF")
    assert done.wait(5)
    return heard[0]


def test_late_task_is_labelled_catch_up():
    assert _fire(time.time() - 10 * 3600).startswith("(Catch-up - this was due at ")


def test_on_time_task_is_not():
    assert _fire(time.time() - 30) == "BRIEF"
