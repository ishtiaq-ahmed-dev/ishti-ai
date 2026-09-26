"""
ISHTI Response Guard - the trust boundary on model output.

Two jobs:
1. REFUSAL: catch "I can't search the internet" style output when the tool in question
   actually ran (or is healthy). Such a response is blocked, not shown.
2. CONTAMINATION: catch hallucination markers ("classified", "Operation Phoenix",
   "intel briefing", "security protocols"). These are blocked AND kept out of session
   history, because history is replayed into later prompts - that feedback loop is exactly
   how one bad reply grew into a persistent persona (see docs/AUDIT.md F5).
"""

import re
from typing import List, Tuple

REFUSAL_PATTERNS = [
    r"i'?m not capable",
    r"i am not capable",
    r"i can'?t (?:access|search|browse|retrieve|provide real-?time)",
    r"i cannot (?:access|search|browse|retrieve)",
    r"i don'?t have (?:internet|real-?time|live|access to the internet)",
    r"i do not have (?:internet|real-?time|live)",
    r"i'?m unable to",
    r"i am unable to",
    r"unfortunately,? i",
    r"i can guide you (?:instead|on how)",
    r"sorry,? i can'?t",
    r"as an ai(?:,| language model)",
    r"my training (?:data )?(?:cut-?off|cutoff)",
]

CONTAMINATION_PATTERNS = [
    r"classified document",
    r"\bclassified\b",
    r"operation phoenix",
    r"intel(?:ligence)? briefing",
    r"security protocols?",
    r"eyes only",
    r"authorized personnel",
    r"clearance level",
]

_REFUSAL_RE = [re.compile(p, re.I) for p in REFUSAL_PATTERNS]
_CONTAM_RE = [re.compile(p, re.I) for p in CONTAMINATION_PATTERNS]


def find_refusals(text: str) -> List[str]:
    return [m.group(0) for r in _REFUSAL_RE for m in [r.search(text or "")] if m]


def find_contamination(text: str) -> List[str]:
    return [m.group(0) for r in _CONTAM_RE for m in [r.search(text or "")] if m]


def inspect(text: str) -> Tuple[bool, List[str], str]:
    """Returns (is_clean, matched_phrases, reason)."""
    refusals = find_refusals(text)
    contam = find_contamination(text)
    if contam:
        return False, contam, "contamination"
    if refusals:
        return False, refusals, "refusal"
    return True, [], ""


def is_safe_for_history(text: str) -> bool:
    """History is replayed into future prompts. Poisoned turns must never re-enter it."""
    return inspect(text)[0]


def clean_history(history_text: str) -> str:
    """
    Strip poisoned lines out of replayed conversation history.
    History from before this guard existed is still on disk; this keeps it from re-entering
    a prompt and re-teaching the model a persona it invented.
    """
    kept = [ln for ln in (history_text or "").splitlines() if is_safe_for_history(ln)]
    return "\n".join(kept)


def fallback_from_evidence(sources: List[dict], query: str) -> str:
    """When the model refuses despite live evidence, answer from the evidence directly."""
    if not sources:
        return ""
    lines = []
    for s in sources[:3]:
        title = (s.get("title") or "").strip()
        snippet = (s.get("snippet") or "").strip()
        url = s.get("url", "")
        lines.append(f"- {title}: {snippet} ({url})" if snippet else f"- {title} ({url})")
    return f"From live search for '{query}':\n" + "\n".join(lines)


if __name__ == "__main__":
    bad_refusal = "I'm not capable of directly searching the internet. However, I can guide you instead."
    bad_contam = "**CLASSIFIED DOCUMENT** EYES ONLY: AUTHORIZED PERSONNEL. Operation Phoenix briefing."
    good = "Tomorrow in Peshawar: 34 degrees and sunny."

    ok, hits, why = inspect(bad_refusal)
    assert not ok and why == "refusal", (ok, hits, why)
    ok, hits, why = inspect(bad_contam)
    assert not ok and why == "contamination", (ok, hits, why)
    ok, hits, why = inspect(good)
    assert ok, hits
    assert not is_safe_for_history(bad_contam)
    assert is_safe_for_history(good)
    poisoned = "ISHTI: **CLASSIFIED DOCUMENT**\nYOU: hello\nISHTI: Hey, all good here."
    assert "CLASSIFIED" not in clean_history(poisoned)
    assert "all good here" in clean_history(poisoned)
    assert "weather.com" in fallback_from_evidence(
        [{"title": "Peshawar forecast", "snippet": "34C", "url": "https://weather.com/x"}], "weather"
    )
    print("[self-check] response_guard OK - refusal, contamination, history gate, evidence fallback")
