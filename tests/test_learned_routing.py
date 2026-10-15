"""D4 routing order: a learned correction must be consulted BEFORE route(), and a "learned"
turn must skip the brain model call.

`corrections.learned()` returns the skill the owner already corrected for this request; the
dispatcher must run it directly instead of letting the router (or the planner model) pick
something else. This pins the wiring end to end: the learned skill runs, the router's pick
never does, and the brain gate treats source="learned" like an explicit directive.

Run:  venv\\Scripts\\python -m pytest tests/test_learned_routing.py -q
"""
import os
import sys
import tempfile
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.environ.setdefault("ISHTI_STATE_DB", os.path.join(tempfile.mkdtemp(prefix="ishti_learned_"), "state.db"))

from orchestrator.memory.user_profile import scratch_memory  # noqa: E402
import ishti.facts as facts_mod  # noqa: E402
import ishti.dispatcher as D  # noqa: E402
from ishti import corrections  # noqa: E402

pytestmark = pytest.mark.integration


class FakeSkill:
    def __init__(self, name):
        self.name = name
        self.status = "working"
        self.manifest = {"description": "fake", "tier": "T0", "priority": 50, "route": "fake"}
        self.runs = []

    def matches(self, text):
        return False

    def run(self, text, s):
        self.runs.append(text)
        return {"reply": f"{self.name} ran", "sources": []}


class Conv:
    active_session_id = None

    def add_message(self, *a, **k):
        return {"id": "m1"}

    def get_messages(self, *a, **k):
        return []


def test_learned_skill_beats_the_router(monkeypatch):
    alpha, beta = FakeSkill("alpha"), FakeSkill("beta")

    def routing(text, skills, exclude=(), **kw):
        return {"skill": "beta", "confidence": 1.0, "source": "rule", "scores": [("beta", 1.0)]}

    for name in ("_remember", "_learn", "_learn_turn", "_memory_extract", "_ftlog"):
        monkeypatch.setattr(D, name, lambda *a, **k: None)
    monkeypatch.setattr(D, "SKILLS", {"alpha": alpha, "beta": beta})
    monkeypatch.setattr(D, "route", routing)
    monkeypatch.setattr(corrections, "learned", lambda text: "alpha")

    def turn():
        shared = {"id": "learned-1", "history": [], "pending": None, "last_entity": None}
        events = []
        view = D.View(shared, trace=lambda **kw: events.append(kw), on_token=None)
        return D._handle("please open the imsciences portal and check my attendance",
                         "learned-1", Conv(), view, events, time.perf_counter(), "text")

    with scratch_memory():
        db = os.path.join(tempfile.mkdtemp(prefix="ishti_facts_"), "facts.db")
        saved, facts_mod._facts = facts_mod._facts, facts_mod.Facts(db)
        try:
            res = turn()
        finally:
            facts_mod._facts = saved

    assert res["skill"] == "alpha" and res["route"]["source"] == "learned", res["route"]
    assert alpha.runs and not beta.runs, (alpha.runs, beta.runs)


def test_learned_is_in_the_brain_skip_tuple():
    """Static pin: source="learned" must keep the turn out of the planner model call."""
    src = open(os.path.join(ROOT, "ishti", "dispatcher.py"), encoding="utf-8").read()
    line = [ln for ln in src.splitlines() if "explicit_directive" in ln and '"learned"' in ln]
    assert line, "the brain-skip source tuple must include 'learned'"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
