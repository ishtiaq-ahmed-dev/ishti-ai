"""Stage 1 - gap detection: is there actually no skill for this? (ask at most ONE clarifying question).

Two independent signals, because either one alone lies:
  * the router's own answer for this request (`route()` over the live skill registry, with its confidence);
  * `skill_lifecycle.discover_gaps()` - requests NOTHING handled that have been seen 2+ times in 30 days
    (the route_log is the evidence, not a guess).
A gap exists when either says "nobody owns this". A skill that already answers with enough confidence is
NOT a gap, so skill_creator stays out of its way.

Ambiguity: one clarifying question, never two, and only when the request has no concrete object to act on
("make yourself able to do that thing we talked about"). Otherwise we proceed - the master prompt's rule.
"""
from __future__ import annotations

import re

def confidence_floor() -> float:
    """'Enough confidence' = the ROUTER's own configured threshold, never an invented number.

    `ishti/router.py:104` accepts a Laya decision at `router.min_confidence` (0.2 in config.json), and
    this repo's own RuntimeWarning says Laya's confidences are uncalibrated - so a second, private floor
    would quietly re-decide what 'owning a request' means. Gap detection defers to the same number
    routing already uses, so the two can never disagree.
    """
    try:
        from ishti import CONFIG
        return float(CONFIG["router"].get("min_confidence", 0.2))
    except Exception:
        return 0.2
VAGUE = re.compile(r"^\s*(?:that|this|it|something|anything|the thing|do (?:it|that))\b.*$", re.I)
NO_OBJECT = re.compile(r"\b(?:stuff|things?|whatever|whatever it is|you know what i mean)\b", re.I)


def route_answer(request, skills=None, route_fn=None):
    """{"skill", "confidence", "source"} for this request, or {"skill": None}. Never raises.

    Lazy imports: this module must work in a test with no dispatcher, no Ollama and no Laya.
    """
    try:
        if route_fn is None:
            from ishti import router as router_mod
            if skills is None:
                from ishti.dispatcher import SKILLS as skills   # already-loaded registry
            route_fn = router_mod.route
        dec = route_fn(request, skills or {})
    except Exception as exc:                                    # a routing failure is not a gap signal
        return {"skill": None, "confidence": 0.0, "source": "unavailable",
                "error": f"{type(exc).__name__}: {exc}"}
    return {"skill": dec.get("skill"), "confidence": float(dec.get("confidence") or 0.0),
            "source": dec.get("source", ""), "ms": dec.get("ms")}


def recurring_gaps(limit=8, discover_fn=None):
    """Requests nothing handled, repeatedly - the slow signal that a capability is genuinely missing."""
    try:
        if discover_fn is None:
            from ishti import skill_lifecycle
            discover_fn = skill_lifecycle.discover_gaps
        return discover_fn(limit) or []
    except Exception:
        return []


def detect_gap(request, skills=None, route_fn=None, discover_fn=None) -> dict:
    """-> {"gap": bool, "reason": str, "route": dict, "recurring": list, "clarify": str|None}"""
    if not str(request or "").strip():
        return {"gap": False, "reason": "empty request", "route": route_answer(""), "recurring": [],
                "clarify": None}
    dec = route_answer(request, skills=skills, route_fn=route_fn)
    recurring = recurring_gaps(discover_fn=discover_fn)
    # A router ERROR is not evidence that no skill exists - building one on that basis would duplicate
    # a skill we simply could not see. Fail safe: unknown is not missing.
    if dec.get("source") == "unavailable":
        return {"gap": False,
                "reason": f"could not be determined: the router was unavailable ({dec.get('error')}); "
                          f"'nobody owns this' is not proven, so nothing is created",
                "route": dec, "recurring": recurring, "clarify": None}
    floor = confidence_floor()
    held = dec["skill"] is not None and dec["confidence"] >= floor and dec["source"] != "default"
    if held:
        return {"gap": False, "reason": f"'{dec['skill']}' already owns this ({dec['source']}, "
                                        f"confidence {dec['confidence']} >= {floor})",
                "route": dec, "recurring": recurring, "clarify": None}
    clarify = one_question(request)
    reason = (f"nothing answered it (router: {dec['skill'] or 'none'} at {dec['confidence']} "
              f"via {dec['source'] or 'n/a'}, floor {floor}"
              + (f"; also seen unhandled {recurring[0]['count']}x in 30 days" if recurring else ")"))
    return {"gap": True, "reason": reason, "route": dec, "recurring": recurring, "clarify": clarify}


def one_question(request) -> str | None:
    """The ONE clarifying question - only when the request names no object to work on. None otherwise.

    Deterministic on purpose: an LLM would ask more often than it should, and the master prompt allows
    at most one question.
    """
    t = str(request or "").strip()
    if not t:
        return None
    if VAGUE.match(t) or NO_OBJECT.search(t):
        return f"What exactly should I learn to do? (You said: \"{t[:90]}\")"
    return None


if __name__ == "__main__":
    # ---- offline self-check: no dispatcher, no Ollama, no Laya, no network ---------------------
    from ishti.skills import load_skills

    skills = load_skills()
    assert len(skills) > 50, len(skills)

    # the floor is the ROUTER's own number, never one invented here
    assert confidence_floor() == 0.2, confidence_floor()

    # 1. a request an existing skill deterministically owns is NOT a gap (real router, regex match)
    r = detect_gap("what is the weather tomorrow in Peshawar?")
    assert r["gap"] is False and r["route"]["skill"] == "weather", r
    assert r["clarify"] is None, r

    # 2. at/above the router's own floor the request IS considered owned - even though Laya's score is
    #    uncalibrated (this is the 0.359/files case that failed with an invented 0.35 floor). Injected so
    #    it is deterministic: no model, no Laya, no dispatcher.
    def laya_359(text, skills=None):
        return {"skill": "files", "confidence": 0.359, "source": "laya", "ms": 0}

    owned = detect_gap("turn every PNG in this folder into a single PDF with one page per image",
                       skills={}, route_fn=laya_359)
    assert owned["gap"] is False and "0.359" in owned["reason"], owned

    # 3. below the floor => a gap, and NO question (the request names an object to work on)
    def laya_low(text, skills=None):
        return {"skill": "files", "confidence": 0.05, "source": "laya", "ms": 0}

    r2 = detect_gap("turn every PNG in this folder into a single PDF with one page per image",
                    skills={}, route_fn=laya_low)
    assert r2["gap"] is True and r2["clarify"] is None, r2
    assert "floor" in r2["reason"], r2

    # 4. a vague request IS a gap AND gets exactly one clarifying question
    r3 = detect_gap("make yourself able to do that thing we talked about", skills={}, route_fn=laya_low)
    assert r3["gap"] is True and r3["clarify"], r3
    assert r3["clarify"].count("?") == 1, r3

    # 5. a routing failure degrades to "not a gap", never an exception
    def boom(*a, **k):
        raise RuntimeError("router exploded")

    r4 = detect_gap("anything at all", skills={}, route_fn=boom)
    assert r4["gap"] is False and r4["route"]["source"] == "unavailable", r4
    assert "RuntimeError" in r4["route"]["error"], r4

    # 6. the recurring-gap signal is optional: a missing route_log must not break detection
    r5 = detect_gap("make yourself able to count the strands in a DNA sequence", skills={},
                    route_fn=laya_low, discover_fn=lambda *_: [])
    assert r5["gap"] is True and r5["recurring"] == [], r5

    print("[self-check] skill_creator.gap OK - real router not re-created, floor taken from the router's "
          "own config (0.2), vague ask asks exactly one question, router failure degrades safely, "
          "route_log optional")

