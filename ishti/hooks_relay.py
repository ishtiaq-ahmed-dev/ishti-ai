"""ISHTI hook relay (WP-COUCOU, matrix COU-02/04/21; coucou hook/src/main.rs).

The coucou-hook equivalent, re-implemented originally:

  OUT (agent -> overlay): every event crosses the owner-only pipe inside the 300 ms
  budget via overlay_pipe.send - the agent NEVER waits on the overlay (COU-13).
  Envelopes: {"v":1,"kind":"hook"|"approval.opened"|"question", ...}.
  Any local agent may appear via the coucou_agent-style ``agent`` field, regex
  validated ^[a-z0-9-]{1,24}$ with fallback to the default pill (COU-14/21).

  IN (overlay -> ISHTI): the owner's Approve/Deny click and yes/no answers are
  applied by the EXISTING single-use machinery (channels.approvals, dispatcher
  pending). The relay adds no authority: an approval needs both ids (spoof without
  the bound action_id fails), a replay fails, an unknown id fails (COU-02/04).
  Inbound decisions are honoured even while paused - pause stops OUTBOUND only.

  PAUSE (COU-17 tray): set_paused(True) drops outbound envelopes; sync'd cards are
  re-emitted after resume.

Everything here never raises: a broken overlay, a dead pipe, malformed input or a
failing handler all end as False/None.
Self-check: python ishti/hooks_relay.py   (sandboxed audit/policy, real pipe roundtrip)
"""
from __future__ import annotations

import json
import os
import re
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from ishti import overlay_pipe as _pipe                      # noqa: E402  (budget-bound send)
from ishti.channels import approvals                          # noqa: E402  (single-use decisions)
from desktop.overlay import log as _log                       # noqa: E402  (scrubbed overlay log)
from desktop.overlay.pills import agent_name                  # noqa: E402  (C14 validation)

VERSION = 1
MAX_EVENT_LEN = 64
MAX_LABEL_LEN = 200
MAX_RECAP_LEN = 300
OPENED_RE = re.compile(r"^(.*) \((ap_[0-9a-f]+)\)")
CARDS_CAP = 200
# the events bus types worth a peek card; vram/model chatter stays out of the island
BUS_EVENTS = ("remote_push", "remote_alert", "error")

_lock = threading.Lock()
_paused = False
_questions = {}                          # qid -> callback(answer) -> any
_cards = set()                           # approval ids already put on the overlay
_last_receipt = 0.0
_stop = threading.Event()
_thread = None


# --------------------------------------------------------------------------- outbound

def _send_envelope(env: dict, budget_ms: int | None = None) -> bool:
    with _lock:
        if _paused:
            return False                  # pause: outbound dropped, never queued forever
    try:
        payload = json.dumps(env, ensure_ascii=True, sort_keys=True,
                             separators=(",", ":")).encode("utf-8")
    except Exception:
        return False
    try:
        ok = bool(_pipe.send(payload, budget_ms=budget_ms) if budget_ms
                  else _pipe.send(payload))
    except Exception:
        ok = False
    if not ok:                            # failures go to the scrubbed log, never raise
        _log.log(f"overlay relay dropped {env.get('kind')} {env.get('event', '')}", kind=None)
    return ok


def emit(event, agent=None, label=None, budget_ms: int | None = None) -> bool:
    """Forward one agent lifecycle event to the overlay inside the relay budget."""
    try:
        ev = str(event or "").strip()
        if not ev or len(ev) > MAX_EVENT_LEN:
            return False
        env = {"v": VERSION, "kind": "hook", "event": ev, "ts": time.time(),
               "agent": agent_name(agent)}
        if label is not None:
            env["label"] = str(label)[:MAX_LABEL_LEN]
        return _send_envelope(env, budget_ms=budget_ms)
    except Exception:
        return False


