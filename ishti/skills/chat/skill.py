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


# Vague stand-ins for a task ("do the usual thing", "you know what to do") have no referent to act on. The local
# model asked to answer them claims it is getting on with it (live test 2026-10-03: "I'll get on with that right
# now.") while doing nothing - the honest answer is to ask which task is meant.
VAGUE = re.compile(r"^\s*(?:please\s+|just\s+|now\s+)?(?:do\s+(?:the|your|our)\s+usual(?:\s+thing)?|the\s+usual(?:\s+thing)?|"
                   r"same\s+as\s+usual|as\s+usual|you\s+know\s+what\s+to\s+do|do\s+your\s+thing|"
                   r"the\s+thing\s+(?:we|you)\s+(?:always|usually)\s+do)\s*[.!]?\s*$", re.I)


# "did you edit it?" / "have you sent it yet?" asks about what already happened - answered from the conversation (honestly), never by
# running the skill again (Laya sent "DID YOU EDITED IT ?" to capcut, which re-ran and claimed effects it never added).
DID_YOU = re.compile(r"^\s*(?:so\s+|and\s+|but\s+)?(?:did|have|has|were|was)\s+(?:you|it)\s+(?:\w+\s+){0,2}?(?:\w+ed|sent|done|made|"
                     r"finished|written|seen|read|got|gotten|it|that|this|them|him|her|send|edit|make|do|open|add|save|delete|"
                     r"book|call|remind|set|finish|complete|create|write|reply|check|buy|order|pay|post|upload|download)\b[^?]{0,60}\??\s*$", re.I)
# A mid-action abort ("I didn't mean to confirm that, stop") (checklist #200): stop what can be stopped and
# say honestly what already happened vs. what was halted - never claim a clean stop over a completed action.
ABORT = re.compile(r"\b(?:i\s+)?(?:didn'?t|did\s+not|didnt)\s+mean\s+to\s+(?:confirm|approve|send|do|say\s+yes|agree)\b|"
                   r"^\s*(?:please\s+)?(?:stop|halt|abort)\s*(?:it|that|this|now|please)?\s*[.!]?\s*$|"
                   r"^\s*(?:please\s+)?cancel\s+(?:that|it|this)\b|\b(?:undo|abort)\s+that\b", re.I)
# "Can you undo the email you sent?" (checklist #199): a delivered email has no recall. Answer honestly (from
# the T1 chat brain, since email itself is T2 and would wrongly gate a plain question) with the real option.
UNSEND = re.compile(r"\b(?:undo|unsend|un-?send|recall|take\s+back|retract)\b[^.!?\n]*\b(?:e-?mails?|mails?|messages?)\b|"
                    r"\b(?:undo|unsend|recall|retract)\s+(?:the\s+)?(?:e-?mail|mail)\s+(?:you\s+)?(?:sent|send)\b", re.I)
# "Should I buy this stock right now?" (checklist #167): never a buy/sell recommendation. A deterministic
# refusal means the answer is stable and never accidentally sounds like advice, whatever the model would say.
ADVICE = re.compile(r"\bshould\s+i\s+(?:buy|sell|invest|hold|short)\b|\b(?:buy|sell)\s+or\s+(?:sell|buy)\b|"
                    r"\bwould\s+you\s+(?:buy|sell)\b|\bis\s+(?:this|it|that)\s+(?:a\s+)?(?:good|bad|safe)\s+"
                    r"(?:buy|investment|stock|trade)\b|\bworth\s+(?:buying|investing\s+in)\b|\bwhen\s+should\s+i\s+(?:buy|sell|sell\s+it)\b", re.I)


# "What is the capital of Pakistan?" / "how does a vaccine work" / "who was Allama Iqbal": timeless knowledge the model answers itself
# in a second. Laya sent it to web search, which pasted raw search snippets (owner's chat). Live topics (prices, news, weather,
# "latest") are claimed by their own rules first; anything about the owner or this PC is not general knowledge.
# Contractions matter here: "What's the capital of Kazakhstan?" was NOT claimed (only "what is" matched) and the
# weaker arch2 model answered "Azerbaijan" - a wrong fact. "Define X" / "what does X mean" are knowledge too.
KNOWLEDGE = re.compile(r"^\s*(?:(?:what|who|where|when|which|why|how)(?:'s|s\b|\s+(?:is|are|was|were|does|do|did|many|much|far|long|old|big|likely|probably|around|about))\b|"
                       r"(?:define|what\s+does|meaning\s+of|what's\s+the\s+definition\s+of)\s+['\"]?[A-Za-z][\w'-]*)", re.I)
# also anything dated ("how many days until eid" was answered with an invented date): the calendar moves, the model's memory doesn't.
# "for me"/"to me" is polite filler in knowledge questions ("define X for me" is a general fact), so bare "me" is NOT here -
# only my/mine/i/i'm/our/we (real owner references). "president"/"ceo" are NOT here either: "who's the president of France"
# is timeless; the live case ("who is the president now") is caught by now/latest/current below.
PERSONAL = re.compile(r"\b(?:my|mine|i|i'm|our|we|ishti|this\s+pc|today|tonight|tomorrow|now|latest|current|price|weather|"
                      r"until|till|left|next|upcoming|this\s+year|20\d\d|date|days|score|won|winner|election|"
                      r"news|trending|this\s+week|released?|maintained|right\s+now|as\s+of\s+today)\b", re.I)



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


