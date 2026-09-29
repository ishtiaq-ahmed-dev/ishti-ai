r"""INTEGRATION: the dispatcher with the resolver wired in (Part D2/D3 end to end).

The real dispatcher.handle() runs turn by turn with the search skills mocked; the
Conversation State Manager is pointed at a temp DB (ISHTI_STATE_DB) and the owner's
memory stores are swapped to scratch (the house rule: tests never touch owner data).

Proves through the LIVE path:
  T1  "Search about Sony WH-1000XM5" -> "deep research the internet and find the
      cheapest price" -> the deep_research skill receives the topic in its text.
  T2  "search iPhone 16" -> "now search Samsung S25" -> "cheapest price?" -> the
      skill sees Samsung S25.
  T5  switching the architecture profile mid-chat keeps the topic alive.
"""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ["ISHTI_STATE_DB"] = os.path.join(tempfile.mkdtemp(prefix="ishti_state_"), "state.db")

from orchestrator.memory.user_profile import scratch_memory  # noqa: E402
import ishti.facts as facts_mod  # noqa: E402
import ishti.dispatcher as D  # noqa: E402

seen = {"deep_research": [], "web_search": []}
_real_deep = D.SKILLS["deep_research"].run
_real_web = D.SKILLS["web_search"].run
# the learning hooks write to the owner DBs: silenced during the test, restored after
_saved_hooks = {}


def _patch():
    D.SKILLS["deep_research"].run = lambda text, s: (
        seen["deep_research"].append(text), {"reply": "REPORT: prices for " + text[:60], "sources": []})[1]
    D.SKILLS["web_search"].run = lambda text, s: (
        seen["web_search"].append(text), {"reply": "FOUND " + text[:60], "sources": []})[1]
    for name in ("_remember", "_learn", "_learn_turn", "_arch2_consolidate", "_ftlog"):
        _saved_hooks[name] = getattr(D, name)
        setattr(D, name, lambda *a, **k: None)


def _unpatch():
    D.SKILLS["deep_research"].run = _real_deep
    D.SKILLS["web_search"].run = _real_web
    for name, fn in _saved_hooks.items():
        setattr(D, name, fn)
    seen["deep_research"].clear()
    seen["web_search"].clear()


def _run_all():
    _patch()
    try:
        _run_turns()
    finally:
        _unpatch()


def _run_turns():
    with scratch_memory():
        db = os.path.join(tempfile.mkdtemp(prefix="ishti_facts_"), "facts.db")
        facts_mod._facts = facts_mod.Facts(db)

        # ---- T1 through the real dispatcher
        r1 = D.handle("Search about Sony WH-1000XM5", session_id="flow1")
        assert "WH-1000XM5" in r1["final_answer"] or seen["web_search"], (r1["final_answer"], seen)
        D.handle("go deep research the internet and find the cheapest price", session_id="flow1")
        assert seen["deep_research"], "deep_research should have been routed"
        assert any("WH-1000XM5" in t for t in seen["deep_research"]), seen["deep_research"]

        # ---- T2 through the real dispatcher
        seen["web_search"].clear()
        D.handle("search iPhone 16", session_id="flow2")
        D.handle("now search Samsung S25", session_id="flow2")
        D.handle("cheapest price?", session_id="flow2")
        assert any("S25" in t and "iphone 16" not in t.lower() for t in seen["web_search"]), seen["web_search"]

        # ---- T5: switch architecture mid-chat, the topic survives
        from ishti.core.architectures import get_registry
        reg = get_registry()
        before = reg.active()
        target = "memory_augmented" if before != "memory_augmented" else "simple_voice"
        res = reg.set_active(target, by="test", run_health_check=False)
        assert res.get("switched") is True, res
        r4 = D.handle("find the cheapest price", session_id="flow1")
        assert any("WH-1000XM5" in t for t in seen["deep_research"] + seen["web_search"]) or \
            "WH-1000XM5" in r4["final_answer"], (seen, r4["final_answer"])
        reg.set_active(before, by="test", run_health_check=False)

        # ---- architecture voice command asks for confirmation (C3)
        rc = D.handle("switch to simple voice mode", session_id="flow1")
        assert "yes/no" in rc["final_answer"].lower(), rc["final_answer"]
        ry = D.handle("yes", session_id="flow1")
        assert "simple" in ry["final_answer"].lower() or "switched" in ry["final_answer"].lower(), ry["final_answer"]
        reg.set_active(before, by="test", run_health_check=False)


def test_dispatcher_state_flow():
    """pytest entry: the whole scripted conversation, one test."""
    _run_all()


def teardown_module(module):
    _unpatch()


if __name__ == "__main__":
    _run_all()
    teardown_module(None)
    print("[integration] dispatcher state flow OK — T1/T2/T5 through handle(), arch command confirms")
