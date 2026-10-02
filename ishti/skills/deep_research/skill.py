"""Deep research skill: the existing multi-query research engine, as its own skill."""
from ishti.skills import ground_dates, trace
from orchestrator.intent_detector import IntentDetector

try:  # best-effort failure log (never breaks the module if logging is unavailable)
    from ishti.core.logutil import swallowed
except Exception:  # pragma: no cover
    def swallowed(*_a, **_k):
        return None


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
    # F1/F2: fact-check the report's checkable claims against independent sources and append
    # the verdict block — conflicts are flagged, never silently kept.
    try:
        import orchestrator.factcheck as F
        fc = F.check(res.get("report", ""), max_claims=3)
        if fc["claims"]:
            res["report"] += "\n\n---\n" + F.format_report(fc)
            res["factcheck"] = {"supported": fc["supported"], "contradicted": fc["contradicted"],
                                "unclear": fc["unclear"]}
            trace(session, "factcheck", f"{fc['supported']} supported, {fc['contradicted']} contradicted, "
                                        f"{fc['unclear']} unclear", status="done")
    except Exception as exc:
        trace(session, "factcheck", f"skipped: {type(exc).__name__}", status="error")
    # D1: record the research (topic + sources) so "go deeper" / "find the cheapest one"
    # resolves against THIS topic in the same chat.
    try:
        from ishti.core import state as cstate
        cstate.get_manager().record_search(session.get("id", "default"), text,
                                           [s.get("url", "") for s in res.get("sources", []) if isinstance(s, dict)])
    except Exception as _exc:
        swallowed('ishti/skills/deep_research/skill.py:run', _exc)
    report = res.get("report", "")
    if not (report or "").strip():  # a skill must never answer with blank text
        report = ("I could not put together a usable answer from the web for that. Please try rephrasing it, "
                  "or ask again in a moment.")
    if session.get("route", {}).get("source") == "escalation":
        # used as a fallback for a plain question: the owner asked for an answer, not a report
        from ishti import llm
        trace(session, "answer", "condense the research into a direct answer", status="start")
        condensed = llm.ask(f"Question: {text}\n\nResearch notes (with [n] citations):\n{report[:5000]}\n\n"
                            "Answer the question directly in one or two sentences, keeping the [n] citations you rely on. "
                            "Prefer the most recent information.", temperature=0.1, max_tokens=200)
        # a failed/empty condense must never blank a report we already have (B1 class: a cleanup step
        # silently emptying a good answer). Keep the report when the condenser gives nothing.
        if condensed and condensed.strip():
            report = condensed
        else:
            trace(session, "answer", "condense returned nothing - keeping the full report", status="error")
        trace(session, "answer", "condensed", status="done")
    return {"reply": report, "sources": res.get("sources", []), "queries": res.get("queries", []),
            "verified": res.get("verified", False), "mode": "DEEP_RESEARCH"}


if __name__ == "__main__":
    assert match("deep research the effect of sleep on memory")
    assert not match("what time is it")
    print("[self-check] deep_research OK - routing")
