"""Wiring test for the agentic learning loop (docs/LEARNING_INTERFACE.md section 6b).

Covers the pieces a per-module self-check cannot see, because they only exist across module
boundaries:

1. Lessons actually reach a model prompt (`chat.build_context` -> `lessons.context_for`)
2. The dispatcher turn hook runs and logs an interaction
3. Attribution is gated on `lessons.CONTEXT_INJECTING_SKILLS`: a skill that never showed the
   lesson to the model must not be able to move its confidence
4. `dashboard.reflections()` returns the payload the /api/learning/reflections endpoint serves
5. A correction supersedes the older rule (last-writer-wins) and the reflection stays recallable
6. A lesson below the confidence floor cannot steer a reply
7. `maybe_decay()` throttles itself instead of scanning the table on every turn
8. `dashboard.promote_insights()` returns the shape /api/learning/reflections/mine serves

Uses a throwaway DB; never touches data\\learning.db.
"""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from ishti.learning import dashboard, lessons, store  # noqa: E402

store.set_db_path(os.path.join(tempfile.mkdtemp(), "wiring.db"))


def dump(label: str) -> None:
    rows = store.rows("SELECT status, kind, source, confidence, context, statement FROM lessons "
                      "ORDER BY updated DESC")
    print(f"--- {label}: {len(rows)} lesson(s)")
    for r in rows:
        print(f"    {r['status']:<10} {r['kind']:<20} {r['source']:<24} {r['confidence']:.2f} "
              f"{r['context']:<14} {r['statement'][:60]}")


def test_wiring() -> None:
    # 1. An owner rule must reach the prompt builder, or every lesson ever learned is write-only.
    lesson = lessons.add("rule", "Open work browsing in Chrome", scope="global", context="browser",
                         source="owner_statement", confidence=0.95, correct="chrome")
    assert lesson, "lesson not created"
    from ishti.skills.chat.skill import build_context
    ctx = build_context("open the work site")
    assert "LEARNED FROM THE OWNER" in ctx, f"lessons not injected into prompt:\n{ctx}"
    print("[1] lessons reach the model's prompt")

    # 2. The dispatcher turn hook runs and logs.
    from ishti import dispatcher
    dispatcher._learn_turn("open the work site", "done", "browser", {}, "sid1", "text")
    assert store.row("SELECT count(*) c FROM interactions")["c"] >= 1
    print("[2] dispatcher turn hook ran")

    # 3. Attribution must only touch lessons the model actually saw this turn, and never an
    #    owner-confirmed rule. `add()` refuses a weak preference that contradicts an owner rule in
    #    the same context, so this observation deliberately lives in a different one.
    watch = lessons.add("preference", "Open the work browser before starting", scope="domain",
                        context="work", source="repeated_observation", confidence=0.5)
    assert watch and int(watch.get("confirmed") or 0) == 0, watch

    def counters():
        return dict(store.row("SELECT positive, negative FROM lessons WHERE lesson_id=?",
                              (watch["lesson_id"],)))

    before = counters()
    dispatcher._learn_turn("open the work site", "done", "browser", {}, "sid3", "text")
    after_browser = counters()
    assert after_browser == before, f"browser turn must not move counters: {before} -> {after_browser}"
    assert "chat" in lessons.CONTEXT_INJECTING_SKILLS, lessons.CONTEXT_INJECTING_SKILLS
    dispatcher._learn_turn("open the work site", "done again", "chat", {}, "sid4", "text")
    after_chat = counters()
    assert after_chat == before, "Unreviewed success must not be treated as positive owner feedback"
    from ishti.learning import observe, feedback
    observe.turn("open the work site", "done again", "chat", "sid4", reward=0.0,
                 message_id="msg_exact_owner_rating")
    assert feedback.apply_answer_rating("sid4", "msg_exact_owner_rating", "positive")
    assert feedback.apply_answer_rating("sid4", "msg_exact_owner_rating", "positive")
    after_chat = counters()
    assert after_chat["positive"] == before["positive"] + 1, \
        f"exact positive rating should credit relevant shown guidance once: {before} -> {after_chat}"
    print(f"[3] attribution gated on CONTEXT_INJECTING_SKILLS: "
          f"{before} -> {after_browser} -> {after_chat}")

    # 4. The reflection payload the endpoint serves, including its counts.
    dispatcher._learn_turn("that was wrong, use edge instead", "oops", "browser", {}, "sid2", "text")
    payload = dashboard.reflections()
    assert "reflections" in payload and "counts" in payload, payload.keys()
    assert any(r.get("kind_of") == "reflection" for r in payload["reflections"]), "no reflection stored"
    print(f"[4] reflection payload OK: {payload['counts']}")
    dump("after turn 2")


    # 5. The correction supersedes the older rule; the reflection stays recallable by words.
    rows = {str(r["statement"]): str(r["status"])
            for r in store.rows("SELECT statement, status FROM lessons")}
    chrome = next((s for s in rows if "chrome" in s.lower()), None)
    assert chrome and rows[chrome] == "superseded", (chrome, rows)
    assert any(r["status"] == "active" for r in store.rows(
        "SELECT status FROM lessons WHERE source='explicit_user_correction'")), "correction not active"
    got = lessons.guidance_for("open the work site", limit=6)
    assert any("work browser" in l["statement"].lower() for l in got), [l["statement"] for l in got]
    print(f"[5] supersede + reflection recall OK ({len(got)} lesson(s) in context)")

    # 6. Below the confidence floor a lesson cannot steer - this is what decay and blame buy us.
    weak = lessons.add("preference", "Use dark mode for notes", scope="domain", context="notes",
                       source="repeated_observation", confidence=0.05)
    assert weak["lesson_id"] not in [l["lesson_id"] for l in lessons.guidance_for("notes", limit=20)], \
        "a lesson below the confidence floor must not steer"
    print("[6] confidence floor blocks a faded lesson from steering")

    # 7. `maybe_decay()` throttles; decay leaves `updated` (evidence time) alone.
    updated_before = store.row("SELECT updated FROM lessons WHERE lesson_id=?",
                               (watch["lesson_id"],))["updated"]
    lessons.maybe_decay(force=True)
    assert lessons.maybe_decay() == 0, "decay must throttle inside its interval"
    assert store.row("SELECT updated FROM lessons WHERE lesson_id=?",
                     (watch["lesson_id"],))["updated"] == updated_before, \
        "decay must not overwrite `updated` or it resets its own clock"
    print("[7] maybe_decay throttles and preserves `updated`")

    # 8. The mining endpoint's payload shape.
    out = dashboard.promote_insights()
    assert isinstance(out.get("promoted"), list), out
    print(f"[8] promote_insights -> {len(out['promoted'])} lesson(s)")

    print("\n[SUCCESS] test_learning_wiring passed all wiring gates!")


if __name__ == "__main__":
    test_wiring()
