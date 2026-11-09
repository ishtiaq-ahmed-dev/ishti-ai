"""Shared dashboard HTTP/WebSocket policy, including loopback and tunnel callers."""
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import threading
import time

from ishti.api_sessions import host_authority, origin_ok
from ishti import CONFIG
from ishti.test_mode import scratch_path

LOOPBACK = frozenset(("127.0.0.1", "::1", "localhost"))
PROXY_HEADERS = ("cf-connecting-ip", "x-forwarded-for", "forwarded", "x-real-ip",
                 "x-forwarded-host", "x-forwarded-proto")
SESSION_PATH = Path(scratch_path(str(Path(CONFIG["memory"]["db_path"]).parent / "remote_sessions.json")))
SESSION_IDLE_S = 30 * 86400
SESSION_ABSOLUTE_S = 90 * 86400
MAGIC_TTL_S = 5 * 60


def request_origin_ok(conn):
    scheme = {"ws": "http", "wss": "https"}.get(conn.scope.get("scheme"), conn.scope.get("scheme", "http"))
    forwarded = conn.headers.get("x-forwarded-proto")
    if forwarded:
        # A proxy header also removes implicit loopback trust in authorized().
        if forwarded not in ("http", "https"):
            return False
        scheme = forwarded
    return origin_ok(conn.headers.get("origin"), conn.headers.get("host"),
                     conn.headers.get("sec-fetch-site"), expected_scheme=scheme)


def token_matches(supplied, token):
    return bool(isinstance(supplied, str) and isinstance(token, str) and supplied and token
                and secrets.compare_digest(supplied.encode("utf-8"), token.encode("utf-8")))


def direct_loopback(conn) -> bool:
    authority = host_authority(conn.headers.get("host"))
    return bool(conn.client and conn.client.host in LOOPBACK and authority and authority[0] in LOOPBACK
                and not any(header in conn.headers for header in PROXY_HEADERS))


# Security configuration is changed on the desktop only: a stolen remote session must not be able to
# add control numbers, set the PIN, widen the policy, enable a channel or start pairing.
# (Revoke/disconnect/stop stay remote-capable: they only reduce access.)
_LOCAL_ONLY = (
    ("PUT", re.compile(r"^/api/remote/policy$")),
    ("POST", re.compile(r"^/api/remote/(pin|enable|test|pair/start|pair/cancel|telegram/token)$")),
    ("DELETE", re.compile(r"^/api/remote/(pin|telegram/token)$")),
    ("PUT", re.compile(r"^/api/connectors/[^/]+/config$")),
    ("POST", re.compile(r"^/api/connectors/[^/]+/(connect|test)$")),
)


def requires_local(method: str, path: str) -> bool:
    return any(method.upper() == m and rx.match(path) for m, rx in _LOCAL_ONLY)


def client_ip(conn) -> str:
    if any(header in conn.headers for header in PROXY_HEADERS):
        # The tunnel (ngrok) APPENDS the address it saw; everything before it is whatever the client sent. Taking the
        # first value let an attacker pick a fresh "IP" per guess and walk past the per-IP login lockout.
        forwarded = conn.headers.get("x-forwarded-for", "").rsplit(",", 1)[-1].strip()
        return forwarded[:80] or str(conn.headers.get("cf-connecting-ip") or conn.headers.get("x-real-ip") or "proxy")[:80]
    return str(conn.client.host if conn.client else "unknown")[:80]


