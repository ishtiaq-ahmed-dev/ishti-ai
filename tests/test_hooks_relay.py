"""WP-COUCOU acceptance: hook relay (matrix rows COU-02, COU-04, COU-21).

Proves, against a sandboxed audit DB (never the owner's data/audit.db) and a
captured sender (the real pipe roundtrip is proven by the module self-check):
  T-COU-02 approval cards carry BOTH ids; approve works exactly once; replay fails;
           approving A with B's action fails; unknown approval id (spoof) fails;
           deny works and consumes; cards are deduped across sync calls; while
           paused no card is emitted, after resume it still gets through
  T-COU-04 question answered exactly once - replay rejected outright, callback
           never runs twice; malformed/hostile payloads always return None
  T-COU-21 coucou_agent field validated ^[a-z0-9-]{1,24}$, invalid -> default pill;
           emit never raises even when the pipe dies; default budget <= 300 ms

Run: venv\\Scripts\\python -m pytest tests/test_hooks_relay.py -q
"""
import json
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

pytest.importorskip("win32pipe", reason="pywin32 not installed - relay targets the overlay pipe")

from ishti import audit, hooks_relay, overlay_pipe  # noqa: E402
from ishti.channels import approvals, policy as policy_mod  # noqa: E402


@pytest.fixture(autouse=True)
def sandbox(tmp_path, monkeypatch):
    """Audit + policy + overlay log point at scratch files; no state survives a test."""
    monkeypatch.setattr(audit, "DB", str(tmp_path / "audit.db"))
    audit._reset_cache()
    monkeypatch.setattr(policy_mod, "PATH", str(tmp_path / "policy.yaml"))
    policy_mod.reset_cache()
    monkeypatch.setattr(hooks_relay._log, "LOG_FILE", str(tmp_path / "overlay.log"))
    approvals.revoke_all()
    hooks_relay._reset()
    sent = []

    def fake_send(payload, budget_ms=None):
        sent.append((payload, budget_ms))
        return True

    monkeypatch.setattr(hooks_relay._pipe, "send", fake_send)
    yield sent
    approvals.revoke_all()
    hooks_relay._reset()


def _decoded(sent, i=-1):
    return json.loads(sent[i][0])


# ------------------------------------------------------------------ T-COU-21 outbound

def test_emit_envelope_shape_and_c14_agent_fallback(sandbox):
    assert hooks_relay.emit("SessionStart", agent="claude-cc", label="ready") is True
    assert hooks_relay.emit("PreToolUse", agent="BAD NAME!") is True
    first, second = _decoded(sandbox, 0), _decoded(sandbox, 1)
    assert first["kind"] == "hook" and first["event"] == "SessionStart"
    assert first["agent"] == "claude-cc" and first["v"] == 1 and first["label"] == "ready"
    assert second["agent"] == "ishti", "invalid coucou_agent falls back to the default pill"
    # label capped, invalid/empty events refused
    hooks_relay.emit("PreToolUse", label="L" * 500)
    assert len(_decoded(sandbox)["label"]) == hooks_relay.MAX_LABEL_LEN
    assert hooks_relay.emit("") is False
    assert hooks_relay.emit("E" * (hooks_relay.MAX_EVENT_LEN + 1)) is False
    assert hooks_relay.emit(None) is False


def test_emit_never_raises_when_the_pipe_dies(sandbox, monkeypatch):
    def dead(payload, budget_ms=None):
        raise OSError("pipe is gone")
    monkeypatch.setattr(hooks_relay._pipe, "send", dead)
    assert hooks_relay.emit("Stop") is False      # False, never an exception
    assert hooks_relay.emit_approval("ap_x", "a:b") is False


def test_default_budget_is_bound(sandbox):
    assert overlay_pipe.DEFAULT_BUDGET_MS <= 300, "COU-13: relay budget is 300 ms"
    hooks_relay.emit("Stop", budget_ms=250)
    assert sandbox[-1][1] == 250, "caller budget is passed through to the pipe send"


# ------------------------------------------------------------------ T-COU-02 approvals

