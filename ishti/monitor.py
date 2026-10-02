"""Live conversation monitoring for issues and mistakes (Section 3).

Passively monitors real chat/voice sessions for:
  - Misunderstood requests / corrections (owner had to correct or repeat)
  - Wrong-language responses (language mismatch against preference/input)
  - Silent skill failures / fell short responses

Logs each event to the correction-feedback log (data/corrections.jsonl and SQLite)
to provide high-quality training/tuning signals for periodic fine-tuning.
Strictly passive/logging only — NEVER auto-triggers self-healing.
"""
import difflib
import json
import os
import re
import sqlite3
import threading
import time

from ishti import CONFIG

try:  # best-effort failure log (never breaks the module if logging is unavailable)
    from ishti.core.logutil import swallowed
except Exception:  # pragma: no cover
    def swallowed(*_a, **_k):
        return None


DB_PATH = CONFIG.get("memory", {}).get(
    "db_path", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "ishti.db"))
CORRECTIONS_PATH = os.path.join(os.path.dirname(DB_PATH), "corrections.jsonl")
_lock = threading.Lock()

URDU_REGEX = re.compile(r"[\u0600-\u06FF]")
PASHTO_REGEX = re.compile(r"[ټځڅډړږښګڼۍې]")
CORRECTION_CUES = re.compile(
    r"\b(?:no[,.!]?\s+(?:that'?s\s+|it'?s\s+)?(?:wrong|incorrect|not)|"
    r"you\s+(?:misunderstood|got\s+(?:it\s+)?wrong|didn'?t\s+understand)|"
    r"i\s+(?:didn'?t\s+ask|asked\s+for|meant|said)|"
    r"that'?s\s+not\s+what\s+i|not\s+what\s+i\s+meant|wrong\s+answer|"
    r"why\s+(?:did|didn'?t)\s+you)\b", re.I
)


def _db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    c = sqlite3.connect(DB_PATH, timeout=10)
    c.execute("""
        CREATE TABLE IF NOT EXISTS monitoring_issues (
            ts REAL,
            category TEXT,
            request TEXT,
            reply TEXT,
            expected TEXT,
            details TEXT,
            session_id TEXT
        )
    """)
    return c


