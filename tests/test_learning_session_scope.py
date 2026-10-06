import os

from ishti.learning import feedback, lessons, observe, policy, store


def test_temporary_lessons_are_session_bound(tmp_path):
    store.set_db_path(os.path.join(tmp_path, "learning.db"))
    created = policy.temporary_instruction("for now use Edge", session={"id": "chat-a"})
    assert created and created["entity"] == "chat-a"
    assert [x["lesson_id"] for x in lessons.guidance_for("open browser", session_id="chat-a")] == [created["lesson_id"]]
    assert lessons.guidance_for("open browser", session_id="chat-b") == []
    assert lessons.guidance_for("open browser") == []


def test_feedback_uses_persisted_applied_lesson_ids(tmp_path):
    store.set_db_path(os.path.join(tmp_path, "learning.db"))
    applied = lessons.add("preference", "Use Edge for browser", context="browser",
                          source="repeated_observation", confidence=0.5)
    iid = observe.turn("open browser", "done", "chat", "sid",
                       dec={"skill": "chat", "applied_lesson_ids": [applied["lesson_id"]]},
                       message_id="msg")
    assert feedback.apply_answer_rating("sid", "msg", "positive")
    after = store.row("SELECT positive FROM lessons WHERE lesson_id=?", (applied["lesson_id"],))["positive"]
    assert after == 1
    assert feedback.apply_answer_rating("sid", "msg", "positive")
    assert store.row("SELECT positive FROM lessons WHERE lesson_id=?", (applied["lesson_id"],))["positive"] == after
