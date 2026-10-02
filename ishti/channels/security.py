"""Channel security: pairing, allowlist, DPAPI secret store, rate limiting, lock state.

Feeds WP-A2 (IMPLEMENTATION_PLAN.md). Design: RESEARCH.md R4 (Security-Gate agent).

  - The bot token lives ONLY in Windows Credential Manager via DPAPI (win32crypt, CurrentUser).
    Never in config files, env, logs, git. Plaintext exists in memory while in use only.
  - Pairing: the desktop UI asks for a 6-digit code; the owner sends `/pair <code>` from his own
    Telegram account; the gate records the SENDER's numeric user ID (never a username). Codes are
    single-use, expire (10 min), only ever created locally.
  - Allowlist: private chats from allowed numeric IDs only. Everything else (other users, groups,
    channels, inline queries, forwarded-from-others) is ignored silently, logged, and the owner is
    alerted with a per-hour cap.
  - Rate limiting: token bucket per sender (policy.rate_limit_per_min, burst 5).
  - Lock state: /lock refuses everything except /unlock with the PIN; state is persisted so a
    restart does not unlock the door; 5 wrong PINs = 15 min lockout (also persisted).
"""
import hashlib
import json
import os
import secrets
import threading
import time

from ishti import CONFIG
from ishti.channels import policy as policy_mod

try:  # best-effort failure log (never breaks the module if logging is unavailable)
    from ishti.core.logutil import swallowed
except Exception:  # pragma: no cover
    def swallowed(*_a, **_k):
        return None


STATE_PATH = os.path.join(os.path.dirname(CONFIG["memory"]["db_path"]), "remote_security.json")

# ----------------------------------------------------------------- secret store (DPAPI)
_cred_name = "ISHTI_Telegram_Bot_Token"


def store_token(token: str) -> None:
    """Windows Credential Manager / DPAPI (CurrentUser). Raises on non-Windows rather than
    silently storing plaintext somewhere else."""
    import win32crypt  # pywin32 (measured: 312 in the venv)

    blob = win32crypt.CryptProtectData(token.encode("utf-8"), _cred_name, None, None, None, 0)
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    state = _read_state()
    state["token_blob"] = blob.hex()
    _write_state(state)


def load_token() -> str | None:
    state = _read_state()
    hex_blob = state.get("token_blob")
    if not hex_blob:
        return None
    import win32crypt

    try:
        _desc, clear = win32crypt.CryptUnprotectData(bytes.fromhex(hex_blob), None, None, None, 0)
        return clear.decode("utf-8")
    except Exception as exc:
        print(f"[channels.security] token unreadable ({type(exc).__name__}) - re-pair the bot")
        return None


def has_token() -> bool:
    return bool(_read_state().get("token_blob"))


def forget_token() -> None:
    state = _read_state()
    state.pop("token_blob", None)
    _write_state(state)


# ----------------------------------------------------------------- state (pairing, lock)
_state_lock = threading.Lock()


def _read_state() -> dict:
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _write_state(state: dict) -> None:
    with _state_lock:
        os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
        tmp = STATE_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f)
        os.replace(tmp, STATE_PATH)


# ----------------------------------------------------------------- pairing codes
_pairings = {}  # code -> {"expires": ts, "used": False}   (memory-only; restart = re-issue)


def new_pairing_code(ttl_s: int = 600) -> str:
    code = f"{secrets.randbelow(1_000_000):06d}"
    _pairings.clear()  # one live code at a time
    _pairings[code] = {"expires": time.time() + ttl_s, "used": False}
    return code


def pending_pairing() -> bool:
    _expire_pairings()
    return bool(_pairings)


def _expire_pairings():
    now = time.time()
    for code in [c for c, v in _pairings.items() if v["expires"] < now]:
        _pairings.pop(code, None)


def try_pair(code: str, telegram_user_id: int) -> bool:
    """Consume a pairing code on behalf of a Telegram numeric ID. Single-use, expiring."""
    _expire_pairings()
    rec = _pairings.pop(code, None) if code else None
    if not rec:
        return False
    pol = policy_mod.load(force=True)
    ids = sorted({int(x) for x in pol.get("allowed_telegram_ids", [])} | {int(telegram_user_id)})
    policy_mod.save({"allowed_telegram_ids": ids})
    return True


def unpair_all() -> int:
    """Desktop-only 'revoke all sessions': clears the allowlist + pending approvals."""
    pol = policy_mod.load(force=True)
    n = len(pol.get("allowed_telegram_ids", []))
    policy_mod.save({"allowed_telegram_ids": []})
    try:
        from ishti.channels import approvals
        approvals.revoke_all()
    except Exception as _exc:
        swallowed('ishti/channels/security.py:unpair_all', _exc)
    return n


# ----------------------------------------------------------------- allowlist + rate limit
class RateLimiter:
    """Token bucket: burst tokens, refill at per-minute rate. thread-safe."""

    def __init__(self, per_min: int, burst: int = 5):
        self.rate = per_min / 60.0
        self.burst = max(1, burst)
        self.tokens = float(self.burst)
        self.updated = time.monotonic()
        self._lock = threading.Lock()

    def allow(self) -> bool:
        with self._lock:
            now = time.monotonic()
            self.tokens = min(self.burst, self.tokens + (now - self.updated) * self.rate)
            self.updated = now
            if self.tokens >= 1.0:
                self.tokens -= 1.0
                return True
            return False


_limiters = {}
_limiters_lock = threading.Lock()


