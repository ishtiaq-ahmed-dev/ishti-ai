"""Lesson lifecycle regressions (learning review 2026-11): reflections stay distinct, faded high-priority lessons
can't crowd preferences out, a faded net-harmful lesson retires, a failing confirmed rule goes back to the owner,
and typed feedback ("wrong, use X") lands on the turn it is about."""
from ishti.learning import lessons, store


def _fresh(tmp_path):
    store.set_db_path(str(tmp_path / "learn.db"))


def test_reflections_without_a_target_stay_distinct(tmp_path):
    _fresh(tmp_path)
    a = lessons.add("procedural_lesson", "Check the time zone first", scope="task", context="reflection", source="monitor")
    b = lessons.add("procedural_lesson", "Ask which Ali before sending", scope="task", context="reflection", source="monitor")
    again = lessons.add("procedural_lesson", "check the time zone first", scope="task", context="reflection", source="monitor")
    assert a["lesson_id"] != b["lesson_id"] and again["lesson_id"] == a["lesson_id"]


def test_faded_lessons_do_not_crowd_out_preferences(tmp_path):
    _fresh(tmp_path)
    for i in range(5):  # high-priority, faded: must not occupy the LIMIT window ahead of a live preference
        lessons.add("procedural_lesson", f"Old faded rule {i} about email", scope="task", context=f"r{i}",
                    source="monitor", confidence=0.05)
    pref = lessons.add("preference", "Keep email replies short", scope="domain", context="email",
                       source="repeated_observation", confidence=0.6)
    got = lessons.active(limit=2, min_confidence=0.3)
    assert [l["lesson_id"] for l in got] == [pref["lesson_id"]]


def test_decay_retires_a_net_harmful_lesson_at_the_floor(tmp_path):
    _fresh(tmp_path)
    ls = lessons.add("preference", "Use bullet points", scope="domain", context="chat", source="monitor", confidence=0.06)
    lessons.adjust(ls["lesson_id"], 0.0, negative=1)
    store.write("UPDATE lessons SET updated=?, decayed=NULL WHERE lesson_id=?", (store.now() - 90 * 86400, ls["lesson_id"]))
    lessons.decay(days=30)
    assert store.row("SELECT status FROM lessons WHERE lesson_id=?", (ls["lesson_id"],))["status"] == "retired"


def test_failing_confirmed_rule_is_put_back_to_the_owner(tmp_path):
    _fresh(tmp_path)
    rule = lessons.add("rule", "Always send invoices from the work account", scope="domain", context="email",
                       source="owner_statement", confidence=0.95)
    for i in range(lessons.REVIEW_AFTER):
        lessons.attribute([rule["lesson_id"]], -1.0, interaction_id=f"i{i}")
    row = store.row("SELECT confidence, negative FROM lessons WHERE lesson_id=?", (rule["lesson_id"],))
    assert row["confidence"] == 0.95 and row["negative"] == lessons.REVIEW_AFTER  # counted, not demoted
    cand = store.row("SELECT * FROM candidates WHERE status='open' AND kind='review'")
    assert cand and "Keep it?" in cand["question"]
    lessons.accept_candidate(cand["id"], keep=False)
    assert store.row("SELECT status FROM lessons WHERE lesson_id=?", (rule["lesson_id"],))["status"] == "retired"


def test_typed_feedback_lands_on_the_previous_turn(tmp_path):
    _fresh(tmp_path)
    from ishti import dispatcher
    dispatcher._learn_turn("open my work site", "Opened it in Edge.", "apps", {}, "s-fb", "text")
    first = store.row("SELECT id FROM interactions WHERE session_id='s-fb' ORDER BY ts LIMIT 1")["id"]
    dispatcher._learn_turn("that was wrong, use chrome instead", "Noted.", "chat", {}, "s-fb", "text")
    fb = store.row("SELECT interaction_id FROM feedback ORDER BY ts DESC LIMIT 1")
    assert fb["interaction_id"] == first
    assert float(store.row("SELECT reward FROM interactions WHERE id=?", (first,))["reward"]) < 0


def test_repeated_feedback_reason_becomes_an_accepted_preference(tmp_path):
    _fresh(tmp_path)
    from ishti.facts import get_facts
    from ishti.learning import feedback
    for i in range(lessons.STYLE_AFTER):
        feedback.record_typed(prompt=f"explain thing {i}", rating="negative", reason="way too long", skill="chat")
    cand = next(c for c in lessons.detect_patterns() if c["signature"] == "style:too_long")
    assert "always follow" in cand["question"]
    lessons.accept_candidate(cand["id"], keep=True)
    assert "short answers" in get_facts().get("owner").get("prefers_answer_length", "")
    assert not [c for c in lessons.detect_patterns() if c["signature"] == "style:too_long"]  # asked once
