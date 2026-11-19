"""Regressions from the live feature check of 2026-11-19 (real app, scratch data, local model)."""
import sys

import pytest


def test_delete_all_notes_asks_first():
    from ishti.skills.notes import skill as notes
    notes.run("note: buy lenses", {})
    s = {}
    out = notes.run("delete all my notes", s)
    assert "(yes/no)" in out["reply"] and "pending" in s
    assert "Deleted all" in s.pop("pending")["action"]()
    assert "no notes" in notes.run("delete all my notes", {})["reply"]


@pytest.mark.parametrize("text,day", [("kal ka mausam kaisa hoga peshawar mein", 1), ("parso ka mausam", 2),
                                      ("weather in Lahore", 0), ("kal mausam kaisa tha", 0)])
def test_roman_urdu_kal_is_tomorrow_in_a_forecast(text, day):
    from tools.web.weather import which_day
    assert which_day(text) == day


def test_remembered_fact_is_said_back_as_you():
    from ishti.memstore import consolidate
    assert consolidate.handle_command("remember that I like green tea", {}) == "Remembered: you like green tea."


def test_model_filler_opener_is_removed():
    from ishti import dispatcher
    out = {"reply": "Okay, here’s a summary of the sources you provided:\n\nPython 3.15 is out."}
    dispatcher._verify_reply("web_search", "summarize python.org", out, {"trace": lambda **k: None})
    assert out["reply"].startswith("Python 3.15 is out")


def test_percent_does_not_load_sympy():
    for mod in [m for m in sys.modules if m == "sympy" or m.startswith("sympy.")]:
        sys.modules.pop(mod)
    from ishti.skills.calc import skill as calc
    assert calc.run("what is 15% of 2400?", {})["reply"] == "15% of 2400 = 360"
    assert calc.run("gcd of 12 and 18", {})["reply"] == "GCD(12, 18) = 6"
    assert "sympy" not in sys.modules


@pytest.mark.parametrize("word,expect", [("no", "nothing is waiting"), ("yes", "nothing waiting for a yes")])
def test_bare_yes_or_no_with_nothing_pending(word, expect):
    from ishti import dispatcher, llm
    llm_calls = []
    orig = llm._chat
    llm._chat = lambda *a, **k: llm_calls.append(1) or (_ for _ in ()).throw(llm.ModelUnavailable("off"))
    try:
        out = dispatcher.handle(word, session_id=f"bare-{word}", channel="text")
    finally:
        llm._chat = orig
    assert expect in out["final_answer"] and not llm_calls


def test_cancel_my_reminder_is_still_a_request():
    from ishti import dispatcher, tool_routing
    assert tool_routing.route("cancel my 5pm reminder", dispatcher.SKILLS).get("skill") == "reminders"
