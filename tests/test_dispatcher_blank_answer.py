"""SWEEP-13 regression (2026-10-01 sweep): a blank reply is never an answer.

Live symptom: web_search handed back an honest "I couldn't find ..." reply, the
dispatcher escalated to deep_research exactly as designed - and the blank fallback
reply replaced the real one, so the owner saw an empty bubble.

Root cause: `fell_short("")` was False, i.e. a skill that answered NOTHING was
scored as a better answer than one that answered honestly-but-imperfectly. Two
layers now hold:

  * fell_short(): a blank (or missing) reply counts as falling short, so a blank
    fallback can never win the escalation slot;
  * _finish(): if a reply is still blank, the dispatcher says so - final_answer
    is never empty, and a skill result without a "reply" key no longer crashes
    the close-out (the old `out["reply"] += ...` raised KeyError).

Run:  venv\\Scripts\\python -m pytest tests/test_dispatcher_blank_answer.py -q
"""
import os
import sys
import tempfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.environ.setdefault("ISHTI_STATE_DB", os.path.join(tempfile.mkdtemp(prefix="ishti_blank_"), "state.db"))

from orchestrator.memory.user_profile import scratch_memory  # noqa: E402
import ishti.facts as facts_mod  # noqa: E402
import ishti.dispatcher as D  # noqa: E402

pytestmark = pytest.mark.integration

BLANK_FALLBACK = ("I couldn't put a usable answer together for that. Please try rephrasing it, "
                  "or ask again in a moment.")


def _stub_route(skill):
    return lambda text, skills, exclude=None: {"skill": skill, "confidence": 1.0, "source": "test", "scores": []}


def _isolate(monkeypatch):
    """No background learning, no planner, no owner stores - the followups/state-flow house rule."""
    for name in ("_remember", "_learn", "_learn_turn", "_arch2_consolidate", "_ftlog"):
        monkeypatch.setattr(D, name, lambda *a, **k: None)
    monkeypatch.setattr(D.planner, "candidate", lambda *a, **k: False)


def _with_scratch_facts(fn):
    with scratch_memory():
        db = os.path.join(tempfile.mkdtemp(prefix="ishti_facts_"), "facts.db")
        saved, facts_mod._facts = facts_mod._facts, facts_mod.Facts(db)
        try:
            return fn()
        finally:
            facts_mod._facts = saved


def test_blank_and_missing_replies_count_as_falling_short():
    assert D.fell_short("what is the price", "", "deep_research", {}) is True
    assert D.fell_short("what is the price", "   \n ", "chat", {}) is True
    assert D.fell_short("what is the price", None, "deep_research", {}) is True  # never crash on None
    assert D.fell_short("what is the price", "It costs $499.", "deep_research", {}) is False


def test_blank_fallback_never_replaces_a_real_answer(monkeypatch):
    """web_search's honest miss must survive a blank deep_research fallback (the live bug)."""
    _isolate(monkeypatch)
    monkeypatch.setattr(D, "route", _stub_route("web_search"))
    seen = []
    monkeypatch.setattr(D.SKILLS["web_search"], "run",
                        lambda text, s: {"reply": "I couldn't find a live answer for that.", "sources": []})
    monkeypatch.setattr(D.SKILLS["deep_research"], "run",
                        lambda text, s: (seen.append(text), {"reply": "  ", "sources": []})[1])  # blank fallback

    res = _with_scratch_facts(lambda: D.handle("price of a niche part", session_id="blank1"))
    assert seen, "the escalation to deep_research must have run (that is the bug path)"
    assert res["skill"] == "web_search", res  # the blank fallback must not take the slot
    assert "couldn't find" in res["final_answer"], res


def test_final_answer_is_never_blank(monkeypatch):
    """A lone skill that answers nothing still gets an honest, non-empty final answer."""
    _isolate(monkeypatch)
    monkeypatch.setattr(D, "route", _stub_route("chat"))
    monkeypatch.setattr(D.SKILLS["chat"], "run",
                        lambda text, s: {"reply": "", "sources": [], "no_escalate": True})

    res = _with_scratch_facts(lambda: D.handle("say something", session_id="blank2"))
    assert res["final_answer"].strip(), res
    assert res["final_answer"] == BLANK_FALLBACK, res


def test_skill_result_without_a_reply_key_is_handled(monkeypatch):
    """A reply-less dict used to KeyError at close-out (out["reply"] += ...); now it gets the net."""
    _isolate(monkeypatch)
    monkeypatch.setattr(D, "route", _stub_route("chat"))
    monkeypatch.setattr(D.SKILLS["chat"], "run",
                        lambda text, s: {"sources": [], "no_escalate": True})  # no "reply" key at all

    res = _with_scratch_facts(lambda: D.handle("say something", session_id="blank3"))
    assert res["mode"] != "ERROR", res  # the request must not fail as a dispatcher error
    assert res["final_answer"].strip(), res
