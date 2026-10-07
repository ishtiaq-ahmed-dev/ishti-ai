"""Feedback detection, attribution, and reinforcement reward assignment (spec 5, 6, 7, 16).

Detects explicit corrections ("no use Chrome", "that was wrong"), positive confirmations,
negative sentiment, and updates lessons, mistake records, and scalar rewards.
"""
import hashlib
import os
import re
from typing import Any, Dict, Optional

from ishti.learning import errors, lessons, store

KINDS = ("explicit_correction", "implicit_correction", "positive_confirmation", "negative_reaction")
REASON_TYPES = (
    "wrong_tool", "wrong_fact", "too_long", "ignored_context", "hallucinated_action",
    "wrong_interpretation", "wrong_parameter", "wrong_person", "wrong_file", "wrong_sequence",
    "wrong_permission", "language_mismatch", "tool_failure", "unknown",
)

_REASON_PATTERNS = (
    ("hallucinated_action", re.compile(r"\b(?:claim(?:ed|ing)?|said)\b.{0,50}\b(?:sent|deleted|uploaded|saved|changed|booked|ran|opened)|\b(?:did not|didn't|never)\b.{0,35}\b(?:send|delete|upload|save|change|book|run|open)", re.I)),
    ("too_long", re.compile(r"\b(?:too long|verbose|wordy|shorter|too much detail|rambl)", re.I)),
    ("ignored_context", re.compile(r"\b(?:ignored?|missed|forgot)\b.{0,35}\b(?:context|instruction|earlier|previous|said|asked)", re.I)),
    ("wrong_tool", re.compile(r"\b(?:wrong (?:tool|skill|route)|should (?:have )?(?:use|used)|use .{1,30} instead|search(?:ed)? the web instead)", re.I)),
    ("wrong_fact", re.compile(r"\b(?:wrong fact|factually|invented|made (?:that|it) up|not true|false|wrong (?:amount|date|name|number|value))\b", re.I)),
)

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


def normalize_prompt(prompt: str) -> str:
    """Conservative exact-match key: case/spacing/punctuation vary, words do not."""
    return " ".join(re.findall(r"[^\W_]+|\d+", str(prompt or "").casefold(), re.UNICODE))[:1000]


def _prompt_signature(prompt: str) -> str:
    return hashlib.sha256(normalize_prompt(prompt).encode("utf-8")).hexdigest()[:24]


def classify_reason(reason: str = "", error_type: str = "", rating: str = "negative") -> str:
    """Map owner wording and legacy error names into the stable feedback taxonomy."""
    legacy = {"wrong_skill": "wrong_tool", "wrong_application": "wrong_tool",
              "wrong_assumption": "wrong_fact", "owner_feedback": "wrong_interpretation"}
    kind = legacy.get(str(error_type or ""), str(error_type or ""))
    if kind in REASON_TYPES:
        return kind
    for name, pattern in _REASON_PATTERNS:
        if pattern.search(str(reason or "")):
            return name
    return "unknown" if rating == "positive" else "wrong_interpretation"


def _preferred_skill(reason: str = "", explicit: str = "") -> str:
    candidate = str(explicit or "").strip().lower().replace(" ", "_")
    if re.fullmatch(r"[a-z][a-z0-9_]{1,50}", candidate):
        return candidate
    classified = errors.classify(str(reason or ""))
    candidate = str((classified or {}).get("correct") or "").strip().lower().replace(" ", "_")
    if candidate in errors.SKILLS or candidate.endswith("_search"):
        return candidate
    low = str(reason or "").lower()
    if re.search(r"\b(?:use|used|route|routing|skill|tool|instead|search)\b", low):
        for name in sorted(errors.SKILLS, key=len, reverse=True):
            if re.search(rf"\b{re.escape(name)}\b|\b{re.escape(name.replace('_', ' '))}\b", low):
                return name
        direct = re.search(r"\b(?:use|used|choose|route(?:d)? to)\s+(?:the\s+)?([a-z][a-z0-9_]{1,50})(?:\s+skill)?\b", low)
        if direct:
            return direct.group(1)
    return ""


