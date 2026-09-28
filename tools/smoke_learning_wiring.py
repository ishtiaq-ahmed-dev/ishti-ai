"""End-to-end smoke test for the agentic learning wiring (dispatcher -> lessons/reflect -> prompt).

Run: .\\venv\\Scripts\\python.exe tools\\smoke_learning_wiring.py
Uses a throwaway DB; never touches D:\\ISHTI\\data\\learning.db.
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ishti.learning import store, lessons, reflect, dashboard  # noqa: E402

store.set_db_path(os.path.join(tempfile.mkdtemp(), "wire.db"))


def dump(label):
    rows = store.rows("SELECT status, kind, source, confidence, context, statement FROM lessons "
                      "ORDER BY updated DESC")
    print(f"--- {label}: {len(rows)} lesson(s)")
    for r in rows:
        print(f"    {r['status']:<8} {r['kind']:<20} {r['source']:<24} {r['confidence']:.2f} "
              f"{r['context']:<14} {r['statement'][:64]}")


def main() -> int:
    # 1. A lesson exists and is offered to the prompt builder.
    lesson = lessons.add("rule", "Open work browsing in Chrome", scope="global", context="browser",
                         source="owner_statement", confidence=0.95, correct="chrome")
    assert lesson, "lesson not created"
    from ishti.skills.chat.skill import build_context
    ctx = build_context("open the work site")
    assert "LEARNED FROM THE OWNER" in ctx, f"lessons not injected into prompt:\n{ctx}"
    print("[1] lessons reach the model's prompt")
    dump("after add")

    # 2. A turn hook runs, logs, and does not raise.
    from ishti import dispatcher
    dispatcher._learn_turn("open the work site", "done", "browser", {}, "sid1", "text")
    assert store.row("SELECT count(*) c FROM interactions")["c"] >= 1
    print("[2] dispatcher turn hook ran")
    dump("after turn 1")

    # 3. Reflections are stored and readable through the dashboard shape the API returns.
    dispatcher._learn_turn("that was wrong, use edge instead", "oops", "browser", {}, "sid2", "text")
    payload = dashboard.reflections()
    assert "reflections" in payload and "counts" in payload, payload.keys()
    stored = payload["reflections"]
    assert any(r.get("kind_of") == "reflection" for r in stored), "no reflection stored"
    print(f"[3] reflection payload OK: {payload['counts']}")
    dump("after turn 2")

    # 4. Turn 2's correction must have superseded the older Chrome rule (last-writer-wins), and
    #    the reflection must stay retrievable by words so it can steer a later turn.
    rows = {str(r["statement"]): str(r["status"]) for r in store.rows("SELECT statement, status FROM lessons")}
    chrome = next((s for s in rows if "chrome" in s.lower()), None)
    assert chrome and rows[chrome] == "superseded", (chrome, rows)
    assert any(r["status"] == "active" for r in store.rows(
        "SELECT status FROM lessons WHERE source='explicit_user_correction'")), "correction not active"
    got = lessons.guidance_for("open the work site", limit=6)
    print("    guidance:", [l["statement"][:56] for l in got])
    assert any("worked" in l["statement"].lower() for l in got), [l["statement"] for l in got]
    print(f"[4] supersede + reflection recall OK ({len(got)} lesson(s) in context)")

    # 5. A low-confidence lesson does not reach the prompt; a confirmed one does.
    weak = lessons.add("preference", "Use dark mode for notes", scope="domain", context="notes",
                       source="repeated_observation", confidence=0.05)
    assert weak["lesson_id"] not in [l["lesson_id"] for l in lessons.guidance_for("notes", limit=20)], \
        "a lesson below the confidence floor must not steer"
    assert lessons.decay(days=30) >= 0
    print("[5] confidence floor blocks a faded lesson from steering")

    # 6. Throttled decay entry point is safe to call repeatedly.
    lessons.maybe_decay(force=True)
    assert lessons.maybe_decay() == 0, "decay must throttle inside its interval"
    print("[6] maybe_decay throttles")

    # 7. The mining endpoint shape.
    out = dashboard.promote_insights()
    assert isinstance(out.get("promoted"), list), out
    print(f"[7] promote_insights -> {len(out['promoted'])} lesson(s)")

    print("[smoke] agentic learning wiring OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
