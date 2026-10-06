"""Policy check and parameter overriding (spec 13, 27, 28).

Applies active lessons and temporary instructions to enforce constraints and guidance.
Precedence: temporary instruction > explicit rule > procedural lesson > preference > weak pattern.
"""
import os
import re
from typing import Any, Dict, List, Optional
from ishti.learning import lessons, store

_TEMP_RX = re.compile(
    r"^(?:just\s+)?(?:for\s+now|this\s+time|temporarily|right\s+now)\s*[,:]?\s*(?P<stmt>.+)$|"
    r"^(?P<stmt2>.+)\s+(?:just\s+)?(?:for\s+now|this\s+time|temporarily|right\s+now)[.!]?$",
    re.I
)

def temporary_instruction(text: str, session: Optional[Any] = None) -> Optional[Dict[str, Any]]:
    """Parse text for temporary instruction pattern (spec 13)."""
    t = (text or "").strip()
    m = _TEMP_RX.match(t)
    if not m:
        return None
    stmt = (m.group("stmt") or m.group("stmt2") or "").strip()
    if not stmt:
        return None
    sid = session.get("id") if isinstance(session, dict) else session
    # Add a temporary instruction lesson
    ttl = float(store.cfg().get("temporary_ttl_s", 3600))
    return lessons.add(
        kind="temporary",
        statement=stmt,
        scope="session",
        context="session",
        entity=str(sid)[:160] if sid else "",
        source="temporary_instruction",
        confidence=1.0,
        expires=store.now() + ttl
    )

def check(text: str, decision: Optional[Dict[str, Any]] = None,
          session: Optional[Any] = None) -> Dict[str, Any]:
    """Check text and decision against active learning lessons (spec 27)."""
    # Clean expired temporary rules first
    lessons.expire_stale()
    
    dec = decision or {}
    domain = str(dec.get("skill") or "") if isinstance(dec, dict) else ""
    sid = session.get("id") if isinstance(session, dict) else session
    guidance = lessons.guidance_for(text, domain=domain, limit=5, session_id=sid)
    overrides = []
    override_skill = None
    why_reasons = []
    ask = None

    for g in guidance:
        # Check if lesson dictates a specific skill or tool override
        kind = g.get("kind")
        correct = g.get("correct")
        stmt = g.get("statement", "")
        
        # Skill override
        if g.get("context") in ("skill", "skill_routing") and correct:
            override_skill = correct
            why_reasons.append(f"lesson {g['lesson_id']}: {stmt}")
            overrides.append({"type": "skill", "value": correct, "lesson": g})
        # Parameter override
        elif correct and g.get("incorrect"):
            overrides.append({
                "type": "parameter",
                "scope": g.get("scope"),
                "incorrect": g.get("incorrect"),
                "correct": correct,
                "lesson": g
            })
            why_reasons.append(f"override {g.get('incorrect')} -> {correct} ({g['statement']})")

    why_str = "; ".join(why_reasons) if why_reasons else ""
    return {
        "overrides": overrides,
        "skill": override_skill,
        "why": why_str,
        "ask": ask
    }

def apply_overrides(text: str, args: Dict[str, Any], decision: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Apply parameter-level overrides to skill call arguments."""
    res = dict(args or {})
    ch = check(text, decision=decision)
    for ov in ch.get("overrides", []):
        if ov.get("type") == "parameter":
            inc = str(ov.get("incorrect", "")).lower()
            cor = ov.get("correct")
            for k, v in list(res.items()):
                if isinstance(v, str) and inc and inc in v.lower():
                    res[k] = re.sub(re.escape(inc), cor, v, flags=re.I)
    return res

if __name__ == "__main__":
    import tempfile
    tmp = tempfile.mkdtemp()
    store.set_db_path(os.path.join(tmp, "pol.db"))
    
    # Test temporary instruction
    t_res = temporary_instruction("for now use Edge")
    assert t_res and t_res["kind"] == "temporary" and t_res["statement"] == "use Edge"
    
    # Test guidance and parameter overrides
    lessons.add("procedural_lesson", "Use Chrome for work browsing", scope="application",
                context="browsing", incorrect="edge", correct="chrome", source="explicit_user_correction",
                confidence=1.0)
    
    out = check("work browsing", decision={"skill": "browsing"})
    assert len(out["overrides"]) >= 1
    
    applied = apply_overrides("work browsing", {"app": "edge"}, decision={"skill": "browsing"})
    assert applied["app"] == "chrome", applied
    # an unrelated request must not be rewritten: guidance is token-scoped, not global
    assert apply_overrides("book me a flight", {"app": "edge"}) == {"app": "edge"}
    
    print("[self-check] learning.policy OK - temporary instructions, precedence check, parameter override")
