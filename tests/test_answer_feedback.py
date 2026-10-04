"""Synthetic answer-rating regressions; no model, network, or desktop access."""
from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch

from orchestrator.memory.conversation import ConversationManager
from ishti import answer_feedback
from ishti.core.context import normalize_history


def _chat():
    scratch = tempfile.TemporaryDirectory()
    manager = ConversationManager(str(Path(scratch.name) / "sessions"))
    sid = manager.active_session_id
    manager.add_message(sid, "user", "What is the exact amount?")
    answer = manager.add_message(sid, "ishti", "The amount is $500.", metadata={"request": "What is the exact amount?", "skill": "chat"})
    return scratch, manager, sid, answer["id"]


def test_negative_revision_excludes_rejected_answer_from_context():
    scratch, manager, sid, mid = _chat()
    with scratch, patch.object(answer_feedback, "get_conversation_manager", return_value=manager), \
            patch.object(answer_feedback, "_review", return_value=("No source for $500", "I cannot verify the amount from the available evidence.")), \
            patch("ishti.learning.feedback.apply"):
        result = answer_feedback.submit(sid, mid, "negative", "That amount was invented")
        assert result["revision"]["metadata"]["revision_of"] == mid
        assert result["diagnosis"] == "Owner feedback: That amount was invented"
        history = normalize_history(manager.get_messages(sid))
        assert all("$500" not in row["content"] for row in history)
        assert history[-1]["content"].startswith("I cannot verify")
        second = answer_feedback.submit(sid, mid, "negative", "That amount was invented")
        assert second["duplicate"]
        assert len(manager.get_messages(sid)) == 3


def test_feedback_targets_exact_chat_and_assistant_only():
    scratch, manager, sid, mid = _chat()
    with scratch, patch.object(answer_feedback, "get_conversation_manager", return_value=manager), \
            patch("ishti.learning.feedback.apply"):
        assert answer_feedback.submit(sid, mid, "positive")["rating"] == "positive"
        assert answer_feedback.submit(sid, mid, "positive")["duplicate"]
        try:
            answer_feedback.submit("sess_wrong", mid, "negative")
            assert False, "Cross-chat rating accepted"
        except ValueError:
            pass
        user_id = manager.get_messages(sid)[0]["id"]
        try:
            answer_feedback.submit(sid, user_id, "negative")
            assert False, "User message rated as assistant answer"
        except ValueError:
            pass