def emit_approval(approval_id, action_id, recap="") -> bool:
    """Approval card with BOTH ids (the anti-spoof: deciding needs the bound action_id)."""
    try:
        aid, act = str(approval_id), str(action_id)
        if not aid or not act:
            return False
        env = {"v": VERSION, "kind": "approval.opened", "approval_id": aid,
               "action_id": act, "recap": str(recap)[:MAX_RECAP_LEN], "ts": time.time()}
        return _send_envelope(env)
    except Exception:
        return False


def ask_question(prompt, callback) -> str | None:
    """Register a single-use yes/no question (COU-04) and send its card.
    Returns the question id, or None (paused / send failed / bad input)."""
    try:
        if not callable(callback):
            return None
        qid = "q_" + __import__("secrets").token_hex(8)
        with _lock:
            _questions[qid] = callback
        env = {"v": VERSION, "kind": "question", "question_id": qid,
               "prompt": str(prompt)[:MAX_RECAP_LEN], "ts": time.time()}
        if _send_envelope(env):
            return qid
        with _lock:                        # never leave a dangling callback nobody saw
            _questions.pop(qid, None)
        return None
    except Exception:
        return None


def sync_approvals() -> int:
    """Emit cards for approval.opened receipts not yet on the overlay (deduped).
    Safe to call repeatedly; 0 while paused (cards re-emit after resume) and rows whose
    send failed stay un-advanced so the next call retries them."""
    global _last_receipt
    if paused():
        return 0                      # withheld; receipts stay unread, resume re-emits
    emitted = 0
    try:
        rows = approvals.receipts(since=_last_receipt)
        max_ts, failed_ts = _last_receipt, None
        for row in rows:
            ts, _init, _skill, kind, detail = row[0], row[1], row[2], row[3], row[4]
            try:
                tsf = float(ts)
                max_ts = max(max_ts, tsf)
            except Exception:
                tsf = None
            if str(kind) != "approval.opened":
                continue
            m = OPENED_RE.match(str(detail))
            if not m:
                continue
            action_id, aid = m.group(1), m.group(2)
            with _lock:
                if aid in _cards:
                    continue
            rec = approvals.get(aid)       # None when already used/expired: stale card, skip
            if rec is None:
                continue
            if emit_approval(aid, action_id, rec.get("recap", "")):
                with _lock:
                    _cards.add(aid)
                    while len(_cards) > CARDS_CAP:      # bounded memory
                        _cards.pop()
                emitted += 1
            elif tsf is not None:
                failed_ts = tsf if failed_ts is None else min(failed_ts, tsf)
        if failed_ts is not None and failed_ts > _last_receipt:
            _last_receipt = failed_ts - 1e-6    # retry that row later; _cards dedupes successes
        else:
            _last_receipt = max_ts
    except Exception:
        return emitted
    return emitted


# --------------------------------------------------------------------------- inbound

def handle_inbound(payload) -> dict | None:
    """Apply one owner decision arriving over the owner-only pipe.
    Returns a result dict, or None for anything malformed/unknown/rejected. Never raises."""
    try:
        if isinstance(payload, (bytes, bytearray)):
            if len(payload) > _pipe.MAX_MSG:
                return None
            payload = bytes(payload).decode("utf-8", errors="strict")
        if isinstance(payload, str):
            data = json.loads(payload)
        elif isinstance(payload, dict):
            data = payload
        else:
            return None
        if not isinstance(data, dict):
            return None
        kind = data.get("kind")
        if kind == "approval.decide":
            return _decide(data)
        if kind == "question.answer":
            return _answer(data)
        return None
    except Exception:
        return None


def _decide(data) -> dict | None:
    aid, act = data.get("approval_id"), data.get("action_id")
    decision = str(data.get("decision") or "").lower()
    if not isinstance(aid, str) or not isinstance(act, str) or not aid:
        return None
    if decision in ("approve", "approved", "allow"):
        ok = approvals.confirm(aid, act)   # single-use, action-bound, expiring: A-vs-B fails
    elif decision in ("deny", "denied", "reject"):
        if decision in ("reject",):
            decision = "deny"
        ok = approvals.deny(aid)
    else:
        return None
    _log.log(f"overlay decision {decision} ok={ok}", kind=None)
    return {"kind": "approval.decided", "ok": bool(ok), "approval_id": aid,
            "decision": decision, "ts": time.time()}


