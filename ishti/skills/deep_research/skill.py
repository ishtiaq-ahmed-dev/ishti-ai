"""Deep research skill: the existing multi-query research engine, as its own skill."""
from ishti.skills import trace
from orchestrator.intent_detector import IntentDetector

_engine = None


def match(text):
    return IntentDetector.classify(text)["mode"] == "DEEP_RESEARCH"


def run(text, session):
    global _engine
    from ishti.dispatcher import HOOKS
    if _engine is None:
        from orchestrator.deep_research import DeepResearchEngine
        _engine = DeepResearchEngine(event_callback=HOOKS["emit"])
    if HOOKS["emit"]:
        HOOKS["emit"]("view_navigate", {"target_view": "RESEARCH", "topic": text})  # HUD research view
    # the engine's own status events become Thinking-panel steps for this request
    _engine.event_callback = lambda kind, data: trace(session, kind, (data or {}).get("detail") or kind.replace("_", " "),
                                                    **{k: v for k, v in (data or {}).items() if k in ("queries", "state")})
    res = _engine.run_deep_research(text)
    return {"reply": res.get("report", ""), "sources": res.get("sources", []), "queries": res.get("queries", []),
            "verified": res.get("verified", False), "mode": "DEEP_RESEARCH"}
