"""WP-OD acceptance: approval receipts + single-use/expiry invariants (matrix rows OD-04, OD-05, OD-06).

Proves, against a sandboxed audit DB (never the owner's data/audit.db):
  T-OD-04  every approval lifecycle transition writes a receipt with BOTH ids
           (approval.opened / approved / denied / expired / mismatch),
  T-OD-05  replay of an approval fails AND does not add a second receipt;
           approving A cannot approve B (mismatch receipt, never approved),
  T-OD-06  expiry/deny consume the approval before any dispatch could happen
           (confirm after deny/expiry is False = no adapter could be called).

Run: venv\\Scripts\\python -m pytest tests/test_gateway_receipts.py -q
"""
import os
import sys
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from ishti import audit  # noqa: E402
from ishti.channels import approvals, policy as policy_mod  # noqa: E402


@pytest.fixture(autouse=True)
def sandbox(tmp_path, monkeypatch):
    """Audit + policy point at scratch files; no approval survives a test."""
    monkeypatch.setattr(audit, "DB", str(tmp_path / "audit.db"))
    audit._reset_cache()
    monkeypatch.setattr(policy_mod, "PATH", str(tmp_path / "policy.yaml"))
    policy_mod.reset_cache()
    approvals.revoke_all()
    yield
    approvals.revoke_all()
    audit._reset_cache()


def _receipts(since=0.0):
    return approvals.receipts(since=since)


def _kinds(rows):
    return [str(r[3]) for r in rows]


def test_opened_receipt_written_before_any_decision():
    """T-OD-04: new() emits approval.opened carrying action id, approval id, ttl, channel."""
    before = time.time()
    aid = approvals.new("whatsapp:send:ali:123", "Send 'hi' to Ali", channel="telegram", ttl_s=60)
    rows = [r for r in _receipts(before) if str(r[3]) == "approval.opened"]
    assert len(rows) == 1, f"exactly one opened receipt, got {len(rows)}"
    detail = str(rows[0][4])
    assert "whatsapp:send:ali:123" in detail and aid in detail
    assert "ttl=60s" in detail and "channel=telegram" in detail


def test_approved_receipt_and_replay_adds_nothing():
    """T-OD-05: one approval -> exactly one approved receipt; replay is False and writes nothing new."""
    before = time.time()
    aid = approvals.new("term:rm:x", "Delete x")
    assert approvals.confirm(aid, "term:rm:x") is True
    assert approvals.confirm(aid, "term:rm:x") is False        # replay fails
    approved = [r for r in _receipts(before) if str(r[3]) == "approval.approved"]
    assert len(approved) == 1, f"replay must not create a second receipt, got {len(approved)}"
    detail = str(approved[0][4])
    assert "term:rm:x" in detail and aid in detail


def test_approving_a_cannot_approve_b():
    """T-OD-05: wrong action -> False + approval.mismatch receipt; A's approval stays unused for A."""
    before = time.time()
    a1 = approvals.new("whatsapp:send:ali:123", "Send")
    a2 = approvals.new("terminal:rm:x", "Delete")
    assert approvals.confirm(a1, "terminal:rm:x") is False      # approving A cannot approve B
    mismatches = [r for r in _receipts(before) if str(r[3]) == "approval.mismatch"]
    assert len(mismatches) == 1
    assert a1 in str(mismatches[0][4]) and "terminal:rm:x" in str(mismatches[0][4])
    approved = [r for r in _receipts(before) if str(r[3]) == "approval.approved"]
    assert approved == [], "no approved receipt may exist for the wrong action"
    assert approvals.confirm(a1, "whatsapp:send:ali:123") is True  # A still works afterwards


def test_denied_receipt_carries_both_ids_and_consumes():
    """T-OD-06: deny writes a receipt with both ids and the approval can never confirm after."""
    before = time.time()
    aid = approvals.new("mail:send:boss", "Email boss")
    assert approvals.deny(aid) is True
    assert approvals.deny(aid) is False                         # second deny does nothing
    assert approvals.confirm(aid, "mail:send:boss") is False    # denied => never dispatchable
    denied = [r for r in _receipts(before) if str(r[3]) == "approval.denied"]
    assert len(denied) == 1
    assert "mail:send:boss" in str(denied[0][4]) and aid in str(denied[0][4])


def test_expired_receipt_and_expired_never_confirms():
    """T-OD-06: expiry consumes the approval; confirm after expiry is False; receipt has both ids."""
    before = time.time()
    aid = approvals.new("term:shutdown", "Shut down", ttl_s=0.05)
    time.sleep(0.08)
    assert approvals.confirm(aid, "term:shutdown") is False     # expired fails
    expired = [r for r in _receipts(before) if str(r[3]) == "approval.expired"]
    assert len(expired) == 1
    assert "term:shutdown" in str(expired[0][4]) and aid in str(expired[0][4])
    assert approvals.get(aid) is None                           # gone from the store too


def test_receipts_query_is_readonly_filtered_and_newest_first():
    """The overlay/audit surface: only approval.* rows, newest first, no store mutation."""
    before = time.time()
    approvals.new("x:1", "r1")
    approvals.new("x:2", "r2")
    audit.record("not.an.approval", "must not appear")
    rows = _receipts(before)
    kinds = _kinds(rows)
    assert kinds and all(k.startswith("approval.") for k in kinds)
    assert "not.an.approval" not in kinds
    assert len(kinds) == 2 and kinds[0] == kinds[1] == "approval.opened"
    assert [r[0] for r in rows] == sorted((r[0] for r in rows), reverse=True)
    assert approvals.pending() == 2, "receipts() must not consume or expire live approvals"


def test_selfcheck_still_passes():
    """The module's own invariant block (from the source) must stay green after the receipt edits."""
    import runpy
    import pathlib
    mod = pathlib.Path(ROOT) / "ishti" / "channels" / "approvals.py"
    # run as __main__ in a subprocess so its temp policy path cannot leak into this process
    import subprocess
    env = {**os.environ, "ISHTI_TEST_MODE": "1"}
    env["PYTHONPATH"] = ROOT + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    r = subprocess.run([sys.executable, str(mod)], capture_output=True, text=True, timeout=60,
                       cwd=ROOT, env=env)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "[self-check] channels.approvals OK" in r.stdout
