"""Web search skill: wraps the existing, live-verified quick_search pipeline."""
from orchestrator.intent_detector import IntentDetector

_engine = None


def match(text):
    return IntentDetector.classify(text)["mode"] == "QUICK_SEARCH"


def run(text, session):
    global _engine
    if _engine is None:
        from ishti.dispatcher import HOOKS
        from orchestrator.deep_research import DeepResearchEngine
        _engine = DeepResearchEngine(event_callback=HOOKS["emit"])
    res = _engine.quick_search(text, depth=IntentDetector.classify(text).get("depth", "SIMPLE"))
    return {"reply": res.get("answer", ""), "sources": res.get("sources", []), "verified": res.get("verified", False)}
