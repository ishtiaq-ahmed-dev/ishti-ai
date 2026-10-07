"""Query Resolution / Rewriting (Part D2) — the critical-bug fix.

Before routing and before ANY tool call, every user message is resolved against the
Conversation State into a STANDALONE request:

    "go deep research the internet and find the cheapest price"
        -> "find the cheapest current price: Sony WH-1000XM5
            (user location: Pakistan; budget: 50000 PKR)"

Rules:
- references ("it", "that", "the cheapest one", "those results", "go deeper") are
  detected and rewritten with the active topic / last search / constraints.
- a message that names its own subject ("search iPhone 16") stays standalone and
  BECOMES the new topic (T2: the most recent topic wins).
- a genuinely ambiguous reference ("the cheapest one" with two live candidates that the
  anchored topic doesn't cover) asks ONE short clarifying question, never guesses (T3).
- BOTH the original and the rewritten request are returned; the caller logs both in the
  trace (D2) and passes `resolved.request` to every component (D3).
- `llm_fn` is injectable (mock in tests); deterministic rewrite runs first — the LLM is
  only consulted when the deterministic pass cannot resolve.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from ishti.core import state as cstate

REFERENCE = cstate.REFERENCES
FILLER = cstate.FILLER
PRODUCT_CODE = cstate.PRODUCT_CODE
BRAND_WORDS = cstate.BRAND_WORDS

# an explicit verb with its own target is the owner's request: context may be added, never substituted
ACTION = re.compile(r"\b(?:send|forward|delete|remove|open|call|message|text|email|share|save|rename|move|copy|post|book|pay|buy)\b", re.I)
AMBIG_ONE = re.compile(r"\b(?:the\s+)?(?:cheapest|best|better)\s+one\b", re.I)
GREETING_ONLY = re.compile(r"^\s*(?:hi|hello|hey|yo|thanks|thank\s+you|ok(?:ay)?|good\s*(?:morning|evening|night)"
                           r"|salam|aoa|bye)\b[!.?\s]*$", re.I)  # "ishti" alone is NOT a greeting (memory skill)
META_QUESTION = re.compile(r"^\s*(?:what(?:'s| is)\s+(?:up|new)|who\s+are\s+you|what\s+can\s+you\s+do)\b", re.I)


@dataclass
class Resolved:
    """The outcome of resolving one user message. `request` is what every downstream
    component receives (D3); `original` is what the owner actually said."""
    original: str
    request: str
    changed: bool = False
    followup: bool = False
    ambiguous: bool = False
    clarify_question: str = ""
    candidates: list[str] = field(default_factory=list)
    trace_note: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {"original": self.original, "request": self.request, "changed": self.changed,
                "followup": self.followup, "ambiguous": self.ambiguous,
                "clarify_question": self.clarify_question, "candidates": self.candidates}


def _with_constraints(request: str, st: cstate.ConversationState) -> str:
    bits = []
    if st.constraints.get("country"):
        bits.append(f"user location: {st.constraints['country']}")
    if st.constraints.get("currency"):
        bits.append(f"currency: {st.constraints['currency']}")
    if st.constraints.get("budget"):
        bits.append(f"budget: {st.constraints['budget']}")
    if st.constraints.get("date_range"):
        bits.append(f"time frame: {st.constraints['date_range']}")
    if st.constraints.get("brand"):
        bits.append(f"brand: {st.constraints['brand']}")
    return f"{request} ({'; '.join(bits)})" if bits else request


class QueryResolver:
    """Resolves a message against ConversationState. `llm_fn(prompt) -> str` is the
    optional LLM rewriter (inject a fake in tests); None = deterministic-only."""

    def __init__(self, llm_fn=None, max_chars: int = 400):
        self.llm_fn = llm_fn
        self.max_chars = max_chars

    # ------------------------------------------------------------------ API
    def resolve(self, text: str, st: cstate.ConversationState) -> Resolved:
        text = FILLER.sub("", (text or "").strip()) or (text or "").strip()
        if not text or GREETING_ONLY.match(text) or META_QUESTION.match(text):
            return Resolved(original=text, request=text, changed=False, followup=False)

        subj = cstate.subject_of(text)
        referenced = bool(REFERENCE.search(text))
        task = cstate.task_of(text)
        has_subject = bool(subj) and not referenced

        # 1. names its own subject (and isn't mid-reference) -> already standalone; the
        #    subject becomes the new topic (T2) unless it's a pure task continuation.
        if has_subject and (not st.active_topic or not referenced):
            return Resolved(original=text, request=text, changed=False, followup=False,
                            trace_note={"subject": subj} if subj else {})

        # 2. subject-less but task-carrying, or explicit references -> follow-up on state
        followup = referenced or bool(task) or bool(cstate.FOLLOWUP_HINTS.search(text))
        if followup and (st.active_topic or st.last_searches):

            # T3: "the cheapest/best ONE" is ambiguous when two candidates are live and
            # the anchored topic does not already cover both.
            candidates = [c for c in st.ambiguous_refs if c.lower() not in (st.active_topic or "").lower()]
            if AMBIG_ONE.search(text) and len(candidates) >= 2 and not st.constraints.get("brand"):
                return Resolved(original=text, request=text, changed=False, followup=True,
                                ambiguous=True,
                                clarify_question=f"Which one — {candidates[0]} or {candidates[1]}?",
                                candidates=candidates,
                                trace_note={"why": "ambiguous reference between two topics"})

            anchor = st.active_topic or st.last_searches[-1]["query"]
            if (task or len(text.split()) <= 3) and not ACTION.search(text):  # "go deeper": the anchor IS the request
                tsk = task or st.active_task or "continue on"
                request = _with_constraints(f"{tsk}: {anchor}", st)
            else:                                # "now send this to rayyan on whatsapp": keep the owner's own words
                request = _with_constraints(f"{text} (regarding: {anchor})", st)
            return Resolved(original=text, request=request[:self.max_chars], changed=True,
                            followup=True, trace_note={"why": "rewritten with the active topic",
                                                       "anchor": anchor})

        # 3. follow-up flavour but nothing to anchor on: LLM rewriter, else pass through
        if followup and self.llm_fn:
            rewritten = self._llm_rewrite(text, st)
            if rewritten:
                return Resolved(original=text, request=rewritten[:self.max_chars], changed=True,
                                followup=True, trace_note={"why": "LLM rewrite from conversation"})
        return Resolved(original=text, request=text, changed=False, followup=followup,
                        trace_note={"why": "nothing to resolve from"} if followup else {})

    # ---------------------------------------------------------------- internals
    def _llm_rewrite(self, text: str, st: cstate.ConversationState) -> str | None:
        summary = cstate.ConversationStateManager.summary(st, max_chars=500)
        prompt = (
            "Conversation state:\n" + (summary or "(none)") +
            f"\n\nOwner's new message: {text}\n\n"
            "Rewrite the message as ONE complete, standalone request that an assistant could act on "
            "without the conversation. Keep their wording, names and numbers exactly; add the topic "
            "and constraints the message only implies. If the message names a different topic than "
            "the state, the NEW topic wins. Output only the rewritten request, or the original if "
            "it is already standalone.")
        try:
            out = (self.llm_fn(prompt) or "").strip().strip('"')
        except Exception:
            return None
        if not out or len(out) < 3 or len(out) > self.max_chars:
            return None
        if REFERENCE.search(out) and not (PRODUCT_CODE.search(out) or BRAND_WORDS.search(out)):
            return None  # the rewrite still references "it" — not standalone, reject
        return out

    # ---------------------------------------------------------------- ambiguity
    @staticmethod
    def clarification(resolved: Resolved) -> str | None:
        """The ONE short clarifying question when the reference is genuinely ambiguous."""
        return resolved.clarify_question if resolved.ambiguous else None


if __name__ == "__main__":
    # ---- offline self-check (no Ollama, no network) ---------------------------
    from ishti.core.state import ConversationStateManager, MemoryStore

    mgr = ConversationStateManager(MemoryStore())
    st = mgr.get("s1")

    r = QueryResolver().resolve("Search about Sony WH-1000XM5", st)
    assert r.request == "Search about Sony WH-1000XM5", r.request
    with mgr.turn("s1") as s:
        mgr.observe_user_turn(s, "Search about Sony WH-1000XM5")
    assert "wh-1000xm5" in s.active_topic.lower(), s.active_topic

    # T1: the critical-bug scenario
    with mgr.turn("s1") as s:
        mgr.observe_user_turn(s, "go deep research the internet and find the cheapest price")
    r = QueryResolver().resolve("go deep research the internet and find the cheapest price", mgr.get("s1"))
    assert "WH-1000XM5" in r.request and r.changed, r.request

    # T2: topic switch — the most recent topic wins
    with mgr.turn("s1") as s:
        mgr.observe_user_turn(s, "now search Samsung S25")
    r2 = QueryResolver().resolve("cheapest price?", mgr.get("s1"))
    assert "S25" in r2.request and "WH-1000XM5" not in r2.request, r2.request
    print("[self-check] core.resolver OK")
