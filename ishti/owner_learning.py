"""Learn about the owner from real use, cheaply and visibly.

After each answered turn: (1) count signals - language/script, time of day, which skills get asked for - and when a signal is
stable write ONE typed, owner-visible fact (provenance OBSERVED); (2) every INFER_EVERY turns the planner model reads the
recent owner messages and may add up to 3 INFERRED preferences (tone/language/habit). Facts feed the prompt (see
orchestrator/model_router.py -> behavior_profiles.inference_facts), are listed in Learning > About you, and can be deleted.
Never stores message text, client data, or anything about third parties. Respects learning mode 'off'.
"""
from __future__ import annotations

import json
import re
import time

from ishti.core.logutil import swallowed

THRESHOLDS = {"language": 3, "time_habit": 6, "frequent_task": 5}
INFER_EVERY = 30
_URDU_SCRIPT = re.compile(r"[؀-ۿ]")
_PASHTO_ONLY = re.compile(r"[ټځڅډړږښڼۍېګ]")
_ROMAN_URDU = re.compile(r"\b(?:kya|hai|nahi|kaise|mujhe|mera|meri|batao|karo|kar|do|acha|theek|aap|tum|yeh|woh|kahan|kab)\b", re.I)


def _language(text: str) -> str:
    if _PASHTO_ONLY.search(text):
        return "Pashto"
    if _URDU_SCRIPT.search(text):
        return "Urdu"
    if len(_ROMAN_URDU.findall(text)) >= 2:
        return "Urdu (written in English letters)"
    return "English" if re.search(r"[A-Za-z]{3,}", text) else ""


def _bucket(ts: float) -> str:
    h = time.localtime(ts).tm_hour
    return "morning" if 5 <= h < 12 else "afternoon" if 12 <= h < 17 else "evening" if 17 <= h < 22 else "late night"


def _bump(key: str) -> int:
    from ishti import runs
    with runs._lock:
        c = runs.conn()
        row = c.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        n = (int(row["value"]) if row else 0) + 1
        c.execute("INSERT INTO meta(key, value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, str(n)))
        c.commit()
    return n


def _remember(fact_type: str, value: str, source: str, confidence: float, reference: str = "") -> bool:
    """Write the fact once (same type+value never duplicated). Returns True when newly written."""
    from ishti import behavior_profiles as bp
    if any(f["fact_type"] == fact_type and f["value"].lower() == value.lower() for f in bp.owner_visible_facts()):
        return False
    bp.record_fact(fact_type, value, bp.Provenance(bp.ProvenanceType(source), reference, confidence))
    return True


def observe_turn(request: str, skills_used: list, ts: float | None = None) -> list[str]:
    """Count signals for one answered turn. Returns the facts newly written (usually none). Never raises."""
    written: list[str] = []
    try:
        from ishti import runs
        if runs.learning_mode() == "off":
            return written
        ts = ts or time.time()
        lang = _language(request)
        if lang:
            n = _bump(f"obs:language:{lang}")
            if n == THRESHOLDS["language"] and _remember("language", f"Usually writes in {lang}", "observed", 0.7, f"{n} messages"):
                written.append(f"language:{lang}")
        b = _bucket(ts)
        n = _bump(f"obs:time:{b}")
        if n == THRESHOLDS["time_habit"] and _remember("time_habit", f"Most active in the {b}", "observed", 0.6, f"{n} messages"):
            written.append(f"time:{b}")
        for s in {x for x in skills_used if x and x != "chat"}:
            n = _bump(f"obs:skill:{s}")
            if n == THRESHOLDS["frequent_task"] and _remember("frequent_task", f"Often asks for the {s} skill", "observed", 0.7, f"{n} uses"):
                written.append(f"task:{s}")
        total = _bump("obs:turns")
        if total % INFER_EVERY == 0:
            written += infer_preferences()
    except Exception as exc:
        swallowed("ishti/owner_learning.py:observe_turn", exc)
    return written


def infer_preferences(llm_fn=None) -> list[str]:
    """Planner pass over the last owner messages (personal chats only): up to 3 INFERRED tone/habit preferences."""
    import os
    if llm_fn is None and os.environ.get("ISHTI_TEST_MODE") and not os.environ.get("ISHTI_ROUTE_LLM"):
        return []
    out: list[str] = []
    try:
        from ishti import runs
        with runs._lock:
            rows = runs.conn().execute(
                "SELECT m.text FROM messages m JOIN chats c ON c.chat_id = m.chat_id "
                "WHERE m.role = 'user' AND c.profile = 'personal' ORDER BY m.seq DESC LIMIT 25").fetchall()
        texts = [r["text"][:200] for r in rows]
        if len(texts) < 10:
            return out
        if llm_fn is None:
            from ishti import llm
            llm_fn = lambda prompt: llm.ask(prompt, system="You infer stable user preferences. JSON only.",  # noqa: E731
                                            role="planner", temperature=0.1, max_tokens=300)
        raw = llm_fn("Owner messages (newest first):\n- " + "\n- ".join(texts) +
                     '\n\nInfer at most 3 STABLE preferences about HOW this person likes to be answered or works '
                     '(tone, length, language, habits). Not facts about other people. JSON: '
                     '{"preferences":[{"type":"tone|language|time_habit|frequent_task","value":"short sentence"}]}')
        data = json.loads(re.search(r"\{.*\}", raw or "", re.S).group(0))
        for p in (data.get("preferences") or [])[:3]:
            t, v = str(p.get("type", "")), str(p.get("value", "")).strip()[:160]
            if t in ("tone", "language", "time_habit", "frequent_task") and len(v) > 5 and _remember(t, v, "inferred", 0.5, "model inference"):
                out.append(f"{t}:{v}")
    except Exception as exc:
        swallowed("ishti/owner_learning.py:infer_preferences", exc)
    return out


if __name__ == "__main__":
    assert _language("mujhe batao mera project kahan hai") .startswith("Urdu")
    assert _language("what is the weather") == "English" and _language("آپ کیسے ہیں") == "Urdu"
    assert _bucket(time.mktime((2026, 1, 1, 9, 0, 0, 0, 0, -1))) == "morning"
    print("[self-check] owner_learning OK")
