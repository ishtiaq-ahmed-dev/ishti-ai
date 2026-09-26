"""Web search skill: wraps the existing, live-verified quick_search pipeline."""
from ishti.skills import ground_dates, trace
from orchestrator.intent_detector import IntentDetector

_engine = None


import re

# Facts about products, prices and releases live on the web, not in the model's memory.
PRODUCT = re.compile(r"\b(?:price|prices|cost|costs|how\s+much\s+is|specs?|specifications|review|reviews|"
                     r"benchmark|benchmarks|release\s+date|launch\s+date|vs\.?|versus|compare)\b", re.I)
MINE = re.compile(r"\b(?:my|this)\s+(?:pc|computer|laptop|system|machine)\b", re.I)


# "search for the physics teacher": a web search unless it's about the owner's own files/PC ("search on pc for x.md")
SEARCH = re.compile(r"^\s*(?:please\s+)?(?:search|look\s+up|google|find\s+out)\b", re.I)
LOCAL = re.compile(r"\b(?:on\s+(?:my\s+)?(?:pc|computer|laptop|drive)|files?|folders?|pdfs?|documents?|downloads|desktop|"
                   r"\.[a-z0-9]{2,4}\b)", re.I)


def match(text):
    return (IntentDetector.classify(text)["mode"] == "QUICK_SEARCH" or (bool(PRODUCT.search(text)) and not MINE.search(text))
            or (bool(SEARCH.search(text)) and not LOCAL.search(text)))


# A definite reference the owner hasn't identified: "find the physics teacher", "call my manager's office".
VAGUE = re.compile(r"\b(?:the|my|our|that|this)\s+(?:\w+\s+){0,2}(?:teacher|professor|lecturer|sir|madam|miss|doctor|"
                   r"manager|boss|principal|director|office|branch|campus|shop|store|restaurant|hospital|clinic|school|"
                   r"department|class|contact|number|address|guy|person|dealer|agent)s?\b", re.I)


def ambiguous(text):
    """One short clarifying question when a search would have to guess which person/place is meant, else None."""
    if not VAGUE.search(text):
        return None
    from ishti import llm
    from ishti.facts import get_facts
    import json
    known = "; ".join(f"{a}: {v}" for a, v in get_facts().get("owner").items() if a != "entity" and not a.startswith("prefers_"))
    raw = llm.ask(f"What we know about the owner: {known}\nThe owner asks: {text}\n\nCould a web search answer this "
                  "without guessing which specific person, place or organisation is meant? Only if several quite "
                  "different targets are plausible AND the facts above don't settle it, it is ambiguous. Return JSON "
                  '{"ambiguous": true/false, "question": "<one short clarifying question, or empty>"}.',
                  system=None, temperature=0, max_tokens=60, response_format={"type": "json_object"})
    try:
        d = json.loads(raw)
    except ValueError:
        return None
    return str(d.get("question") or "").strip() or None if d.get("ambiguous") else None


def run(text, session):
    import copy
    global _engine
    pend = session.get("pending")
    if pend and pend.get("skill") == "web_search":  # the answer to our clarifying question: same task, now specific
        session["pending"] = None
        text = f"{pend['request']} ({text.strip()})"
        trace(session, "clarified", f"searching for: {text}")
    elif question := ambiguous(text):
        session["pending"] = {"skill": "web_search", "request": text}
        trace(session, "ambiguous", "which one is meant isn't clear - asking before searching")
        return {"reply": question, "sources": [], "no_escalate": True}
    if _engine is None:
        from ishti.dispatcher import HOOKS
        from orchestrator.deep_research import DeepResearchEngine
        _engine = DeepResearchEngine(event_callback=HOOKS["emit"])
    # a shallow copy per request: its own progress callback (parallel searches must not narrate into each
    # other's Thinking panel), the shared search/fetch/model clients
    engine = copy.copy(_engine)
    engine.event_callback = lambda kind, data: trace(session, kind, (data or {}).get("detail") or kind.replace("_", " "),
                                                     **{k: v for k, v in (data or {}).items() if k in ("queries", "state")})
    # "Hi, how are you ... can you search X": greetings and filler aren't search terms (and "today" in
    # "how can I help you today" became a date)
    from ishti.planner import CLAUSE, smalltalk
    kept = [c for c in CLAUSE.split(text) if c and not smalltalk(c)]
    text = re.sub(r"^\s*(?:okay|ok|so)?[,\s]*(?:can|could)\s+you\s+(?:please\s+)?(?:search\s+(?:on\s+)?(?:the\s+)?(?:website|web|internet|google)\s*(?:for|what)?\s*)?",
                  "", " ".join(kept) or text, flags=re.I).strip() or text
    query = ground_dates(text)
    if query != text:
        trace(session, "dates", f"search for: {query}")
    res = engine.quick_search(query, depth=IntentDetector.classify(text).get("depth", "SIMPLE"))
    # "no confident match" after the rewording retries is final: escalating would just loop again. A confident answer
    # was written only from pages that passed the per-page check, so the dispatcher's second model check (which
    # misjudged a correct answer and cost a 25 s deep-research detour) is skipped unless the text itself gives up.
    from ishti.dispatcher import FELL_SHORT
    answer = res.get("answer", "")
    return {"reply": answer, "sources": res.get("sources", []), "verified": res.get("verified", False),
            "no_escalate": res.get("confident") is False or not FELL_SHORT.search(answer)}
