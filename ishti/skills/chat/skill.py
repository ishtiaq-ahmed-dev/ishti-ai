"""Chat skill: local LLM answer. Exact facts are injected first and win; semantic recall
that mentions the same entity is dropped so a stale memory can't contradict a fact."""
import re

from ishti import llm
from ishti.facts import get_facts
from ishti.skills import trace
from orchestrator.response_guard import inspect

try:  # best-effort failure log (never breaks the module if logging is unavailable)
    from ishti.core.logutil import swallowed
except Exception:  # pragma: no cover
    def swallowed(*_a, **_k):
        return None



ARITHMETIC = re.compile(r"\d\s*(?:[-+*/x×^]|times|plus|minus|divided\s+by|multiplied\s+by|to\s+the\s+power\s+of)\s*\d", re.I)


CREATIVE = re.compile(r"^\s*(?:please\s+)?(?:write|compose)\s+(?:me\s+)?(?:a|an|some)\s+(?:\w+\s+)?"
                      r"(?:poem|haiku|story|song|joke|limerick|essay|letter|speech|toast|caption)s?\b", re.I)

# Programming questions the coder model answers (chat.run picks the coder role). Priority 90 puts this rule last,
# so a real trigger - dev's git/test/lint rules, skillsmith's, docs' - is still checked first.
# Written after Laya sent "write a python function to reverse a linked list" to apps and "how do I fix a segfault
# in C" to capcut: both are code questions, and the wrong skill answers them confidently.
CODE = re.compile(r"\b(?:in|with|using)\s+(?:python|javascript|typescript|java|c\+\+|c#|golang|go|rust|sql|bash|"
                  r"powershell|kotlin|swift|php|ruby)\b"
                  r"|\b(?:segfault|stack\s*trace|traceback|syntax\s+error|compile\s+error|compiler\s+error|"
                  r"null\s+pointer|undefined\s+behaviou?r|memory\s+leak|deadlock|refactor)\b"
                  r"|\b(?:write|fix|debug|refactor|explain|optimize|optimi[sz]e)\b[^.]{0,40}?\b(?:function|code|script|"
                  r"program|class|method|regex|regexp|bug|algorithm|loop|api\s+endpoint)\b"
                  r"|\b(?:python|javascript|typescript|c\+\+|sql|regex)\b[^.]{0,30}?\b(?:function|code|script|error|"
                  r"exception|snippet)\b", re.I)


BACKCHANNEL = re.compile(r"\b(?:thanks|thank\s+you|that\s+helped|that\s+helps|that's\s+all|good\s+job|"
                         r"well\s+done|nice\s+work|appreciate\s+it|got\s+it|makes\s+sense)\b", re.I)


# "did you edit it?" / "have you sent it yet?" asks about what already happened - answered from the conversation (honestly), never by
# running the skill again (Laya sent "DID YOU EDITED IT ?" to capcut, which re-ran and claimed effects it never added).
DID_YOU = re.compile(r"^\s*(?:so\s+|and\s+|but\s+)?(?:did|have|has|were|was)\s+(?:you|it)\s+(?:\w+\s+){0,2}?(?:\w+ed|sent|done|made|"
                     r"finished|written|seen|read|got|gotten|it|that|this|them|him|her|send|edit|make|do|open|add|save|delete|"
                     r"book|call|remind|set|finish|complete|create|write|reply|check|buy|order|pay|post|upload|download)\b[^?]{0,60}\??\s*$", re.I)


# "What is the capital of Pakistan?" / "how does a vaccine work" / "who was Allama Iqbal": timeless knowledge the model answers itself
# in a second. Laya sent it to web search, which pasted raw search snippets (owner's chat). Live topics (prices, news, weather,
# "latest") are claimed by their own rules first; anything about the owner or this PC is not general knowledge.
KNOWLEDGE = re.compile(r"^\s*(?:what|who|where|when|which|why|how)\s+(?:is|are|was|were|does|do|did|many|much|far|long|old|big)\b", re.I)
# also anything dated ("how many days until eid" was answered with an invented date): the calendar moves, the model's memory doesn't
PERSONAL = re.compile(r"\b(?:my|me|mine|i|i'm|our|we|you|your|ishti|this\s+pc|today|tonight|tomorrow|now|latest|current|price|weather|"
                      r"until|till|left|next|upcoming|this\s+year|20\d\d|date|days|score|won|winner|election|president|prime\s+minister|ceo)\b", re.I)


def match(text):
    if DID_YOU.match(text):
        return True
    if KNOWLEDGE.match(text) and not PERSONAL.search(text) and len(text.split()) <= 14:
        from orchestrator.intent_detector import IntentDetector
        if IntentDetector.classify(text)["mode"] == "LOCAL_KNOWLEDGE":
            return True
    # arithmetic, creative writing and code questions: Laya zero-shot sends "a haiku about rain" to weather,
    # "write a python function ..." to apps and "fix a segfault in C" to capcut.
    # Back-channel ("thanks, that helped") is a chat reply too - routing it to a task skill answers
    # "what would you like me to do?" and breaks the flow.
    return bool(ARITHMETIC.search(text) or CREATIVE.match(text) or CODE.search(text)
                or BACKCHANNEL.search(text))