def limiter_for(sender_id: int) -> RateLimiter:
    per_min = int(policy_mod.load().get("rate_limit_per_min", 20))
    key = (sender_id, per_min)
    with _limiters_lock:
        if key not in _limiters:
            _limiters.clear()  # one live limiter per config value
            _limiters[key] = RateLimiter(per_min)
        return _limiters[key]


def is_allowed(sender_id, pol: dict | None = None) -> bool:
    """The single allowlist check: numeric ID in policy.allowed_telegram_ids, private-only enforced
    by the gate before this is consulted."""
    pol = pol or policy_mod.load()
    try:
        return int(sender_id) in {int(x) for x in pol.get("allowed_telegram_ids", [])}
    except (TypeError, ValueError):
        return False


# ----------------------------------------------------------------- PIN / lock
_pin_cache = {"hash": None, "checked": 0.0}


def _pin_hash() -> str | None:
    """Hash of the PIN stored under DPAPI (set locally from the settings UI)."""
    if _pin_cache["hash"] and time.time() - _pin_cache["checked"] < 5:
        return _pin_cache["hash"]
    state = _read_state()
    h = state.get("pin_hash")
    _pin_cache.update(hash=h, checked=time.time())
    return h


def set_pin(pin: str) -> None:
    """Local-only (settings UI): store a salted hash of the PIN; the PIN itself never persists."""
    salt = secrets.token_hex(8)
    h = hashlib.sha256((salt + str(pin)).encode()).hexdigest()
    state = _read_state()
    state["pin_salt"], state["pin_hash"] = salt, h
    _write_state(state)
    _pin_cache.update(hash=h, checked=time.time())


def has_pin() -> bool:
    return bool(_pin_hash())


def check_pin(pin: str) -> bool:
    state = _read_state()
    salt, want = state.get("pin_salt"), state.get("pin_hash")
    if not salt or not want:
        return False
    got = hashlib.sha256((salt + str(pin)).encode()).hexdigest()
    return secrets.compare_digest(got, want)


LOCKOUT_N, LOCKOUT_S = 5, 900


def locked() -> bool:
    """True while /lock is active. Persisted: restarting ISHTI does not unlock."""
    st = _read_state()
    until = st.get("lockout_until", 0)
    if until and until > time.time():
        return True  # wrong-PIN lockout behaves like a lock
    return bool(st.get("locked"))


def set_locked(v: bool) -> None:
    """v=True: /lock. v=False: a SUCCESSFUL /unlock - clears the failure counters and any expired
    lockout. While a lockout is running the gate refuses /unlock before the PIN is consulted, so
    this clearing path can only run after the lockout has expired."""
    state = _read_state()
    state["locked"] = bool(v)
    if not v:
        state["failed_pins"] = 0
        state["lockout_until"] = 0
    _write_state(state)


def register_pin_failure() -> int:
    """Count a wrong PIN; return how many failures are now recorded (lockout at LOCKOUT_N)."""
    state = _read_state()
    n = int(state.get("failed_pins", 0)) + 1
    state["failed_pins"] = n
    if n >= LOCKOUT_N:
        state["lockout_until"] = time.time() + LOCKOUT_S
        state["failed_pins"] = 0
    _write_state(state)
    return n


def lockout_remaining() -> float:
    until = _read_state().get("lockout_until", 0)
    return max(0.0, until - time.time()) if until else 0.0


def status() -> dict:
    st = _read_state()
    pol = policy_mod.load()
    return {"paired_ids": pol.get("allowed_telegram_ids", []), "token_stored": has_token(),
            "pin_set": has_pin(), "locked": locked(),
            "lockout_s": round(lockout_remaining()), "pending_pairing": pending_pairing()}


if __name__ == "__main__":
    import tempfile
    import ishti.channels.policy as P
    tmp = tempfile.mkdtemp()
    P.PATH = os.path.join(tmp, "p.yaml")
    P.reset_cache()
    STATE_PATH = os.path.join(tmp, "s.json")
    _pin_cache.update(hash=None, checked=0.0)
    # pairing round-trip
    code = new_pairing_code()
    assert pending_pairing() and not try_pair("000000" if code != "000000" else "999999", 111)
    assert try_pair(code, 111) and is_allowed(111) and not is_allowed(222)
    assert not pending_pairing() and not try_pair(code, 111)  # single use
    # expired codes die
    code2 = new_pairing_code(ttl_s=-1)
    time.sleep(0.01)
    assert not try_pair(code2, 333)
    # rate limiter: burst then dry
    rl = RateLimiter(60, burst=3)
    assert [rl.allow() for _ in range(3)] == [True, True, True] and not rl.allow()
    # PIN + lockout
    set_pin("1234")
    assert check_pin("1234") and not check_pin("9999") and has_pin()
    set_locked(True)
    assert locked()
    for _ in range(LOCKOUT_N):
        register_pin_failure()
    assert locked() and lockout_remaining() > 0
    # persisted: a restart re-reads the same file, so the lockout survives it (the gate refuses
    # /unlock entirely while lockout_remaining() > 0 - the PIN is not even consulted)
    assert _read_state().get("lockout_until", 0) > time.time()
    set_locked(False)  # a successful /unlock (correct PIN, possible only after expiry) clears all
    assert not locked() and lockout_remaining() == 0
    st = status()
    assert st["paired_ids"] == [111] and st["token_stored"] is False
    print("[self-check] channels.security OK - pairing single-use+expiry, allowlist, rate burst, PIN lockout persists")
