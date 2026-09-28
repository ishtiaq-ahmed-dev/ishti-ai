"""Integration test suite for ISHTI offline-first learning system (docs/LEARNING_INTERFACE.md section 6).

Covers full lifecycle:
1. Correction -> lesson -> policy override
2. Repeated mistakes -> candidate rule -> HITL confirm/reject
3. Entity separation and disambiguation questions
4. Self-eval ambiguity and reversible memory write-back
5. Workflow mining from repeated task execution
6. Dataset curation (deduplication, quality filtering) and JSONL export
7. Reward calculation table
8. Temporary instruction expiry
"""
import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import ishti.facts as F
from ishti.learning import (
    dashboard,
    dataset,
    entities,
    errors,
    feedback,
    lessons,
    observe,
    policy,
    selfeval,
    store,
    workflows,
)

def test_full_cycle():
    tmp = tempfile.mkdtemp()
    db_file = os.path.join(tmp, "test_learning.db")
    facts_file = os.path.join(tmp, "test_facts.db")
    store.set_db_path(db_file)
    F._facts = F.Facts(facts_file)

    print("[1] Testing correction -> lesson -> policy override...")
    fb = feedback.detect("you opened edge, I meant chrome", last_skill="browser")
    assert fb and fb["correct"] == "chrome" and fb["error_type"] == "wrong_application"
    applied = feedback.apply(fb, interaction={"id": "it_test1", "skill": "browser", "request": "open work web"})
    assert "recorded_mistake" in applied["effects"]
    assert applied["lesson"] and applied["lesson"]["correct"] == "chrome"
    assert applied["lesson"]["context"] == "browser", applied["lesson"]["context"]

    pol = policy.check("open browser", decision={"skill": "browser"})
    assert any(ov.get("correct") == "chrome" for ov in pol.get("overrides", [])), pol
    args_overridden = policy.apply_overrides("open browser", {"app": "edge"}, decision={"skill": "browser"})
    assert args_overridden["app"] == "chrome"

    print("[2] Testing repeated mistakes -> candidate rule -> HITL confirm/reject...")
    for i in range(3):
        errors.record(errors.classify("you used Edge, I meant Chrome"), interaction_id=f"rep_{i}", domain="browser")
    patterns = lessons.detect_patterns(min_count=3)
    assert len(patterns) >= 1 and patterns[0]["kind"] == "rule"
    cand_id = patterns[0]["id"]
    
    confirmed_res = lessons.accept_candidate(cand_id, keep=True)
    assert confirmed_res["lesson"]["confidence"] >= 0.95
    assert confirmed_res["candidate"]["status"] == "confirmed"
    assert store.row("SELECT status FROM candidates WHERE id=?", (cand_id,))["status"] == "confirmed"

    # a rejected candidate leaves a lesson behind but never an active one
    for i in range(3):
        errors.record(errors.classify("you used Excel, I meant Sheets"), interaction_id=f"rej_{i}", domain="files")
    rej = lessons.detect_patterns(min_count=3)[0]
    dropped = lessons.accept_candidate(rej["id"], keep=False)
    assert dropped["candidate"]["status"] == "discarded" and dropped["lesson"] == {}

    print("[3] Testing entity separation and disambiguation questions...")
    p1 = entities.observe("Bilal", org="Karachi Corp", email="bilal@karachi.com")
    p2 = entities.observe("Bilal", org="Lahore Labs", email="bilal@lahore.com")
    res_amb = entities.resolve("Bilal")
    assert res_amb["ambiguous"] is True
    assert res_amb["question"] and "Karachi" in res_amb["question"] and "Lahore" in res_amb["question"]
    
    res_dis = entities.resolve("Bilal", context="email Karachi office")
    assert res_dis["ambiguous"] is False
    assert res_dis["selected"]["org"] == "Karachi Corp", res_dis["selected"]

    print("[4] Testing self-eval risk scoring & reversible memory write-back...")
    eval_safe = selfeval.evaluate("read_text", {"file": "doc.txt"})
    assert eval_safe["confident"] is True and not eval_safe["requires_confirmation"]
    
    eval_risk = selfeval.evaluate("delete_database", {"db": "prod"})
    assert eval_risk["reversible"] is False and eval_risk["requires_confirmation"] is True

    pref = lessons.add("preference", "Prefeers dark mode in terminal", scope="global", context="terminal",
                       source="owner_statement", confidence=1.0,
                       action={"fact": ["owner", "terminal_theme", "dark"]})
    assert F.get_facts().get("owner").get("terminal_theme") == "dark"
    forgot = lessons.forget(pref["lesson_id"])
    assert forgot["status"] == "retired"
    assert not F.get_facts().get("owner").get("terminal_theme")

    print("[5] Testing workflow mining from multi-step tasks...")
    for i in range(3):
        tid = observe.task_begin("Deploy website", domain="dev")
        observe.task_step(tid, 1, "git_pull", "pulled", True)
        observe.task_step(tid, 2, "build_assets", "built", True)
        observe.task_step(tid, 3, "restart_service", "restarted", True)
        observe.task_end(tid, "success", reward=1.0)
        workflows.record(tid)
        
    wf_cands = workflows.scan(min_count=3)
    assert len(wf_cands) >= 1
    assert wf_cands[0]["steps"] == ["git_pull", "build_assets", "restart_service"]

    print("[6] Testing dataset curation & JSONL export...")
    dataset.add_example("Translate to Pashto", correct="dera manana", quality=0.9, skill="chat")
    dataset.add_example("Translate to Pashto", correct="dera manana", quality=0.9, skill="chat")  # duplicate
    dataset.add_example("bad text", correct="none", quality=0.1, skill="chat")  # low quality
    
    all_rows = dataset.rows()
    kept, skipped = dataset.curate(all_rows)
    assert len(kept) == 1 and skipped["duplicate"] == 1 and skipped["low_quality"] == 1
    
    exp_path, count = dataset.export(path=os.path.join(tmp, "train_export.jsonl"))
    assert count == 1 and os.path.exists(exp_path)

    print("[7] Testing reward table calculations...")
    assert feedback.reward_of("ok") == 0.5
    assert feedback.reward_of("failed") == -0.5
    assert feedback.reward_of("ok", {"polarity": "positive"}) == 1.0
    assert feedback.reward_of("failed", {"kind": "explicit_correction"}) == -0.8
    assert feedback.reward_of("failed", {"kind": "negative_reaction"}) == -1.0

    print("[8] Testing temporary instruction expiry...")
    temp_ls = policy.temporary_instruction("for now use Firefox")
    assert temp_ls and temp_ls["kind"] == "temporary"
    store.write("UPDATE lessons SET expires=? WHERE lesson_id=?", (store.now() - 10, temp_ls["lesson_id"]))
    lessons.expire_stale()
    active_temps = lessons.active(kind="temporary")
    assert len(active_temps) == 0

    print("\n[SUCCESS] test_learning passed all integration test gates!")

if __name__ == "__main__":
    test_full_cycle()
