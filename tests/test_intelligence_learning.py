"""Focused offline acceptance tests for intelligence feedback and workflow learning."""
from __future__ import annotations

import json
from unittest.mock import patch

from ishti import answer_feedback
from ishti.learning import feedback, nightly, observe, store, workflows
from orchestrator.memory.conversation import ConversationManager


def _fresh_store(tmp_path):
    store.set_db_path(str(tmp_path / "learning.db"))
    return store.conn()


def test_disliked_routing_mistake_changes_same_prompt_via_accessor(tmp_path):
    _fresh_store(tmp_path)
    prompt = "Check the current Ollama release"
    row = feedback.record_typed(
        prompt=prompt, route={"skill": "chat", "confidence": 0.7}, skill="chat",
        answer="I think it is 1.0", rating="negative",
        reason="Wrong tool: use web_search instead", source="owner_button")
    assert row["reason_type"] == "wrong_tool"
    assert feedback.routing_hint(prompt) == "web_search"
    assert feedback.routing_hint("  CHECK the current Ollama release!!!  ") == "web_search"
    assert feedback.routing_hint("Check an unrelated release") is None


def test_dislike_revision_then_positive_creates_preference_pair(tmp_path):
    _fresh_store(tmp_path)
    manager = ConversationManager(str(tmp_path / "sessions"))
    sid = manager.active_session_id
    manager.add_message(sid, "user", "What is the exact invoice amount?")
    original = manager.add_message(
        sid, "ishti", "The amount is $500.",
        metadata={"request": "What is the exact invoice amount?", "skill": "chat",
                  "route": {"skill": "chat", "confidence": 0.8}})
    with patch.object(answer_feedback, "get_conversation_manager", return_value=manager), \
            patch.object(answer_feedback, "_review", return_value=(
                "The amount was unsupported", "I cannot verify the amount from the supplied evidence.")):
        disliked = answer_feedback.submit(sid, original["id"], "negative", "That fact was invented")
        revision_id = disliked["revision"]["id"]
        accepted = answer_feedback.submit(sid, revision_id, "positive")

    pair = store.row("SELECT * FROM preference_pairs WHERE accepted_message_id=?", (revision_id,))
    assert accepted["preference_pair_id"] == pair["id"]
    assert pair["rejected"] == "The amount is $500."
    assert pair["accepted"].startswith("I cannot verify")
    assert pair["reason_type"] == "wrong_fact"
    typed = store.rows("SELECT prompt,route,skill,answer,rating,reason_type FROM typed_feedback ORDER BY id")
    assert [row["rating"] for row in typed] == ["negative", "positive"]
    assert all(set(row) == {"prompt", "route", "skill", "answer", "rating", "reason_type"} for row in typed)


def test_nightly_exports_hints_pairs_and_nonpromotion_policy(tmp_path):
    _fresh_store(tmp_path)
    feedback.record_typed(prompt="Find the latest release", route={"skill": "chat"}, skill="chat",
                          answer="unknown", rating="negative",
                          reason="wrong tool, use web_search instead")
    feedback.accept_preference_pair(
        prompt="Summarize this", route={"skill": "chat"}, skill="chat", rejected="A very long answer",
        accepted="A concise answer", reason_type="too_long", accepted_message_id="msg_accepted")
    out = nightly.run(str(tmp_path / "nightly"))
    assert out["pairs"] == 1 and out["hints"] == 1 and out["auto_promote"] is False
    with open(out["lora_path"], encoding="utf-8") as handle:
        example = json.loads(handle.readline())
    assert example["messages"][-1]["content"] == "A concise answer"
    with open(out["metadata_path"], encoding="utf-8") as handle:
        policy = json.load(handle)
    assert policy["evaluate_before_promote"] is True
    assert policy["rollback_hook"] == "orchestrator.finetune.rollback"
    assert nightly.run_if_due(str(tmp_path / "nightly"), now=store.now())["status"] == "not_due"


def test_two_successful_identical_sequences_propose_disabled_skill_draft(tmp_path):
    _fresh_store(tmp_path)
    proposal = None
    for index in range(2):
        task_id = observe.task_begin("Prepare weekly client brief")
        observe.task_step(task_id, 1, "web_search", "sources", True)
        observe.task_step(task_id, 2, "docs", "draft", True)
        observe.task_end(task_id, "success", reward=1.0)
        workflow = workflows.record(task_id)
        proposal = workflow.get("skill_proposal")
        assert workflow["count"] == index + 1
    assert proposal and proposal["kind"] == "skill_draft" and proposal["status"] == "proposed"
    assert proposal["payload"]["initial_status"] == "disabled"
    assert proposal["payload"]["enable_tier"] == "T2"
    assert len(store.rows("SELECT * FROM candidates WHERE kind='skill_draft'")) == 1