def _upsert_routing_hint(prompt: str, preferred_skill: str, rejected_skill: str = "",
                         client_id: str = "", source: str = "owner_feedback") -> bool:
    normalized = normalize_prompt(prompt)
    if not normalized or not preferred_skill:
        return False
    sig = _prompt_signature(normalized)
    return bool(store.write(
        "INSERT INTO routing_hints(signature,client_id,prompt,preferred_skill,rejected_skill,evidence,confidence,source,updated,active) "
        "VALUES(?,?,?,?,?,1,1.0,?,?,1) ON CONFLICT(signature,client_id) DO UPDATE SET "
        "preferred_skill=excluded.preferred_skill,rejected_skill=excluded.rejected_skill,evidence=routing_hints.evidence+1,"
        "confidence=1.0,source=excluded.source,updated=excluded.updated,active=1",
        (sig, str(client_id or ""), normalized, preferred_skill, rejected_skill, source, store.now())))


def routing_hint(prompt: str, client_id: str = "") -> Optional[str]:
    """Return an owner-corrected skill only for the same safely-normalized prompt."""
    sig = _prompt_signature(prompt)
    scopes = [str(client_id or "")]
    if client_id:
        scopes.append("")  # owner-global hints are safe; another client's hints are never considered
    for scope in scopes:
        row = store.row("SELECT preferred_skill FROM routing_hints WHERE signature=? AND client_id=? "
                        "AND active=1 ORDER BY updated DESC LIMIT 1", (sig, scope))
        if row and row.get("preferred_skill"):
            return str(row["preferred_skill"])
    return None


def record_typed(*, prompt: str, route: Any = None, skill: str = "", answer: str = "",
                 rating: str, reason: str = "", reason_type: str = "", revised_answer: str = "",
                 session_id: str = "", message_id: str = "", interaction_id: str = "",
                 client_id: str = "", preferred_skill: str = "", source: str = "owner") -> Dict[str, Any]:
    """Persist one complete, typed owner signal and immediately expose safe routing corrections."""
    if rating not in {"positive", "negative", "correction"}:
        raise ValueError("rating must be positive, negative, or correction")
    interaction = None
    if interaction_id:
        interaction = store.row("SELECT * FROM interactions WHERE id=?", (interaction_id,))
    if interaction is None and session_id and message_id:
        interaction = store.row("SELECT * FROM interactions WHERE session_id=? AND message_id=? ORDER BY ts DESC LIMIT 1",
                                (session_id, message_id))
    interaction = interaction or {}
    prompt = str(prompt or interaction.get("request") or "")[:4000]
    route = route if route is not None else store.js(interaction.get("decision"), {})
    skill = str(skill or interaction.get("skill") or "")[:120]
    answer = str(answer or interaction.get("result") or "")[:12000]
    reason_type = classify_reason(reason, reason_type, rating)
    preferred_skill = _preferred_skill(reason, preferred_skill)
    row_id = store.add(
        "typed_feedback", ts=store.now(), session_id=str(session_id or ""), client_id=str(client_id or ""),
        message_id=str(message_id or ""), interaction_id=str(interaction_id or interaction.get("id") or ""),
        prompt=prompt, route=store.dump(route or {}), skill=skill, answer=answer, rating=rating,
        reason=str(reason or "")[:2000], reason_type=reason_type, revised_answer=str(revised_answer or "")[:12000],
        status="accepted" if rating == "positive" else "rejected", preferred_skill=preferred_skill,
        source=str(source or "owner")[:80])
    if not row_id:
        return {}
    if rating in {"negative", "correction"} and reason_type == "wrong_tool" and preferred_skill:
        _upsert_routing_hint(prompt, preferred_skill, rejected_skill=skill, client_id=client_id, source=source)
    return store.row("SELECT * FROM typed_feedback WHERE id=?", (row_id,)) or {}


