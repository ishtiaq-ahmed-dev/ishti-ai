"""PERF-7/8 regression: every call a request makes is in its ledger, a simple request makes exactly the calls it needs,
a skill call without a route reason is flagged, English replies are not "translated" into English, and a repeated
embedding is not recomputed.

Offline: skills and the model layer are stubbed (same house style as test_dispatcher_blank_answer.py).
"""
import os
import sys
import tempfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.environ.setdefault("ISHTI_STATE_DB", os.path.join(tempfile.mkdtemp(prefix="ishti_calls_"), "state.db"))

from orchestrator.memory.user_profile import scratch_memory  # noqa: E402
import ishti.facts as facts_mod  # noqa: E402
import ishti.dispatcher as D  # noqa: E402
from ishti import calltrace  # noqa: E402

pytestmark = pytest.mark.integration


def _isolate(monkeypatch):
    for name in ("_remember", "_learn", "_learn_turn", "_arch2_consolidate", "_ftlog"):
        monkeypatch.setattr(D, name, lambda *a, **k: None)
    monkeypatch.setattr(D.planner, "candidate", lambda *a, **k: False)
    monkeypatch.setattr(D, "preferred_language", lambda: "English")   # the owner's language lock: every reply is checked against it
    monkeypatch.setattr(D, "translate", lambda *a, **k: pytest.fail("an English reply must not go through the translator"))


def _handle(text, sid):
    with scratch_memory():
        db = os.path.join(tempfile.mkdtemp(prefix="ishti_facts_"), "facts.db")
        saved, facts_mod._facts = facts_mod._facts, facts_mod.Facts(db)
        try:
            return D.handle(text, session_id=sid)
        finally:
            facts_mod._facts = saved


def test_a_one_skill_request_makes_exactly_one_call(monkeypatch):
    _isolate(monkeypatch)
    monkeypatch.setattr(D, "route", lambda text, skills, exclude=None: {"skill": "system", "confidence": 1.0, "source": "rule", "scores": []})
    monkeypatch.setattr(D.SKILLS["system"], "run", lambda text, s: {"reply": "The CPU is at 3 percent and RAM is fine.", "sources": [], "no_escalate": True})
    res = _handle("how is my pc", "calls1")
    c = res["calls"]
    assert (c["skill_calls"], c["llm_calls"], c["embed_calls"], c["net_calls"]) == (1, 0, 0, 0), c
    assert c["flags"] == [] and c["calls"][0]["why"], "the skill call must carry its plan reason"


def test_a_skill_call_without_a_route_reason_is_flagged(monkeypatch):
    _isolate(monkeypatch)
    monkeypatch.setattr(D, "route", lambda text, skills, exclude=None: {"skill": "system", "confidence": 1.0, "scores": []})   # no "source"
    monkeypatch.setattr(D.SKILLS["system"], "run", lambda text, s: {"reply": "All fine here, nothing to report.", "sources": [], "no_escalate": True})
    res = _handle("how is my pc", "calls2")
    assert res["calls"]["flags"] and "no route reason" in res["calls"]["flags"][0], res["calls"]


def test_llm_calls_are_in_the_ledger(monkeypatch):
    _isolate(monkeypatch)
    from ishti import llm
    from types import SimpleNamespace
    monkeypatch.setattr(llm, "_chat", lambda *a, **k: SimpleNamespace(content="ok", tool_calls=None))
    monkeypatch.setattr(llm, "model_for", lambda role: f"stub-{role}")

    def run(text, s):
        return {"reply": llm.chat([{"role": "user", "content": text}], role="planner").content + " and that is all of it.", "sources": [], "no_escalate": True}
    monkeypatch.setattr(D, "route", lambda text, skills, exclude=None: {"skill": "chat", "confidence": 1.0, "source": "rule", "scores": []})
    monkeypatch.setattr(D.SKILLS["chat"], "run", run)
    c = _handle("tell me about quokkas please", "calls3")["calls"]
    llm_call = next(x for x in c["calls"] if x["kind"] == "llm")   # recorded when it finishes, i.e. before the skill that made it
    assert c["llm_calls"] == 1 and c["skill_calls"] == 1 and llm_call["model"] == "stub-planner", c


def test_english_is_not_translated_but_other_languages_are():
    assert D.already_in("7 plus 5 equals 12.", "English")
    assert D.already_in("12", "English") and D.already_in("Done.", "English")
    assert D.already_in("The CPU is at 3 percent and the memory is fine.", "English")
    assert not D.already_in("Bonjour, je m'appelle Pierre et je suis ici pour vous aider aujourd'hui.", "English")
    assert not D.already_in("Ich habe heute keine Zeit für das Treffen mit dir.", "English")
    assert not D.already_in("آج موسم اچھا ہے", "English")
    assert not D.already_in("7 plus 5 equals 12.", "Roman Urdu (Urdu written in English letters)")   # the other locks are unchanged


def test_a_repeated_embedding_is_not_recomputed(monkeypatch):
    from ishti import semantic
    semantic._EMBED_CACHE.clear()
    calls = []

    class R:
        def raise_for_status(self):
            pass

        def json(self):
            return {"embeddings": [[0.1, 0.2]]}
    monkeypatch.setattr(semantic.keepalive, "post", lambda *a, **k: (calls.append(1), R())[1])
    monkeypatch.setattr("ishti.models.used", lambda *a, **k: None)
    calltrace.begin("t")
    assert semantic.embed(["search_query: hello"]) == semantic.embed(["search_query: hello"])
    assert len(calls) == 1
    summary = calltrace.end()
    assert summary["embed_calls"] == 2 and summary["calls"][1].get("cached") is True


def test_planner_prompt_lists_only_relevant_skills():
    """PERF-8: the multi-task planning prompt carried all 124 skill descriptions (~3,850 tokens) on every request."""
    from ishti import planner
    total = len(D.SKILLS)
    for text, must in (("track subscription netflix 1500 monthly and then show my subscriptions", {"subscriptions"}),
                       ("open whatsapp and send hi to ali, then tell me my notes", {"whatsapp", "notes"}),
                       ("check the weather and edit the latest video in downloads", {"weather", "video"})):
        keep = planner.shortlist(text, D.SKILLS)
        assert keep is not None and must <= keep, (text, keep)
        assert len(keep) <= 20 and len(keep) < total / 4, f"{len(keep)} of {total} skills would still be listed"


def test_boot_warm_up_makes_no_speculative_network_call():
    """The no-cloud gate (tools/gate_no_cloud.py) caught warm_up() fetching the KSE-100 list from dps.psx.com.pk at every boot."""
    import inspect
    assert "psx_symbols" not in inspect.getsource(D.warm_up)


def test_clock_questions_are_answered_by_the_reflex_not_the_web():
    """"what time is it right now" used to miss the reflex and went to web search (126 s measured in the arch comparison);
    inside a multi-task plan the router sent "what time is it" to the weather skill."""
    from ishti import reflex
    for q in ("what time is it", "what time is it right now", "what is the current time", "what's the time now"):
        assert reflex.pure(q) == "time", q
    for q in ("what time is it in tokyo", "volume up", "what time is my exam"):
        assert reflex.pure(q) is None, q