def _answer(data) -> dict | None:
    qid = data.get("question_id")
    answer = str(data.get("answer") or "").lower()
    if not isinstance(qid, str) or answer not in ("yes", "no"):
        return None
    with _lock:
        cb = _questions.pop(qid, None)     # single-use: consumed exactly once
    if cb is None:
        return None                        # replay / unknown id: rejected
    try:
        result = cb(answer)
        ok = result is not False
    except Exception:
        ok = False
    return {"kind": "question.answered", "ok": bool(ok), "question_id": qid,
            "answer": answer, "ts": time.time()}


# --------------------------------------------------------------------------- bus + lifecycle

def on_bus(msg) -> None:
    """events.publish fan-out -> overlay peek (COU-03 runtime wiring)."""
    try:
        if not isinstance(msg, dict) or msg.get("type") not in BUS_EVENTS:
            return
        data = msg.get("data") or {}
        label = data.get("text") or data.get("detail") or data.get("message") or ""
        emit(str(msg.get("type")), agent="ishti", label=str(label))
    except Exception:
        pass


def set_paused(paused: bool) -> bool:
    """C17 tray Pause: drop outbound relay traffic (inbound decisions stay live)."""
    global _paused
    with _lock:
        _paused = bool(paused)
        return _paused


def paused() -> bool:
    with _lock:
        return _paused


def start(poll_s: float = 2.0) -> None:
    """Runtime wiring: subscribe the events bus + poll new approval receipts."""
    global _thread
    from ishti import events
    try:
        events.subscribe(on_bus)
    except Exception:
        pass
    if _thread is not None and _thread.is_alive():
        return
    _stop.clear()

    def _loop():
        while not _stop.wait(poll_s):
            sync_approvals()

    _thread = threading.Thread(target=_loop, daemon=True, name="hooks-relay")
    _thread.start()


def stop() -> None:
    global _thread
    _stop.set()
    try:
        from ishti import events
        events.unsubscribe(on_bus)
    except Exception:
        pass
    if _thread is not None:
        _thread.join(timeout=3)
    _thread = None


def _reset() -> None:
    """Fresh state for self-checks/tests (no approvals/answers/cards carried over)."""
    global _paused, _last_receipt, _thread
    with _lock:
        _paused = False
        _questions.clear()
        _cards.clear()
    _last_receipt = 0.0
    _stop.set()
    _thread = None


