"""Web search skill: wraps the existing, live-verified quick_search pipeline."""
from ishti.skills import ground_dates, trace
from orchestrator.intent_detector import IntentDetector

_engine = None


import re

# Facts about products, prices and releases live on the web, not in the model's memory.
PRODUCT = re.compile(r"\b(?:price|prices|cost|costs|how\s+much\s+is|specs?|specifications|review|reviews|"
                     r"benchmark|benchmarks|release\s+date|launch\s+date|vs\.?|versus|compare)\b", re.I)
MINE = re.compile(r"\b(?:my|this)\s+(?:pc|computer|laptop|system|machine)\b", re.I)


def match(text):
    return IntentDetector.classify(text)["mode"] == "QUICK_SEARCH" or (bool(PRODUCT.search(text)) and not MINE.search(text))


def run(text, session):
    global _engine
    if _engine is None:
        from ishti.dispatcher import HOOKS
        from orchestrator.deep_research import DeepResearchEngine
        _engine = DeepResearchEngine(event_callback=HOOKS["emit"])
    # the engine's own status events become Thinking-panel steps for this request
    _engine.event_callback = lambda kind, data: trace(session, kind, (data or {}).get("detail") or kind.replace("_", " "),
                                                    **{k: v for k, v in (data or {}).items() if k in ("queries", "state")})
    query = ground_dates(text)
    if query != text:
        trace(session, "dates", f"search for: {query}")
    res = _engine.quick_search(query, depth=IntentDetector.classify(text).get("depth", "SIMPLE"))
    return {"reply": res.get("answer", ""), "sources": res.get("sources", []), "verified": res.get("verified", False)}
