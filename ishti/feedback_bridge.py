"""Bridge: v2 chat thumbs (runs.add_feedback) -> the learning system.

Before this, a rating only landed in runs.db and never reached lessons/rewards/corrections, so ISHTI could not
learn from it. Now every stored rating is turned into a typed learning record (error taxonomy from the chosen reasons),
a dislike also produces a text-only revised answer posted into the same chat (the owner can rate again, and keep
going until a thumbs-up), and an explicit correction text becomes a high-confidence lesson.
"""
from __future__ import annotations

import threading

from ishti.core.logutil import swallowed

# UI reason chip -> learning error taxonomy (ishti/learning/errors.py)
_REASON_TYPE = {"wrong tool": "wrong_application", "wrong fact": "wrong_fact", "wrong facts": "wrong_fact",
                "too long": "style_preference", "too short": "style_preference", "ignored context": "wrong_interpretation",
                "didn't do it": "incomplete_action", "did not do it": "incomplete_action"}


def error_type(reasons, comment: str, correction: str) -> str:
    for r in reasons or []:
        t = _REASON_TYPE.get(str(r).strip().lower())
        if t:
            return t
    return "owner_feedback" if (comment or correction) else "wrong_interpretation"


def _answer_and_request(run_id: str, message_id: str):
    from ishti import runs
    run = runs.get_run(run_id) if run_id else None
    chat_id = (run or {}).get("chat_id")
    msgs = runs.messages_after(chat_id, 0, limit=2000) if chat_id else []
    idx = next((i for i, m in enumerate(msgs) if m["message_id"] == message_id), -1)
    if idx < 0 and run:  # fall back to the run's own assistant row
        idx = next((i for i, m in enumerate(msgs) if m["role"] == "assistant" and m["run_id"] == run_id), -1)
    if idx < 0:
        return chat_id, None, "", []
    answer = msgs[idx]
    request = next((m["text"] for m in reversed(msgs[:idx]) if m["role"] == "user"), "")
    context = [{"role": m["role"], "content": m["text"][:1200]} for m in msgs[max(0, idx - 8):idx]]
    return chat_id, answer, request, context


def record(message_id: str, run_id: str, rating: int, reasons=None, comment: str = "", correction: str = "") -> dict:
    """Synchronous learning write; returns {recorded, revised}. Never raises."""
    out = {"recorded": False, "revised": False}
    try:
        from ishti.learning import feedback
        chat_id, answer, request, context = _answer_and_request(run_id, message_id)
        if not answer:
            return out
        neg = rating < 0
        etype = error_type(reasons, comment, correction) if neg else ""
        note = "; ".join(filter(None, [", ".join(map(str, reasons or [])), comment]))[:900]
        fb = {"kind": "explicit_correction" if (neg and correction) else
                      ("negative_reaction" if neg else "positive_confirmation"),
              "polarity": "negative" if neg else "positive",
              "message": note or ("Owner disliked this exact answer" if neg else "Owner liked this exact answer"),
              "target": "chat", "error_type": etype,
              "incorrect": answer["text"][:600] if neg else "", "correct": correction[:600] if neg else "",
              "source": "owner_button", "confidence": 1.0 if (note or correction) else 0.8}
        feedback.apply(fb, interaction={"id": message_id, "request": request, "skill": "chat"})
        out["recorded"] = True
        if neg and rating < 0:
            out["revised"] = _revise(chat_id, run_id, answer, request, context, note or correction)
    except Exception as exc:
        swallowed("ishti/feedback_bridge.py:record", exc)
    return out


def _revise(chat_id: str, run_id: str, answer: dict, request: str, context: list, reason: str) -> bool:
    from ishti import answer_feedback, runs
    try:
        run = runs.get_run(run_id) or {}
        steps = run.get("steps") or []
        evidence = {"content": answer["text"], "sources": [u for st in steps for u in (st.get("sources") or [])][:6]}
        if steps:  # what the tools actually did for this answer: the review may rely on it, nothing else
            context = context + [{"role": "tool_trace", "content": "; ".join(str(st.get("label")) for st in steps)[:1200]}]
        issue, revised = answer_feedback._review(request, evidence, context, reason)
    except Exception as exc:  # honest: no fabricated revision when the local model cannot produce one
        swallowed("ishti/feedback_bridge.py:_revise", exc)
        return False
    runs.add_assistant_message(chat_id, f"**Revised answer** (rate it again - I'll keep correcting until it is right)\n\n{revised}",
                               run_id=run_id)
    return True


def record_async(*a, **k) -> None:
    threading.Thread(target=record, args=a, kwargs=k, name="ishti-feedback-bridge", daemon=True).start()


if __name__ == "__main__":
    assert error_type(["Wrong tool"], "", "") == "wrong_application"
    assert error_type([], "", "use chrome") == "owner_feedback" and error_type([], "", "") == "wrong_interpretation"
    assert record("nope", "nope", -1)["recorded"] is False  # unknown message: no crash, nothing recorded
    print("[self-check] feedback_bridge OK")


# ---------------------------------------------------------------- auto skill creation (trajectory mining)

TRAJ_MIN = 3  # same multi-skill sequence this many times -> propose a skill (owner approves; skill_creator builds it DISABLED)


def trajectory(request: str, skills_used: list, ok: bool = True) -> dict | None:
    """Count repeated multi-step skill sequences from real runs; at TRAJ_MIN open ONE proposal. Never raises."""
    try:
        import json
        from ishti import runs
        seq = [s for i, s in enumerate(skills_used) if s and s != "chat" and (i == 0 or s != skills_used[i - 1])]
        if not ok or len(seq) < 2 or runs.learning_mode() == "off":
            return None
        sig = "+".join(seq)
        key = f"traj:{sig}"
        with runs._lock:
            c = runs.conn()
            row = c.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
            rec = json.loads(row["value"]) if row else {"count": 0, "examples": [], "proposed": False}
            rec["count"] += 1
            rec["examples"] = (rec["examples"] + [str(request)[:120]])[-3:]
            fire = rec["count"] >= TRAJ_MIN and not rec["proposed"]
            rec["proposed"] = rec["proposed"] or fire
            c.execute("INSERT INTO meta(key, value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                      (key, json.dumps(rec, ensure_ascii=False)))
            c.commit()
        if fire:
            return runs.add_proposal(f"Automate: {' -> '.join(seq)} (e.g. \"{rec['examples'][-1]}\")", skill=sig,
                                     origin="trajectory")
    except Exception as exc:
        swallowed("ishti/feedback_bridge.py:trajectory", exc)
    return None


def build_from_proposal(pid: str) -> None:
    """Owner approved a proposal: draft the skill with skill_creator in the background (saved DISABLED; enabling is a
    separate owner yes). Result/error is published as a learning_update."""
    def work():
        from ishti import runs
        try:
            p = next((x for x in runs.list_proposals() if x["id"] == pid), None)
            if not p:
                return
            from ishti.skills.skill_creator import skill as sc
            res = sc.build(f"create a skill that handles: {p['title']}", None)
            runs.publish("learning_update", {"kind": "skill_built", "detail": {"id": pid, "result": str(res)[:300]}})
        except Exception as exc:
            swallowed("ishti/feedback_bridge.py:build_from_proposal", exc)
            from ishti import runs as _r
            _r.publish("learning_update", {"kind": "skill_build_failed", "detail": {"id": pid, "error": str(exc)[:200]}})
    threading.Thread(target=work, name="ishti-skill-build", daemon=True).start()
