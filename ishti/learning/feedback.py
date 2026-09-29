"""Feedback detection, attribution, and reinforcement reward assignment (spec 5, 6, 7, 16).

Detects explicit corrections ("no use Chrome", "that was wrong"), positive confirmations,
negative sentiment, and updates lessons, mistake records, and scalar rewards.
"""
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from ishti.learning import errors, lessons, store

KINDS = ("explicit_correction", "implicit_correction", "positive_confirmation", "negative_reaction")

_POS = re.compile(
    r"^(?:perfect|great(?:\s+job)?|awesome|thank you|thanks|good job|well done|exactly|spot on|"
    r"nice|love it|brilliant|correct|that's right|super|wonderful)[.!]?$",
    re.I
)
_NEG = re.compile(
    r"^(?:bad|horrible|terrible|useless|stop doing that|you suck|no that's bad|"
    r"wrong|completely wrong|not what i (?:asked|wanted|meant))[.!]?$",
    re.I
)

def reward_of(status: str, fb: Optional[Dict[str, Any]] = None) -> float:
    """Spec 16 scalar reward table."""
    if fb:
        pol = fb.get("polarity")
        kind = fb.get("kind")
        if pol == "positive" or kind == "positive_confirmation":
            return 1.0
        if kind == "explicit_correction":
            return -0.8
        if kind == "negative_reaction" or pol == "negative":
            return -1.0
        if kind == "implicit_correction":
            return -0.5
    st = (status or "").lower()
    if st in ("ok", "success", "done"):
        return 0.5
    if st in ("failed", "error"):
        return -0.5
    if st == "cancelled":
        return 0.0
    return 0.0

def detect(text: str, last_request: str = "", last_skill: str = "",
           pending: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """Scan text for feedback/correction signals."""
    t = (text or "").strip()
    if not t or len(t) > 300:
        return None
    if _POS.match(t):
        return {
            "kind": "positive_confirmation", "polarity": "positive",
            "message": t, "error_type": "", "incorrect": "", "correct": "",
            "target": last_skill or "", "confidence": 0.9, "source": "owner_sentiment"
        }
    if _NEG.match(t):
        return {
            "kind": "negative_reaction", "polarity": "negative",
            "message": t, "error_type": "wrong_interpretation", "incorrect": "", "correct": "",
            "target": last_skill or "", "confidence": 0.8, "source": "owner_sentiment"
        }
    # Check for error taxonomy classification
    err = errors.classify(t)
    if err and err.get("error_type") and err.get("error_type") != "unknown":
        return {
            "kind": "explicit_correction", "polarity": "negative",
            "message": t, "error_type": err["error_type"],
            "incorrect": err.get("incorrect", ""), "correct": err.get("correct", ""),
            "target": err.get("domain") or last_skill or "", "confidence": 0.95,
            "source": "explicit_user_correction"
        }
    return None

def from_correction(text: str, wrong: str, right: str, session: Optional[Any] = None) -> Dict[str, Any]:
    """Build feedback directly from an explicit skill routing correction."""
    return {
        "kind": "explicit_correction", "polarity": "negative", "error_type": "wrong_skill",
        "incorrect": wrong, "correct": right, "message": text, "target": "skill_routing",
        "confidence": 1.0, "source": "explicit_user_correction"
    }


def apply(fb: Dict[str, Any], interaction: Optional[Dict[str, Any]] = None,
          session: Optional[Any] = None) -> Dict[str, Any]:
    """Apply feedback: update store, attribute mistake/lesson, adjust rewards."""
    if not fb:
        return {"effects": [], "lesson": None, "ask": None}
    now = store.now()
    effects = []
    iid = (interaction or {}).get("id") or ""
    tid = (interaction or {}).get("task_id") or ""
    
    # Record feedback row
    fb_id = store.add("feedback",
                      ts=now, interaction_id=iid, kind=fb.get("kind", ""),
                      polarity=fb.get("polarity", ""), target=fb.get("target", ""),
                      message=fb.get("message", ""), error_type=fb.get("error_type", ""),
                      incorrect=fb.get("incorrect", ""), correct=fb.get("correct", ""),
                      source=fb.get("source", "owner"),
                      confidence=float(fb.get("confidence", 0.9)),
                      applied="applied", task_id=tid)
    effects.append(f"recorded_feedback_{fb_id}")

    lesson_out = None
    # If explicit correction with wrong/right or error type, create/adjust lesson & mistake
    if fb.get("kind") == "explicit_correction":
        err_type = fb.get("error_type") or "unknown"
        wrong = fb.get("incorrect") or ""
        right = fb.get("correct") or ""
        # the lesson is scoped to where the mistake happened: the corrected thing, else the skill that ran
        domain = fb.get("target") or (interaction or {}).get("skill") or ""
        errors.record(fb, interaction_id=iid, domain=domain)
        effects.append("recorded_mistake")
        
        # Add high-confidence lesson (spec 8). The lesson's CONTEXT carries the corrected
        # subject (the app/person/file) AND the request's own words — 'open my work browser'
        # must retrieve the Chrome lesson next time, not just a request literally saying 'apps'.
        if right:
            scope = "application" if err_type == "wrong_application" else "domain"
            subject = domain or err_type
            # retrieval recall WITHOUT touching context: `context` stays the exact subject so
            # contradiction pairing (lessons.contradictions matches context=) keeps working;
            # the request's own words go in `note`, which guidance_for() also searches.
            req_words = [w for w in str((interaction or {}).get("request") or "").lower().split()
                         if len(w) > 3 and w not in errors.STOP][:6]
            statement = f"When dealing with {subject}, use {right} (not {wrong})." if wrong else f"When dealing with {subject}, use {right}."
            lesson_out = lessons.add(
                kind="procedural_lesson",
                statement=statement,
                scope=scope,
                context=subject,
                incorrect=wrong,
                correct=right,
                source="explicit_user_correction",
                confidence=1.0,
                interaction_id=iid,
                note=" ".join(req_words)[:160]
            )
            effects.append("added_procedural_lesson")
            
    return {"effects": effects, "lesson": lesson_out, "ask": None}

if __name__ == "__main__":
    import tempfile
    tmp = tempfile.mkdtemp()
    store.set_db_path(os.path.join(tmp, "fb.db"))
    
    assert reward_of("ok") == 0.5
    assert reward_of("failed") == -0.5
    
    pos = detect("Great job!")
    assert pos and pos["polarity"] == "positive" and reward_of("ok", pos) == 1.0
    
    corr = detect("No, use Chrome")
    assert corr and corr["kind"] == "explicit_correction" and corr["correct"] == "chrome"
    assert reward_of("failed", corr) == -0.8
    
    res = apply(corr, interaction={"id": "it_123", "task_id": "tsk_1"})
    assert "recorded_mistake" in res["effects"]
    assert res["lesson"] and res["lesson"]["correct"] == "chrome"
    
    print("[self-check] learning.feedback OK - detection, taxonomy attribution, reward table, lesson creation")