def accept_preference_pair(*, prompt: str, route: Any, skill: str, rejected: str, accepted: str,
                           reason_type: str, accepted_message_id: str, client_id: str = "",
                           source_feedback_id: Optional[int] = None) -> Dict[str, Any]:
    """Store the final owner-accepted answer against the immediately rejected predecessor."""
    if not prompt or not rejected or not accepted or not accepted_message_id:
        return {}
    if source_feedback_id is None:
        prior = store.row("SELECT id FROM typed_feedback WHERE client_id=? AND prompt=? "
                          "AND rating IN ('negative','correction') ORDER BY ts DESC LIMIT 1",
                          (str(client_id or ""), str(prompt)[:4000]))
        source_feedback_id = (prior or {}).get("id")
    store.write(
        "INSERT OR IGNORE INTO preference_pairs(ts,client_id,prompt,route,skill,rejected,accepted,reason_type,"
        "source_feedback_id,accepted_message_id,exported) VALUES(?,?,?,?,?,?,?,?,?,?,0)",
        (store.now(), str(client_id or ""), str(prompt)[:4000], store.dump(route or {}), str(skill or "")[:120],
         str(rejected)[:12000], str(accepted)[:12000], classify_reason("", reason_type), source_feedback_id,
         str(accepted_message_id)))
    return store.row("SELECT * FROM preference_pairs WHERE accepted_message_id=?", (str(accepted_message_id),)) or {}

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
            "source": "explicit_user_correction", "prompt": str(last_request or "")
        }
    return None

def from_correction(text: str, wrong: str, right: str, session: Optional[Any] = None) -> Dict[str, Any]:
    """Build feedback directly from an explicit skill routing correction."""
    return {
        "kind": "explicit_correction", "polarity": "negative", "error_type": "wrong_skill",
        "incorrect": wrong, "correct": right, "message": text, "target": "skill_routing",
        "confidence": 1.0, "source": "explicit_user_correction", "prompt": text
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

        typed = record_typed(
            prompt=fb.get("prompt") or (interaction or {}).get("request") or fb.get("message") or "",
            route=(interaction or {}).get("route") or (interaction or {}).get("decision") or {},
            skill=(interaction or {}).get("skill") or "", answer=(interaction or {}).get("answer") or
            (interaction or {}).get("result") or "", rating="correction", reason=fb.get("message") or "",
            reason_type=err_type, interaction_id=iid, client_id=(interaction or {}).get("client_id") or "",
            preferred_skill=right if err_type == "wrong_skill" else "", source=fb.get("source") or "owner")
        if typed:
            effects.append("recorded_typed_feedback")

    return {"effects": effects, "lesson": lesson_out, "ask": None}


def apply_answer_rating(session_id: str, message_id: str, rating: str) -> bool:
    """Attribute one owner's exact answer rating to its original interaction.

    A background turn observation may finish before or after the owner clicks a
    button. Both paths call this method; the reward transition is idempotent.
    """
    if rating not in {"positive", "negative"} or not message_id:
        return False
    from ishti.learning import observe
    target_reward = 1.0 if rating == "positive" else -1.0
    with store.LOCK:
        row = store.row("SELECT id, request, skill, reward FROM interactions WHERE session_id=? AND message_id=? "
                        "ORDER BY ts DESC LIMIT 1", (session_id, message_id))
        if not row:
            return False
        if float(row["reward"] or 0) == target_reward:
            return True
        iid = row["id"]
        observe.end(iid, reward=target_reward)
        if row["skill"] in lessons.CONTEXT_INJECTING_SKILLS:
            stored = store.js(row.get("decision"), {}) or {}
            applied = [str(i) for i in stored.get("applied_lesson_ids", []) if isinstance(i, str)]
            # Compatibility for callers that predate exact lesson snapshots; live dispatcher turns
            # always persist applied_lesson_ids before feedback can arrive.
            if "applied_lesson_ids" not in stored and not stored:
                applied = [item["lesson_id"] for item in lessons.guidance_for(row["request"], limit=6)]
            if applied:
                lessons.apply_turn_outcome(iid, target_reward, applied, reason="owner-rated exact answer")
        return True

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
