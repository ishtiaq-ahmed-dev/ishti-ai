"""Web search skill: wraps the existing, live-verified quick_search pipeline."""
from ishti.skills import trace
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
    # the engine's own status events become Thinking-panel steps for this request
    _engine.event_callback = lambda kind, data: trace(session, kind, (data or {}).get("detail") or kind.replace("_", " "),
                                                    **{k: v for k, v in (data or {}).items() if k in ("queries", "state")})
    res = _engine.quick_search(text, depth=IntentDetector.classify(text).get("depth", "SIMPLE"))
    return {"reply": res.get("answer", ""), "sources": res.get("sources", []), "verified": res.get("verified", False)}