class OwnerSessionStore:
    """Persistent hashed owner sessions and single-use magic codes."""

    def __init__(self, path=SESSION_PATH, now=time.time):
        self.path = Path(path)
        self.now = now
        self._lock = threading.RLock()

    @staticmethod
    def _hash(value: str) -> str:
        return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()

    def _read(self) -> dict:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {"sessions": {}, "magic": {}}
        except (OSError, ValueError, json.JSONDecodeError):
            return {"sessions": {}, "magic": {}}

    def _write(self, value: dict):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
        try:
            tmp.write_text(json.dumps(value, separators=(",", ":")), encoding="utf-8")
            os.replace(tmp, self.path)
        finally:
            tmp.unlink(missing_ok=True)

    def _prune(self, data: dict) -> int:
        now, removed = self.now(), 0
        sessions = data.setdefault("sessions", {})
        for key, row in list(sessions.items()):
            if now - float(row.get("last_seen", 0)) > SESSION_IDLE_S or now - float(row.get("created", 0)) > SESSION_ABSOLUTE_S:
                sessions.pop(key, None); removed += 1
        magic = data.setdefault("magic", {})
        for key, row in list(magic.items()):
            if now > float(row.get("expires", 0)):
                magic.pop(key, None)
        return removed

    def create(self, *, ip: str, device: str) -> str:
        token = secrets.token_urlsafe(32)
        with self._lock:
            data = self._read(); self._prune(data); now = self.now(); digest = self._hash(token)
            data["sessions"][digest] = {"id": digest[:16], "ip": str(ip)[:80], "device": str(device or "Browser")[:120],
                                         "created": now, "last_seen": now}
            self._write(data)
        return token

    def get(self, token: str, *, touch=True) -> dict | None:
        if not token:
            return None
        with self._lock:
            data = self._read(); changed = bool(self._prune(data)); row = data["sessions"].get(self._hash(token))
            if row and touch:
                row["last_seen"] = self.now(); changed = True
            if changed:
                self._write(data)
            return dict(row) if row else None

    def list(self) -> list[dict]:
        with self._lock:
            data = self._read(); changed = bool(self._prune(data))
            if changed:
                self._write(data)
            return sorted((dict(row) for row in data["sessions"].values()), key=lambda row: row["last_seen"], reverse=True)

    def revoke(self, session_id: str) -> bool:
        with self._lock:
            data = self._read()
            key = next((key for key, row in data.setdefault("sessions", {}).items() if row.get("id") == session_id), None)
            if key is None:
                return False
            data["sessions"].pop(key, None); self._write(data); return True

    def revoke_token(self, token: str) -> bool:
        with self._lock:
            data = self._read(); removed = data.setdefault("sessions", {}).pop(self._hash(token), None) is not None
            if removed:
                self._write(data)
            return removed

    def revoke_all(self) -> int:
        with self._lock:
            data = self._read(); count = len(data.setdefault("sessions", {})); data["sessions"] = {}; self._write(data); return count

    def create_magic(self) -> str:
        code = secrets.token_urlsafe(32)
        with self._lock:
            data = self._read(); self._prune(data)
            data["magic"][self._hash(code)] = {"expires": self.now() + MAGIC_TTL_S}
            self._write(data)
        return code

    def consume_magic(self, code: str) -> bool:
        with self._lock:
            data = self._read(); self._prune(data); row = data.setdefault("magic", {}).pop(self._hash(code), None)
            self._write(data)
            return bool(row and self.now() <= float(row.get("expires", 0)))


class LoginGuard:
    def __init__(self, now=time.time, per_ip_limit=5, global_limit=25, window_s=600):
        self.now, self.per_ip_limit, self.global_limit, self.window_s = now, per_ip_limit, global_limit, window_s
        self._failures: list[tuple[float, str]] = []
        self._lockouts: dict[str, float] = {}
        self._global_until = 0.0
        self._lock = threading.Lock()

    def remaining(self, ip: str) -> int:
        with self._lock:
            return max(0, int(max(self._lockouts.get(ip, 0), self._global_until) - self.now()))

    def fail(self, ip: str) -> int:
        with self._lock:
            now = self.now(); self._failures = [(ts, who) for ts, who in self._failures if now - ts < self.window_s]
            self._failures.append((now, ip))
            if sum(who == ip for _, who in self._failures) >= self.per_ip_limit:
                self._lockouts[ip] = now + self.window_s
            if len(self._failures) >= self.global_limit:
                self._global_until = now + self.window_s
            return max(0, int(max(self._lockouts.get(ip, 0), self._global_until) - now))

    def success(self, ip: str):
        with self._lock:
            self._failures = [(ts, who) for ts, who in self._failures if who != ip]
            self._lockouts.pop(ip, None)


owner_sessions = OwnerSessionStore()
login_guard = LoginGuard()


def authorized(conn, token, sessions=None):
    if not request_origin_ok(conn):
        return False
    if direct_loopback(conn):
        return True
    scheme, _, credential = conn.headers.get("authorization", "").partition(" ")
    bearer = credential.strip() if scheme.lower() == "bearer" else ""
    if token_matches(bearer, token):
        return True
    return bool((sessions or owner_sessions).get(conn.cookies.get("ishti_session", "")))