def test_approval_card_carries_both_ids_and_sync_dedupes(sandbox):
    aid = approvals.new("term:rm:notes.txt", "Delete notes.txt", ttl_s=30)
    assert hooks_relay.sync_approvals() == 1
    card = _decoded(sandbox)
    assert card["kind"] == "approval.opened"
    assert card["approval_id"] == aid and card["action_id"] == "term:rm:notes.txt"
    assert hooks_relay.sync_approvals() == 0, "the same approval never emits twice"


def test_approve_single_use_replay_and_mismatch(sandbox):
    aid = approvals.new("term:rm:notes.txt", "Delete", ttl_s=30)
    ok = hooks_relay.handle_inbound({"kind": "approval.decide", "approval_id": aid,
                                     "action_id": "term:rm:notes.txt",
                                     "decision": "approve"})
    assert ok and ok["ok"] is True
    replay = hooks_relay.handle_inbound({"kind": "approval.decide", "approval_id": aid,
                                         "action_id": "term:rm:notes.txt",
                                         "decision": "approve"})
    assert replay and replay["ok"] is False, "single-use: replay fails"
    other = approvals.new("mail:send:boss", "Email", ttl_s=30)
    mismatch = hooks_relay.handle_inbound({"kind": "approval.decide",
                                           "approval_id": other,
                                           "action_id": "term:rm:notes.txt",
                                           "decision": "approve"})
    assert mismatch and mismatch["ok"] is False, "approving A with B's action fails"


def test_unknown_approval_id_is_rejected_and_deny_consumes(sandbox):
    spoof = hooks_relay.handle_inbound({"kind": "approval.decide",
                                        "approval_id": "ap_0000000000000000",
                                        "action_id": "anything",
                                        "decision": "approve"})
    assert spoof and spoof["ok"] is False, "spoofed overlay card without a real approval fails"
    aid = approvals.new("mail:send:boss", "Email", ttl_s=30)
    deny = hooks_relay.handle_inbound({"kind": "approval.decide", "approval_id": aid,
                                       "action_id": "mail:send:boss", "decision": "deny"})
    assert deny and deny["ok"] is True
    again = hooks_relay.handle_inbound({"kind": "approval.decide", "approval_id": aid,
                                        "action_id": "mail:send:boss", "decision": "deny"})
    assert again and again["ok"] is False, "deny is single-use too"


def test_pause_drops_outbound_but_inbound_decisions_stay_live(sandbox):
    assert hooks_relay.set_paused(True) is True
    aid = approvals.new("term:rm:x", "Delete", ttl_s=30)
    assert hooks_relay.emit("Stop") is False
    assert hooks_relay.sync_approvals() == 0, "no cards while paused"
    assert sandbox == [], "paused relay must not deliver anything"
    dec = hooks_relay.handle_inbound({"kind": "approval.decide", "approval_id": aid,
                                      "action_id": "term:rm:x", "decision": "approve"})
    assert dec and dec["ok"] is True, "owner decisions still apply while paused"
    # a second approval arrives while paused and stays undecided
    aid2 = approvals.new("mail:send:boss", "Email boss", ttl_s=30)
    assert hooks_relay.sync_approvals() == 0, "still paused"
    # resume: the withheld card gets through; the already-decided one never reappears
    hooks_relay.set_paused(False)
    assert hooks_relay.sync_approvals() == 1
    card = _decoded(sandbox)
    assert card["approval_id"] == aid2


# ------------------------------------------------------------------ T-COU-04 questions

def test_question_answered_exactly_once(sandbox):
    seen = []
    qid = hooks_relay.ask_question("Run it?", lambda a: seen.append(a))
    assert qid and _decoded(sandbox)["kind"] == "question"
    ans = hooks_relay.handle_inbound({"kind": "question.answer",
                                      "question_id": qid, "answer": "yes"})
    assert ans and ans["ok"] is True and seen == ["yes"]
    replay = hooks_relay.handle_inbound({"kind": "question.answer",
                                         "question_id": qid, "answer": "yes"})
    assert replay is None, "replay of a consumed question is rejected outright"
    assert seen == ["yes"], "callback ran exactly once"