if __name__ == "__main__":
    # self-check: sandboxed audit+policy (never the owner's DB), REAL pipe roundtrip
    import tempfile
    from ishti import audit
    import ishti.channels.policy as P

    tmp = tempfile.mkdtemp()
    audit.DB = os.path.join(tmp, "audit.db")
    audit._reset_cache()
    P.PATH = os.path.join(tmp, "policy.yaml")
    P.reset_cache()
    _log.LOG_FILE = os.path.join(tmp, "overlay.log")
    approvals.revoke_all()
    _reset()
    _stop.clear()

    stop_ev, got = threading.Event(), []

    def _collect(raw):
        got.append(json.loads(raw))

    t = threading.Thread(target=_pipe.serve, args=(_collect, stop_ev), daemon=True)
    t.start()
    time.sleep(0.15)
    try:
        # outbound: envelope shape + C14 fallback, inside the real owner-only pipe
        assert emit("SessionStart", agent="claude-cc", label="ready"), "emit must deliver"
        assert emit("PreToolUse", agent="BAD NAME")          # invalid -> default pill
        time.sleep(0.1)
        kinds = [m.get("kind") for m in got]
        assert kinds == ["hook", "hook"], kinds
        assert got[0]["event"] == "SessionStart" and got[0]["agent"] == "claude-cc", got[0]
        assert got[1]["agent"] == "ishti", "invalid coucou_agent falls back to default pill"

        # pause: outbound dropped, inbound still live
        set_paused(True)
        assert emit("Stop") is False and paused() is True
        time.sleep(0.1)
        assert len(got) == 2, "paused relay must not deliver"
        set_paused(False)

        # COU-02: sync puts a card with BOTH ids; approve once -> works; replay -> fails;
        # wrong action -> fails; unknown id -> fails (overlay spoof without ids)
        aid = approvals.new("term:rm:notes.txt", "Delete notes.txt", ttl_s=30)
        assert sync_approvals() == 1, "card must be emitted for the opened receipt"
        time.sleep(0.1)
        card = [m for m in got if m.get("kind") == "approval.opened"][-1]
        assert card["approval_id"] == aid and card["action_id"] == "term:rm:notes.txt", card
        assert sync_approvals() == 0, "cards are deduped"
        r1 = handle_inbound(json.dumps({"kind": "approval.decide", "approval_id": aid,
                                        "action_id": "term:rm:notes.txt",
                                        "decision": "approve"}))
        assert r1 and r1["ok"] is True, r1
        r2 = handle_inbound(json.dumps({"kind": "approval.decide", "approval_id": aid,
                                        "action_id": "term:rm:notes.txt",
                                        "decision": "approve"}))
        assert r2 and r2["ok"] is False, "replay must fail (single-use)"
        aid2 = approvals.new("mail:send:boss", "Email boss", ttl_s=30)
        bad = handle_inbound(json.dumps({"kind": "approval.decide", "approval_id": aid2,
                                         "action_id": "term:rm:notes.txt",
                                         "decision": "approve"}))
        assert bad and bad["ok"] is False, "approving A with B's action must fail"
        spoof = handle_inbound(json.dumps({"kind": "approval.decide",
                                           "approval_id": "ap_deadbeef",
                                           "action_id": "whatever",
                                           "decision": "approve"}))
        assert spoof and spoof["ok"] is False, "unknown approval id must be rejected"
        deny = handle_inbound(json.dumps({"kind": "approval.decide", "approval_id": aid2,
                                          "action_id": "mail:send:boss",
                                          "decision": "deny"}))
        assert deny and deny["ok"] is True, "deny works"

        # COU-04: single-use question, answered exactly once
        seen = []
        qid = ask_question("Run the migration now?", lambda a: seen.append(a))
        assert qid, "question card must be sent"
        time.sleep(0.1)
        assert any(m.get("kind") == "question" and m["question_id"] == qid for m in got)
        ans = handle_inbound(json.dumps({"kind": "question.answer",
                                         "question_id": qid, "answer": "yes"}))
        assert ans and ans["ok"] is True and seen == ["yes"], (ans, seen)
        replay = handle_inbound(json.dumps({"kind": "question.answer",
                                            "question_id": qid, "answer": "yes"}))
        assert replay is None, "question replay must be rejected outright"
        assert seen == ["yes"], "callback ran exactly once"

        # malformed / hostile input: never raises, always None
        for junk in (b"\xff\xfe not json", "[]", "{}", '"str"',
                     json.dumps({"kind": "approval.decide"}),
                     json.dumps({"kind": "approval.decide", "approval_id": [],
                                 "action_id": "x", "decision": "approve"}),
                     json.dumps({"kind": "question.answer", "question_id": qid,
                                 "answer": "maybe"}),
                     "x" * (_pipe.MAX_MSG + 10)):
            assert handle_inbound(junk) is None, junk[:40]
    finally:
        stop_ev.set()
        t.join(timeout=3)
        approvals.revoke_all()
        _reset()
    print("[self-check] hooks relay OK - envelopes inside budget, COU-14 fallback, pause, "
          "single-use approve/deny + spoof rejection, single-use question, junk never raises")
