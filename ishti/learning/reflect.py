"""Reflection: turn a turn's outcome into a sentence ISHTI can act on next time.

Reflexion (2303.11366) keeps a *verbal* reflection in an episodic buffer and re-reads it on
the next attempt, rather than only nudging a scalar. ExpeL (2308.10144) extracts transferable
insights from trajectories and recalls them at inference. Both are store-based: no weights.

Why this matters here: `lessons.adjust()` records *that* confidence moved, but a number cannot
tell the next turn what actually went wrong. A reflection names the failure, the request shape
it happened on, and the correction - so `guidance_for()` can retrieve it by words.
"""
import os
import re
from typing import Any, Dict, List, Optional

from ishti.learning import observe, store

# Reflexion keeps reflections short and about the *process*, not the answer.
MAX_REFLECTION = 240


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())[:MAX_REFLECTION]


def reflect(interaction=None, reward=None, feedback=None, lesson=None) -> str:
    """Compose one reflection sentence for a turn that did not go well (or did)."""
    fb = feedback or {}
    if isinstance(interaction, str):
        interaction = {"request": interaction}
    it = interaction or {}
    request = _clean(str(it.get("request") or ""))
    skill = str(it.get("skill") or "").strip()
    wrong = _clean(str(fb.get("incorrect") or it.get("incorrect") or ""))
    right = _clean(str(fb.get("correct") or it.get("correct") or ""))
    err = str(fb.get("error_type") or "").strip()
    score = reward

    if err and right:
        # the useful shape: what was wrong, what is right, and where it applies
        where = f" when {skill} handles \"{request[:60]}\"" if skill and request else ""
        return _clean(f"{err.replace('_', ' ')}{where}: used {wrong or 'the wrong choice'}, "
                      f"the owner wanted {right}.")
    if err:
        return _clean(f"{err.replace('_', ' ')} on \"{request[:60]}\" - the owner corrected it.")
    if score is not None:
        try:
            s = float(score)
        except (TypeError, ValueError):
            s = 0.0
        if s < 0:
            return _clean(f"That turn went wrong ({s:+.2f}) on \"{request[:60]}\" - do not repeat that path.")
        if s > 0:
            return _clean(f"That approach worked ({s:+.2f}) on \"{request[:60]}\" - keep doing it that way.")
    if lesson and lesson.get("statement"):
        return _clean(f"Confirmed: {lesson['statement']}")
    return _clean(f"Turn on \"{request[:70]}\" produced no usable signal.")


def remember(text: str, interaction_id: str = "", kind: str = "reflection",
             context: str = "", confidence: Optional[float] = None) -> Dict[str, Any]:
    """Persist a reflection in the lesson store so it is retrievable by words, not numbers.

    A reflection defaults to the *weak* band, not to zero: `guidance_for()` applies a confidence
    floor, so a 0.0 reflection would be stored and never recalled - the buffer would be write-only.
    At the floor a reflection is retrievable on an exact word match and invisible otherwise, which
    is the intended behaviour: a record of what happened, recalled when the same thing comes round.
    It then earns (or loses) influence the same way any other lesson does, through `attribute()`.
    """
    statement = _clean(text)
    if not statement:
        return {}
    if confidence is None:
        confidence = float(store.cfg().get("thresholds", {}).get("weak", 0.3))
    try:
        from ishti.learning import lessons
        return lessons.add(kind="procedural_lesson", statement=statement, scope="task",
                           context=context or kind, source="monitor", confidence=confidence,
                           note=f"reflection of interaction {interaction_id}" if interaction_id else "reflection")
    except Exception:
        return {}


def for_turn(interaction, reward=None, feedback=None, keep=True) -> Dict[str, Any]:
    """Reflect on a turn and (by default) store it. Returns {text, lesson}."""
    text = reflect(interaction, reward, feedback)
    if not text or not keep:
        return {"text": text, "lesson": {}}
    iid = interaction.get("id") if isinstance(interaction, dict) else ""
    lesson = remember(text, interaction_id=iid or "", kind="reflection")
    return {"text": text, "lesson": lesson}


# --- insight mining across trajectories (ExpeL 2308.10144: transferable insights, not task memory) -----

_INSIGHT = re.compile(
    r"\b(?:next time|in future|instead|always|never|from now on|going forward|remember to|"
    r"make sure (?:to|you)|should have|learn(?:ed|t) that|the issue was|went wrong)\b", re.I)


