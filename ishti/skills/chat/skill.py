"""Chat skill: local LLM answer. Exact facts are injected first and win; semantic recall
that mentions the same entity is dropped so a stale memory can't contradict a fact."""
import re

from ishti import llm
from ishti.facts import get_facts
from orchestrator.response_guard import inspect


ARITHMETIC = re.compile(r"\d\s*(?:[-+*/x×^]|times|plus|minus|divided\s+by|multiplied\s+by|to\s+the\s+power\s+of)\s*\d", re.I)


CREATIVE = re.compile(r"^\s*(?:please\s+)?(?:write|compose)\s+(?:me\s+)?(?:a|an|some)\s+(?:\w+\s+)?"
                      r"(?:poem|haiku|story|song|joke|limerick|essay|letter|speech|toast|caption)s?\b", re.I)


def match(text):
    # arithmetic and creative writing: Laya zero-shot sends "a haiku about rain" to weather
    return bool(ARITHMETIC.search(text) or CREATIVE.match(text))


def build_context(text):
    f = get_facts()
    rows = f.mentioned_in(text) + [("owner", a, v) for a, v in f.get("owner").items() if a != "entity"]
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
    return "\n\n".join(parts)


def run(text, session):
    role = "coder" if session.get("route", {}).get("domain") == "code" else "planner"
    ctx = build_context(text)
    msgs = session.get("history", [])[-12:] + [
        {"role": "user", "content": f"{ctx}\n\nOwner: {text}" if ctx else text}]
    out = (llm.chat(msgs, role=role, max_tokens=2048 if role == "coder" else 1024,
                    on_token=session.get("on_token")).content or "").strip()
    if not inspect(out)[0]:  # refusal / contamination -> one clean retry
        out = (llm.chat(msgs[-1:], role=role, temperature=0).content or "").strip()
    return {"reply": out or "No answer from the local model.", "sources": []}