def build_context(text, domain=""):
    f = get_facts()
    rows = f.mentioned_in(text) + [("owner", a, v) for a, v in f.get("owner").items()  # instructions/prefs: llm.style()
                                   if a not in ("entity", "instructions") and not a.startswith("prefers_")]
    seen = {e.lower() for e, _, _ in rows if e != "owner"}
    try:
        from ishti.semantic import get_semantic
        recalled = [s for s in get_semantic().recall(text) if not any(e in s.lower() for e in seen)]
    except Exception:
        recalled = []  # semantic memory is optional context, never a hard dependency
    parts = []
    if rows:
        parts.append("KNOWN FACTS (exact):\n" + "\n".join(f"- {e} {a}: {v}" for e, a, v in rows))
    if recalled:
        parts.append("Possibly relevant past context:\n" + "\n".join(f"- {s[:300]}" for s in recalled))
    # The apply end of the learning loop: without this line every lesson ISHTI ever learned is
    # write-only - recorded, scored, displayed on the dashboard, and never read by the model.
    try:
        from ishti.learning import lessons
        block = lessons.context_for(text, domain=domain)
        if block:
            parts.append(block)
    except Exception as _exc:
        swallowed('ishti/skills/chat/skill.py:build_context', _exc)  # learning is an enhancement; a broken lesson store must not sink the reply
    return "\n\n".join(parts)


# FIX-2 (QA prompt 2, reviewer pass): the local model sometimes answers a "store my password/api key"
# message with a fabricated confirmation like "Stored your API key: ... in secure memory" - chat has
# NO write path (facts are only written via the memory skill, after the owner confirms). Verified in
# the reviewer pass: reply claimed a write while data/arch2_memory.db stayed clean. This guard is
# deterministic, so it cannot be prompted away; it fires only when the reply both names a secret and
# claims a write.
_SECRET_WORD = re.compile(r"\b(password|passcode|passphrase|api[ _-]?key|secret[ _-]?key|access[ _-]?token|"
                          r"seed\s*(?:phrase|recovery)|recovery\s*phrase|2fa|otp|one[- ]time\s*code|"
                          r"credit\s*card(?:\s+number)?|cvv|cvc)\b", re.I)
_CLAIM_WRITE = re.compile(r"\b(stored|saved|locked away|secure vault|secure memory|safekeeping|safe keeping)\b", re.I)


def _honesty_guard(reply):
    """Replace a fabricated secret-storage confirmation with the true state."""
    if _SECRET_WORD.search(reply) and _CLAIM_WRITE.search(reply):
        return ("I have to be straight with you: I don't store passwords, keys, codes or recovery "
                "phrases, and I haven't stored this one - nothing was written anywhere. If you "
                "genuinely want ISHTI to remember a fact, the memory skill can do it, and it will "
                "ask you to confirm before saving anything.")
    return reply


def did_you(text, session):
    """"Did you edit it?" - answered only from what this conversation shows was done (the honest answer is often "no")."""
    turns = "\n".join(f"{'Owner' if m['role'] == 'user' else 'ISHTI'}: {str(m['content'])[:500]}" for m in session.get("history", [])[-8:])
    out = llm.ask(f"Conversation so far:\n{turns or '(nothing yet)'}\n\nThe owner now asks: {text}\n\n"
                  "Answer whether that action was actually DONE, using only the conversation above. It counts as done only if an ISHTI "
                  "message clearly reports the finished result (not a plan, not a question, not \"I will\"). If it was not done or you "
                  "cannot tell, say plainly that it was not done yet and offer to do it now. Two sentences at most.",
                  role="planner", system=llm.style(), temperature=0, max_tokens=120)
    return {"reply": out or "No - I haven't done that yet. Shall I do it now?", "sources": []}


def run(text, session):
    if DID_YOU.match(text):
        return did_you(text, session)
    route = session.get("route") or {}
    if not isinstance(route, dict):
        route = {}
    role = "coder" if route.get("domain") == "code" else "planner"
    ctx = build_context(text, domain=str(route.get("domain") or ""))
    trace(session, "context", f"{ctx.count(chr(10) + '- ')} saved facts / memories in context" if ctx else "no saved context needed")
    trace(session, "model", f"thinking with {llm.model_for(role)}", status="start")
    # memory goes in the system prompt, not the owner's turn: in the user turn the model echoed the
    # "KNOWN FACTS" header back into its answer
    system = llm.style() + (f"\n\nContext you may use (never mention these headings; the known facts are exact "
                           f"and override anything else you recall):\n{ctx}" if ctx else "")
    msgs = session.get("history", [])[-12:] + [{"role": "user", "content": text}]
    out = (llm.chat(msgs, role=role, system=system, max_tokens=2048 if role == "coder" else 1024,
                    on_token=session.get("on_token")).content or "").strip()
    if not inspect(out)[0]:  # refusal / contamination -> one clean retry
        out = (llm.chat(msgs[-1:], role=role, system=system, temperature=0).content or "").strip()
    out = re.sub(r"\s*(?:KNOWN FACTS|Possibly relevant past context|LEARNED FROM THE OWNER)\b.*$", "", out, flags=re.S | re.I).strip()
    out = _honesty_guard(out)
    if not out:  # a system message, not model text: it must not go through the translator (which made up a reply for it)
        return {"reply": "No answer from the local model.", "sources": [], "no_translate": True}
    return {"reply": out, "sources": []}


if __name__ == "__main__":
    assert match("what is 17 times 23?")
    assert not match("make flashcards on cells")
    print("[self-check] chat OK - routing")
