"""Single-use, expiring, action-bound T2 step-up approvals (WP-A3; design R4 §3).

An approval is created when a remote T2 action needs the owner's yes:
  new(action_id, recap) -> approval_id   (bound to ONE action, expires after policy.approval_ttl_s)
  confirm(approval_id, action_id, sender_ok=True) -> True exactly once, before expiry.
Anything else (wrong action, second press, expired, unknown id, locked/revoked) is False and audited.
"""
import threading
import time

from ishti.channels import policy as policy_mod

try:  # best-effort failure log (never breaks the module if logging is unavailable)
    from ishti.core.logutil import swallowed
except Exception:  # pragma: no cover
    def swallowed(*_a, **_k):
        return None


_lock = threading.Lock()
_store = {}  # approval_id -> {"action": str, "recap": str, "expires": float, "used": False, "channel": str}


def new(action_id: str, recap: str, channel: str = "telegram", ttl_s: int | None = None) -> str:
    import secrets

    aid = "ap_" + secrets.token_hex(8)
    ttl = float(ttl_s) if ttl_s is not None else float(policy_mod.load().get("approval_ttl_s", 120))
    with _lock:
        _expire_locked()
        _store[aid] = {"action": action_id, "recap": recap, "expires": time.time() + ttl,
                       "used": False, "channel": channel, "created": time.time()}
    # Receipt (open-dots OD-04): the OPEN event must exist in the audit before any decision can,
    # so a denied/expired approval is always provable as "it was asked, nobody answered".
    _audit("approval.opened", f"{action_id} ({aid}) ttl={ttl:.0f}s channel={channel}")
    return aid


def get(approval_id: str) -> dict | None:
    with _lock:
        _expire_locked()
        rec = _store.get(approval_id)
        if not rec or rec["used"]:
            return None
        return dict(rec)


def confirm(approval_id: str, action_id: str) -> bool:
    """Consume the approval. True only for the right action, once, before expiry."""
    with _lock:
        _expire_locked()
        rec = _store.get(approval_id)
        if not rec or rec["used"]:
            return False
        if rec["action"] != action_id:
            _audit("approval.mismatch", f"{approval_id} bound to {rec['action']}, got {action_id}")
            return False
        rec["used"] = True
    _audit("approval.approved", f"{action_id} ({approval_id})")
    return True


def deny(approval_id: str) -> bool:
    with _lock:
        rec = _store.pop(approval_id, None)
    if rec:
        _audit("approval.denied", f"{rec['action']} ({approval_id})")
        return True
    return False


def revoke_all() -> int:
    with _lock:
        n = len(_store)
        _store.clear()
    return n


def _expire_locked():
    now = time.time()
    for aid in [a for a, r in _store.items() if r["expires"] < now]:
        rec = _store.pop(aid)
        _audit("approval.expired", f"{rec['action']} ({aid})")


def receipts(since: float = 0.0, limit: int = 200) -> list[tuple]:
    """Approval lifecycle receipts (opened/approved/denied/expired/mismatch), newest first.

    Read-only surface for the ISHTI Overlay approvals queue, the audit panel, and tests:
    every row is (ts, initiator, skill, action, detail, tier, confirmed, request, channel)
    from the audit log, filtered to approval.* events. Secrets are already masked at write time.
    """
    from ishti import audit
    return [row for row in audit.query(since=since, limit=500)
            if str(row[3]).startswith("approval.")][:limit]


def _audit(action: str, detail: str):
    try:
        from ishti import audit
        with audit.context(skill="channels", initiator="owner", channel="remote-gate"):
            audit.record(action, detail)
    except Exception as _exc:
        swallowed('ishti/channels/approvals.py:_audit', _exc)


def pending() -> int:
    with _lock:
        _expire_locked()
        return len(_store)


if __name__ == "__main__":
    import ishti.channels.policy as P
    import tempfile, os
    from types import SimpleNamespace
    clock = [time.time()]
    time = SimpleNamespace(time=lambda: clock[0])
    P.PATH = os.path.join(tempfile.mkdtemp(), "p.yaml")
    P.reset_cache()
    a1 = new("whatsapp:send:ali:123", "Send 'hello' to Ali (chat 123)", ttl_s=0.05)
    a2 = new("terminal:rm:x", "Delete file x", ttl_s=0.05)
    assert pending() == 2
    assert not confirm(a1, "terminal:rm:x")          # approving A cannot approve B
    assert confirm(a1, "whatsapp:send:ali:123")
    assert not confirm(a1, "whatsapp:send:ali:123")  # replay fails
    assert not get(a1)
    clock[0] += 0.08
    assert not confirm(a2, "terminal:rm:x")          # expired fails
    assert pending() == 0
    a3 = new("x:1", "r", ttl_s=5)
    assert deny(a3) and not deny(a3)
    assert revoke_all() == 0
    print("[self-check] channels.approvals OK - action-bound, single-use, expiring, deniable, revocable")