def mine_insights(limit=50, min_words=4) -> List[Dict[str, Any]]:
    """Pull transferable insights out of stored interactions, de-instantiated from the task.

    ExpeL's core observation: what transfers between tasks is the *procedure*, not the task
    memory. A stored lesson like "use Chrome for work browsing" is worth carrying; "for the
    Tuesday deploy I used Chrome" is not. So an insight is only kept when it reads as a rule
    rather than a log line, and the concrete request it came from is recorded as provenance
    instead of being embedded in the statement.
    """
    from ishti.learning import lessons
    out = []
    for row in store.rows("SELECT * FROM interactions WHERE result != '' ORDER BY ts DESC LIMIT ?",
                          (int(limit),)):
        text = str(row.get("result") or "")[:400]
        if not _INSIGHT.search(text):
            continue
        statement = _clean(text)
        if len(statement.split()) < min_words:
            continue
        out.append({
            "statement": statement,
            "context": str(row.get("skill") or row.get("domain") or ""),
            "provenance": {"interaction_id": row.get("id"), "request": str(row.get("request") or "")[:120],
                           "skill": row.get("skill"), "ts": row.get("ts")},
        })
    return out


def promote_insights(limit=50, confidence=0.3) -> List[Dict[str, Any]]:
    """Turn mined insights into low-confidence lessons, de-instantiated and provenance-tracked.

    They start weak on purpose: an inferred rule has not been confirmed by the owner, so it
    must earn its place through `attribute()`/`confirm()` like anything else. Injection guard
    still applies - `lessons.add()` refuses external sources.
    """
    from ishti.learning import lessons
    made = []
    for insight in mine_insights(limit):
        lesson = lessons.add(
            kind="procedural_lesson", statement=insight["statement"], scope="domain",
            context=insight["context"], source="monitor", confidence=confidence,
            note=f"mined insight from interaction {insight['provenance']['interaction_id']}")
        if lesson:
            made.append(lesson)
    return made


if __name__ == "__main__":
    import tempfile
    store.set_db_path(os.path.join(tempfile.mkdtemp(), "refl.db"))

    fb = {"error_type": "wrong_application", "incorrect": "edge", "correct": "chrome"}
    it = {"request": "open the work site", "skill": "browser", "id": "it_1"}
    text = reflect(it, -0.8, fb)
    assert "wrong application" in text and "chrome" in text, text
    assert "open the work site" in text, text

    assert "do not repeat" in reflect({"request": "x"}, -0.5)
    assert "keep doing it" in reflect({"request": "x"}, 0.9)
    assert reflect({}, None, None)  # never raises on empty input

    got = for_turn(it, -0.8, fb)
    assert got["lesson"] and got["lesson"]["statement"] == text
    assert got["lesson"]["confidence"] == float(store.cfg()["thresholds"]["weak"]), got["lesson"]["confidence"]

    # a stored reflection is retrievable by the words it contains
    from ishti.learning import lessons
    assert any("chrome" in l["statement"] for l in lessons.guidance_for("open the work site", limit=5))

    # for_turn(keep=False) is a dry run: no store growth
    before = len(lessons.active(limit=500))
    for_turn({"request": "dry run"}, -0.5, keep=False)
    assert len(lessons.active(limit=500)) == before

    # --- insight mining (ExpeL): transferable rules, not log lines ---
    assert mine_insights() == []  # nothing stored yet
    iid = observe.turn("deploy", "Next time check the staging URL before promoting", skill="dev")
    found = mine_insights()
    assert len(found) == 1 and "staging" in found[0]["statement"], found
    assert found[0]["provenance"]["interaction_id"] == iid, found
    assert found[0]["provenance"]["request"] == "deploy", found

    observe.turn("plain", "Just some ordinary output with no rule in it", skill="chat")
    assert len(mine_insights()) == 1  # a log line is not an insight

    made = promote_insights()
    assert len(made) == 1 and made[0]["confidence"] == 0.3, made
    assert made[0]["note"].startswith("mined insight from interaction"), made[0]["note"]

    print("[self-check] learning.reflect OK - verbal reflections, process-level wording, "
          "retrievable by words, insight mining with provenance, dry-run mode")
