"""Web search skill: wraps the existing, live-verified quick_search pipeline."""
from ishti.skills import ground_dates, trace
from orchestrator.intent_detector import IntentDetector

_engine = None


import re

try:  # best-effort failure log (never breaks the module if logging is unavailable)
    from ishti.core.logutil import swallowed
except Exception:  # pragma: no cover
    def swallowed(*_a, **_k):
        return None


# Facts about products, prices and releases live on the web, not in the model's memory.
PRODUCT = re.compile(r"\b(?:price|prices|cost|costs|how\s+much\s+is|specs?|specifications|review|reviews|"
                     r"benchmark|benchmarks|release\s+date|launch\s+date|vs\.?|versus|compare)\b", re.I)
MINE = re.compile(r"\b(?:my|this)\s+(?:pc|computer|laptop|system|machine)\b", re.I)


# "search for the physics teacher": a web search unless it's about the owner's own files/PC ("search on pc for x.md")
SEARCH = re.compile(r"^\s*(?:please\s+)?(?:(?:now|then|next|and)\s+)?(?:search|look\s+up|google|find\s+out)\b", re.I)
LOCAL = re.compile(r"\b(?:on\s+(?:my\s+)?(?:pc|computer|laptop|drive)|files?|folders?|pdfs?|documents?|downloads|desktop|"
                   r"\.[a-z0-9]{2,4}\b)", re.I)
# Sending a person a message is not a search, whatever the intent classifier says. "Message all my IMS
# classmates about a study group" and "message him about meeting up" were both classified QUICK_SEARCH and
# routed to the web, which cannot possibly answer them (checklist 148, 150).
MESSAGE_PEOPLE = re.compile(
    r"^\s*(?:please\s+)?(?:message|msg|text|whatsapp|reply\s+to|write\s+to|ask|ping)\s+"
    r"(?:all\s+|every\s+|both\s+|the\s+|my\s+|his\s+|her\s+|them\s+|him\s+)?\w", re.I)
# the same verb can sit mid-sentence: "my friend from university, not the CardioSense one, message him
# about meeting up" was still classified QUICK_SEARCH and sent to the web.
MESSAGE_ANYWHERE = re.compile(
    r"\b(?:message|text|reply\s+to|write\s+to|whatsapp)\s+(?:him|her|them|us|all|everyone|every\s+\w+|"
    r"my\s+\w+|the\s+\w+)\b", re.I)
# "Are you running okay?" / "Is anything currently broken that you know of?": the intent classifier
# reads "currently" as a live factual query, but the answer is ISHTI's own health, which the system/meta
# skills report. Never a web search (checklist #188, #192).
SELF_STATUS = re.compile(
    r"\b(?:are\s+you\s+(?:running|ok|okay|fine|working|healthy|alright)|how\s+are\s+you\s+(?:doing|running)|"
    r"(?:is|anything)\s+anything?\s+(?:currently\s+|right\s+now\s+)?(?:broken|wrong|down|failing|offline)|"
    r"what(?:'s|\s+is)\s+(?:currently\s+)?(?:broken|wrong|down|failing)\b|anything\s+broken\b)", re.I)
# "Find a time this week that works for me and the JQ AI client": coordination with a real calendar,
# not a web fact. The intent classifier saw "this week" and called it a live query (checklist #153).
SCHEDULE_COORD = re.compile(
    r"\b(?:find|suggest|pick|propose|give\s+me)\s+(?:me\s+)?(?:a\s+|an\s+|some\s+)?"
    r"(?:time|slot|opening|free\s+slot)\b|\btime\s+(?:this|next)\s+week\s+that\s+works\b|"
    r"\bthat\s+works\s+for\s+(?:me|us|both|everyone)\b|\bfind\s+a\s+time\b", re.I)



# Clause split and filler test used to strip greetings from a search query ("Hi, how are you... can you search X").
_CLAUSE = re.compile(r"\s*(?:,?\s*(?:and\s+(?:also|then)|and|also|then|after\s+that|plus)|[;.!?]+(?:\s+|$))\s*", re.I)
_SMALLTALK = re.compile(r"^\W*(?:hi|hello|hey|thanks?|thank you|ok(?:ay)?|how are you|good (?:morning|evening|night)|salam|bye)", re.I)