def build_context(text, domain="", session=None):
    # Verified glossary first: a definition question about a local-AI term (imatrix, GGUF, LoRA...) gets
    # the sourced definition so the local model cannot invent a meaning (ishti/glossary.py).
    try:
        from ishti import glossary
        if line := glossary.context_line(text):
            return line
    except Exception as _exc:
        swallowed('ishti/skills/chat/skill.py:build_context.glossary', _exc)
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
    try:
        from ishti import rgraph
        edges = rgraph.edges_of("me", direction="out") or []
        family_edges = [e for e in edges if e.get("rel") in (
            "brother", "sister", "father", "mother", "son", "daughter", "cousin", "uncle", "aunt",
            "grandfather", "grandmother", "spouse", "wife", "husband", "friend", "colleague")]
        if family_edges:
            f_lines = []
            owner_name = (get_facts().get("owner").get("name") or "").lower()
            for e in family_edges:
                rel = e.get("rel", "").title()
                name = e.get("d_name", "").title()
                if name.lower() in ("me", "you", owner_name, "owner"):
                    continue
                detail = f" ({e['detail']})" if e.get("detail") else ""
                f_lines.append(f"- {name}: your {rel}{detail}")
            if f_lines:
                parts.append("FAMILY & RELATIONSHIPS (from database):\n" + "\n".join(f_lines))
    except Exception as _exc:
        swallowed('ishti/skills/chat/skill.py:build_context.rgraph', _exc)
    if recalled:
        parts.append("Possibly relevant past context:\n" + "\n".join(f"- {s[:300]}" for s in recalled))
    # The apply end of the learning loop: without this line every lesson ISHTI ever learned is
    # write-only - recorded, scored, displayed on the dashboard, and never read by the model.
    try:
        from ishti.learning import lessons
        sid = session.get("id") if isinstance(session, dict) else None
        if isinstance(session, dict):
            session["_applied_lesson_ids"] = []
        block = lessons.context_for(
            text, domain=domain, session_id=sid,
            capture=session.get("_applied_lesson_ids") if isinstance(session, dict) else None,
        )
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
    if VAGUE.match(text):
        # ends with the question mark on purpose: fell_short() treats a clarifying question as a real
        # answer, while a flat "I don't know ..." would escalate chat -> web_search (which hung on
        # "Do the usual thing" in the live run, 2026-10-03).
        return {"reply": "I don't know what \"the usual\" is yet - we haven't set up a routine I can run. "
                         "Which task do you mean?", "sources": []}
    if DID_YOU.match(text):
        return did_you(text, session)
    if ADVICE.search(text):
        return {"reply": "I can't tell you whether to buy or sell - that would be me giving financial advice, "
                         "and I'm not qualified to, so I won't guess or nudge you either way. What I can do is pull "
                         "the live price, the recent trend and the numbers behind a named stock or coin so you can "
                         "weigh the risk yourself. Tell me which one and I'll get the data.", "sources": []}
    if UNSEND.search(text):
        return {"reply": "I can't unsend an email once it has gone out - email has no recall, and I won't "
                         "pretend it does. The real options are a short correction or follow-up to the recipient, "
                         "or a separate note explaining the mistake. Tell me who and what to say and I'll draft it "
                         "before anything is sent.", "sources": []}
    if ABORT.search(text):
        pend = (session or {}).get("pending")
        if isinstance(pend, dict) and pend.get("gate"):
            session["pending"] = None
            return {"reply": f"Stopped. I cancelled the pending \"{str(pend['gate'])[:160]}\" before it ran - "
                             "nothing was sent or changed.", "sources": []}
        return {"reply": "Stopped. Nothing was running and no action was waiting for a confirm, so nothing "
                         "was already done - there's nothing to undo. Tell me what you meant to confirm and I'll ignore it.",
                "sources": []}
    route = session.get("route") or {}
    if not isinstance(route, dict):
        route = {}
    role = "coder" if route.get("domain") == "code" else "planner"
    ctx = build_context(text, domain=str(route.get("domain") or ""), session=session)
    trace(session, "context", f"{ctx.count(chr(10) + '- ')} saved facts / memories in context" if ctx else "no saved context needed")
    trace(session, "model", f"thinking with {llm.model_for(role)}", status="start")
    # memory goes in the system prompt, not the owner's turn: in the user turn the model echoed the
    # "KNOWN FACTS" header back into its answer
    system = llm.style() + (f"\n\nContext you may use (never mention these headings; the known facts are exact "
                           f"and override anything else you recall):\n{ctx}" if ctx else "")
    # Keep the live conversation state pinned and summarize older turns instead of silently
    # throwing away everything outside the last dozen messages after a long chat resumes.
    try:
        from ishti.core.context import assemble_session_context
        assembled = assemble_session_context(session, text, verbatim_turns=6, max_msg_chars=1600)
        msgs = assembled + [{"role": "user", "content": text}]
    except Exception as exc:
        swallowed("ishti/skills/chat/skill.py:context_assembly", exc)
        msgs = session.get("history", [])[-12:] + [{"role": "user", "content": text}]
    out = (llm.chat(msgs, role=role, system=system, max_tokens=2048 if role == "coder" else 1024,
                    on_token=session.get("on_token")).content or "").strip()
    if not inspect(out)[0]:  # refusal / contamination -> one clean retry
        out = (llm.chat(msgs, role=role, system=system, temperature=0).content or "").strip()
    out = re.sub(r"\s*(?:KNOWN FACTS|Possibly relevant past context|LEARNED FROM THE OWNER)\b.*$", "", out, flags=re.S | re.I).strip()
    out = _honesty_guard(out)
    if not out:  # a system message, not model text: it must not go through the translator (which made up a reply for it)
        return {"reply": "No answer from the local model.", "sources": [], "no_translate": True}
    return {"reply": out, "sources": []}


if __name__ == "__main__":
    assert match("what is 17 times 23?")
    assert not match("make flashcards on cells")
    print("[self-check] chat OK - routing")
