"""Deep research skill: the existing multi-query research engine, as its own skill."""
from ishti.skills import ground_dates, trace
from orchestrator.intent_detector import IntentDetector

_engine = None


def match(text):
    from ishti.skills.markets.skill import INVEST  # "research nvidia, how much should I buy" is a markets decision
    return IntentDetector.classify(text)["mode"] == "DEEP_RESEARCH" and not INVEST.search(text)


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
    fallback = session.get("route", {}).get("source") == "escalation"
    # as a fallback for a plain question: 2 focused queries, not a 4-angle investigation
    res = _engine.run_deep_research(ground_dates(text), max_queries=2 if fallback else 4)
    report = res.get("report", "")
    if session.get("route", {}).get("source") == "escalation":
        # used as a fallback for a plain question: the owner asked for an answer, not a report
        from ishti import llm
        trace(session, "answer", "condense the research into a direct answer", status="start")
        report = llm.ask(f"Question: {text}\n\nResearch notes (with [n] citations):\n{report[:5000]}\n\n"
                         "Answer the question directly in one or two sentences, keeping the [n] citations you rely on. "
                         "Prefer the most recent information.", temperature=0.1, max_tokens=200)
        trace(session, "answer", "condensed", status="done")
    return {"reply": report, "sources": res.get("sources", []), "queries": res.get("queries", []),
            "verified": res.get("verified", False), "mode": "DEEP_RESEARCH"}