def record_issue(category: str, request: str, reply: str, expected: str, details: str = "", session_id: str = ""):
    """Log an observed issue/mistake passively without triggering any automatic healing."""
    now = time.time()
    try:
        with _lock, _db() as c:
            c.execute("INSERT INTO monitoring_issues VALUES (?, ?, ?, ?, ?, ?, ?)",
                      (now, category, request[:500], reply[:1000], expected[:500], details[:500], session_id or ""))
    except Exception as exc:
        print(f"[monitor] Database insert skipped: {exc}")

    # Append to corrections feedback log as training signal
    try:
        entry = {
            "timestamp": now,
            "category": category,
            "request": request[:500],
            "wrong_reply": reply[:600],
            "expected_behavior": expected[:500],
            "details": details,
            "type": "conversation_monitoring"
        }
        with _lock, open(CORRECTIONS_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as exc:
        print(f"[monitor] Feedback log append skipped: {exc}")

    print(f"[monitor] Logged issue ({category}): {details or expected[:60]}")


def inspect_turn(request: str, reply: str, skill: str, dec: dict, session: dict, channel: str = "text"):
    """Examine one completed turn for signs something went wrong."""
    if not request or not reply:
        return

    req_str = request.strip()
    rep_str = reply.strip()
    history = session.get("history") or []

    # 1. Misunderstood request / Owner repeated or corrected
    if len(history) >= 2:
        prev_user = None
        for m in reversed(history[:-1]):
            if m.get("role") == "user":
                prev_user = m.get("content", "").strip()
                break

        if prev_user:
            sim = difflib.SequenceMatcher(None, req_str.lower(), prev_user.lower()).ratio()
            # If owner repeats almost the exact same query (>= 88% similarity)
            if sim >= 0.88 and req_str.lower() != prev_user.lower():
                record_issue(
                    category="repeated_request",
                    request=req_str,
                    reply=rep_str,
                    expected=f"Clarification or direct answer to: {prev_user}",
                    details=f"Owner repeated request ({sim:.0%} match to previous turn)",
                    session_id=session.get("session_id", "")
                )

    if CORRECTION_CUES.search(req_str):
        record_issue(
            category="user_correction",
            request=req_str,
            reply=rep_str,
            expected="Correct behavior requested by owner in correction statement",
            details=f"Owner stated correction cue in turn: '{req_str[:80]}'",
            session_id=session.get("session_id", "")
        )

    # 2. Language mismatch
    lang_pref = session.get("prefers_language")
    try:
        from ishti.facts import get_facts
        lang_pref = lang_pref or get_facts().get("owner").get("prefers_language")
    except Exception as _exc:
        swallowed('ishti/monitor.py:inspect_turn', _exc)

    has_urdu_script = bool(URDU_REGEX.search(rep_str))
    req_has_urdu_script = bool(URDU_REGEX.search(req_str))

    if lang_pref == "English" and has_urdu_script:
        record_issue(
            category="language_mismatch",
            request=req_str,
            reply=rep_str,
            expected="English response",
            details="Preferred language is English, but response contained Urdu script",
            session_id=session.get("session_id", "")
        )
    elif lang_pref in ("Urdu", "Pashto") and not has_urdu_script and not req_has_urdu_script and len(rep_str.split()) > 6:
        # Expected Urdu/Pashto script but response is pure English
        record_issue(
            category="language_mismatch",
            request=req_str,
            reply=rep_str,
            expected=f"{lang_pref} script response",
            details=f"Preferred language is {lang_pref}, but response was in English",
            session_id=session.get("session_id", "")
        )

    # 3. Silent failure or fell short
    if rep_str.startswith(("The ", "I can't do that yet")) and " failed:" in rep_str[:80]:
        record_issue(
            category="skill_failure",
            request=req_str,
            reply=rep_str,
            expected=f"Successful execution of skill {skill}",
            details=f"Skill {skill} returned explicit failure: {rep_str[:80]}",
            session_id=session.get("session_id", "")
        )
    elif "Traceback (most recent call last)" in rep_str:
        record_issue(
            category="silent_exception",
            request=req_str,
            reply=rep_str,
            expected="Clean user-facing response without raw traceback",
            details=f"Skill {skill} leaked Python traceback to response",
            session_id=session.get("session_id", "")
        )


def get_issues(limit: int = 50) -> list[dict]:
    """Retrieve recent monitoring issues for inspection."""
    try:
        with _lock, _db() as c:
            rows = c.execute(
                "SELECT ts, category, request, reply, expected, details, session_id FROM monitoring_issues "
                "ORDER BY ts DESC LIMIT ?", (limit,)
            ).fetchall()
            return [
                {
                    "timestamp": ts,
                    "category": cat,
                    "request": req,
                    "reply": rep,
                    "expected": exp,
                    "details": det,
                    "session_id": sid
                } for ts, cat, req, rep, exp, det, sid in rows
            ]
    except Exception:
        return []


if __name__ == "__main__":
    import tempfile
    DB_PATH = os.path.join(tempfile.mkdtemp(), "test_mon.db")
    CORRECTIONS_PATH = os.path.join(tempfile.mkdtemp(), "test_corr.jsonl")

    # Test recording
    record_issue("test_category", "What is the capital of France?", "Berlin", "Paris", "Geography miss")
    issues = get_issues(5)
    assert len(issues) == 1
    assert issues[0]["category"] == "test_category"

    # Test inspection
    sess = {"history": [{"role": "user", "content": "What is the capital of France?"},
                        {"role": "assistant", "content": "Berlin"}]}
    inspect_turn("No, that's wrong, what is the capital of France?", "Paris", "chat", {}, sess)
    issues = get_issues(5)
    assert any(i["category"] == "user_correction" for i in issues)
    print("[self-check] monitor OK - passive conversation monitoring & feedback logging")