def test_question_rejects_bad_answer_unknown_id_and_non_callable(sandbox):
    assert hooks_relay.ask_question("x", "not-callable") is None
    bad = hooks_relay.handle_inbound({"kind": "question.answer",
                                      "question_id": "q_nope", "answer": "maybe"})
    assert bad is None
    qid = hooks_relay.ask_question("Sure?", lambda a: None)
    bad2 = hooks_relay.handle_inbound({"kind": "question.answer",
                                       "question_id": qid, "answer": "SIDEWAYS"})
    assert bad2 is None, "only yes/no are answers"
    ok = hooks_relay.handle_inbound({"kind": "question.answer",
                                     "question_id": qid, "answer": "no"})
    assert ok and ok["ok"] is True


def test_question_dropped_when_send_fails(sandbox, monkeypatch):
    monkeypatch.setattr(hooks_relay._pipe, "send", lambda *a, **k: False)
    assert hooks_relay.ask_question("x?", lambda a: None) is None, "no dangling callbacks"


# ------------------------------------------------------------------ hostile input

def test_malformed_inbound_never_raises(sandbox):
    junk = [b"\xff\xfe not json", "[]", "{}", '"str"', 42, None, [],
            {"kind": "unknown.kind"},
            {"kind": "approval.decide"},
            {"kind": "approval.decide", "approval_id": [], "action_id": "x",
             "decision": "approve"},
            {"kind": "approval.decide", "approval_id": "ap_x", "action_id": "y",
             "decision": "shrug"},
            {"kind": "question.answer", "question_id": 7, "answer": "yes"},
            b"{" * 10]
    for item in junk:
        assert hooks_relay.handle_inbound(item) is None, repr(item)[:60]


def test_oversized_inbound_payload_rejected(sandbox):
    big = b"x" * (overlay_pipe.MAX_MSG + 1)
    assert hooks_relay.handle_inbound(big) is None


def test_on_bus_maps_only_peekable_events(sandbox):
    hooks_relay.on_bus({"type": "remote_alert", "data": {"detail": "door open"}})
    hooks_relay.on_bus({"type": "vram_status", "data": {"vram_mb": 1}})
    hooks_relay.on_bus({"type": "remote_push", "data": {"text": "hi"}})
    events = [_decoded(sandbox, i)["event"] for i in range(len(sandbox))]
    assert events == ["remote_alert", "remote_push"], events
    assert hooks_relay.on_bus("not-a-dict") is None      # never raises


def test_on_bus_maps_session_envelopes_to_pills(sandbox):
    """ECC-09: a session envelope published on the bus reaches the overlay as a pill lifecycle event
    (the schema's normalized state maps back onto the hook events the pills speak)."""
    from ishti import session_schema
    env = session_schema.make("s-1", "claude-cc", "working", label="Bash")
    hooks_relay.on_bus({"type": "session", "data": env})
    got = _decoded(sandbox)
    assert got["kind"] == "hook" and got["event"] == "PreToolUse", got
    assert got["agent"] == "claude-cc" and got["label"] == "Bash", got
    # an empty/invalid envelope falls back honestly instead of raising
    hooks_relay.on_bus({"type": "session", "data": {"state": "nonsense", "agent": "BAD NAME"}})
    got2 = _decoded(sandbox)
    assert got2["event"] == "SessionStart" and got2["agent"] == "ishti", got2
    hooks_relay.on_bus({"type": "session", "data": None})   # never raises


def test_selfcheck_still_passes():
    """The module's own __main__ block: real pipe, sandboxed audit, full decision matrix."""
    mod = os.path.join(ROOT, "ishti", "hooks_relay.py")
    env = {**os.environ,
           "PYTHONPATH": ROOT + (os.pathsep + os.environ["PYTHONPATH"] if os.environ.get("PYTHONPATH") else "")}
    r = subprocess.run([sys.executable, mod], capture_output=True, text=True,
                       timeout=120, cwd=ROOT, env=env)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "[self-check] hooks relay OK" in r.stdout