def match(text):
    t = text.strip()
    if MESSAGE_PEOPLE.match(t) or MESSAGE_ANYWHERE.search(t):
        return False          # a person is being written to, not looked up
    if SELF_STATUS.search(t) or SCHEDULE_COORD.search(t):
        return False          # own health / calendar coordination, not a web fact
    # A style/behaviour instruction ("stop explaining your reasoning", "be more concise", "talk normal")
    # is a preference for how ISHTI answers, not a lookup. The classifier saw "result"/"every time" as a
    # live query and sent it to the web.
    if re.search(r"\b(?:stop\s+\w+ing\b|just\s+give\s+me\s+the\s+result|from\s+now\s+on|don'?t\s+explain|"
                 r"be\s+(?:more\s+)?(?:concise|brief|short|casual|formal|normal)|talk\s+normal|no\s+more\s+"
                 r"explanation|don'?t\s+over-?explain)\b", t, re.I):
        return False
    # "Deploy my latest commit" / "push my latest changes": a dev/git action on the owner's repo, not a
    # web fact. The classifier read "latest" as a live query (checklist #164).
    if re.search(r"\b(?:deploy|redeploy|release|ship|push|merge|revert|rollback|roll\s+back|commit|pull\s+request)\b", t, re.I) \
            and re.search(r"\b(?:commit|branch|repo|repository|changes?|code|build|deployment|production|staging|github|gitlab|origin)\b", t, re.I):
        return False
    # "Find 3 YouTube videos about local LLM fine-tuning" (checklist #42): a request to FIND links/results
    # about a topic is a web search, whatever the intent classifier says. The "find N <media> about X"
    # shape names a count and a topic - nothing on this PC can answer it.
    if re.search(r"\b(?:find|search|look\s+up|give\s+me|show\s+me)\s+(?:me\s+)?(?:\d+|three|five|ten|a\s+few)?\s*"
                 r".{0,24}?\b(?:videos?|articles?|results?|tutorials?|links?|posts?|examples?|papers?)\b", t, re.I):
        return True
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
    kept = [c for c in _CLAUSE.split(text) if c and not _SMALLTALK.match(c.strip())]
    text = re.sub(r"^\s*(?:okay|ok|so)?[,\s]*(?:can|could)\s+you\s+(?:please\s+)?(?:search\s+(?:on\s+)?(?:the\s+)?(?:website|web|internet|google)\s*(?:for|what)?\s*)?",
                  "", " ".join(kept) or text, flags=re.I).strip() or text
    query = ground_dates(text)
    if query != text:
        trace(session, "dates", f"search for: {query}")
    res = engine.quick_search(query, depth=IntentDetector.classify(text).get("depth", "SIMPLE"))
    # D1: remember the search (query + result URLs) in the Conversation State so later
    # "those results" / "find the cheapest one" resolves against THIS topic.
    try:
        from ishti.core import state as cstate
        cstate.get_manager().record_search(session.get("id", "default"), query,
                                           [s.get("url", "") for s in res.get("sources", []) if isinstance(s, dict)])
    except Exception as _exc:
        swallowed('ishti/skills/web_search/skill.py:run', _exc)
    # "no confident match" after the rewording retries is final: escalating would just loop again. A confident answer
    # was written only from pages that passed the per-page check, so the dispatcher's second model check (which
    # misjudged a correct answer and cost a 25 s deep-research detour) is skipped unless the text itself gives up.
    from ishti.dispatcher import FELL_SHORT
    answer = res.get("answer", "")
    return {"reply": answer, "sources": res.get("sources", []), "verified": res.get("verified", False),
            "no_escalate": res.get("confident") is False or not FELL_SHORT.search(answer)}


if __name__ == "__main__":
    assert match("search the web for the speed of light")
    assert not match("summarize this file")
    print("[self-check] web_search OK - routing")
