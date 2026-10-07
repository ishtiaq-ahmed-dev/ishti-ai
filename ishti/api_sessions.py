"""Browser session tokens + origin policy (WP-OD, matrix OD-12/23; open-dots auth_service.py).

Hardening layer ON TOP of the web UI's existing bearer token (`interface/web_ui/server.py`
keeps its own token; nothing here replaces or weakens it):

  * tokens are 32-byte urlsafe random strings; only their SHA-256 hex is ever stored, so a
    dump of memory/state cannot be replayed as a session
  * sessions live in MEMORY only - restarting ISHTI invalidates every session (OD-12)
  * rotation: refresh() mints a new token and revokes the old one immediately
  * TTL + idle expiry, revoke(token) / revoke_all()
  * origin policy for state-changing requests: modern browsers send Sec-Fetch-Site, and
    "cross-site"/"same-site" values are refused regardless of token; when an Origin header
    is present it must match the Host. Legacy/non-browser clients that send neither still
    work (they already hold the bearer token)
  * documented cookie posture: HttpOnly + Secure (https) + SameSite (web_security.OwnerSessionStore
    issues the browser `ishti_session` cookie; the long-lived API key never goes in a cookie or URL)

Never raises: every method returns a value (None/False/0).
Self-check: python ishti/api_sessions.py
"""
from __future__ import annotations

import hashlib
import os
import re
import time
import urllib.parse

DEFAULT_TTL_S = 12 * 3600
DEFAULT_IDLE_S = 2 * 3600
CROSS_SITE = ("cross-site", "same-site")          # both are cross-origin contexts
COOKIE_POSTURE = {"httponly": True, "secure": True, "samesite": "strict"}


def hash_token(token) -> str:
    return hashlib.sha256(str(token or "").encode("utf-8")).hexdigest()


def session_cookie_flags() -> dict:
    """The posture server.py must keep for its cookie (OD-23)."""
    return dict(COOKIE_POSTURE)


def host_authority(host, scheme="http"):
    """Parse a single HTTP Host authority, refusing URL/userinfo/port tricks."""
    try:
        raw = str(host or "")
        if not raw or any(c.isspace() for c in raw) or any(c in raw for c in "/\\?#@,%"):
            return None
        if not re.fullmatch(r"(?:\[[0-9a-fA-F:.]+\]|[^:\[\]]+)(?::[0-9]+)?", raw):
            return None
        parts = urllib.parse.urlsplit("//" + raw)
        if not parts.hostname or parts.path or parts.query or parts.fragment or raw.endswith(":"):
            return None
        port = parts.port
        if port == 0:
            return None
        return parts.hostname.lower().rstrip("."), port or (443 if scheme == "https" else 80)
    except (TypeError, ValueError):
        return None


def origin_ok(origin, host, sec_fetch_site, expected_scheme=None) -> bool:
    """True when a state-changing request may proceed. Rules (in order):
      1. Sec-Fetch-Site cross-site/same-site -> False (browser tells on itself)
      2. no Origin header -> True (curl/tests/non-browser; the bearer token still applies)
      3. Origin present -> scheme http(s), and its host[:port] must equal Host[:port]
      4. anything unparseable -> False
    """
    try:
        site = str(sec_fetch_site or "").strip().lower()
        if site in CROSS_SITE:
            return False
        raw = str(origin or "").strip()
        if not raw:
            return True
        parts = urllib.parse.urlsplit(raw)
        if (parts.scheme not in ("http", "https") or not parts.hostname
                or parts.username is not None or parts.password is not None
                or parts.path or parts.query or parts.fragment
                or any(c.isspace() for c in raw)):
            return False
        if expected_scheme is not None and parts.scheme != expected_scheme:
            return False
        o_host = parts.hostname.lower().rstrip(".")
        o_port = parts.port or (443 if parts.scheme == "https" else 80)
        authority = host_authority(parts.netloc, parts.scheme)
        return bool(authority and authority == (o_host, o_port) == host_authority(host, parts.scheme))
    except Exception:
        return False


class SessionStore:
    """In-memory, hashed browser sessions. One instance per process; a restart empties it."""

    def __init__(self, ttl_s: float = DEFAULT_TTL_S, idle_s: float = DEFAULT_IDLE_S, now=None):
        self.ttl_s = float(ttl_s)
        self.idle_s = float(idle_s)
        self._now = now or time.time
        self._rows: dict[str, dict] = {}

    # ---- lifecycle ------------------------------------------------------------
    def create(self, subject: str = "owner", meta: dict | None = None):
        try:
            token = os.urandom(32).hex()
            now = self._now()
            self._rows[hash_token(token)] = {"subject": str(subject or "owner")[:120],
                                             "meta": dict(meta or {}), "created": now,
                                             "last_seen": now}
            return token
        except Exception:
            return None

    def get(self, token):
        """The session row (subject/meta), sliding the idle window; None if unknown/expired."""
        try:
            row = self._rows.get(hash_token(token))
            if not row:
                return None
            now = self._now()
            if now - row["created"] > self.ttl_s or now - row["last_seen"] > self.idle_s:
                self._rows.pop(hash_token(token), None)
                return None
            row["last_seen"] = now
            return {"subject": row["subject"], "meta": dict(row["meta"]),
                    "created": row["created"], "last_seen": row["last_seen"]}
        except Exception:
            return None

    def refresh(self, token):
        """Rotate: the old token dies the moment the new one is minted (or None)."""
        try:
            row = self.get(token)
            if row is None:
                return None
            self._rows.pop(hash_token(token), None)
            return self.create(row["subject"], row["meta"])
        except Exception:
            return None

    def revoke(self, token) -> bool:
        try:
            return self._rows.pop(hash_token(token), None) is not None
        except Exception:
            return False

    def revoke_all(self) -> int:
        try:
            n = len(self._rows)
            self._rows.clear()
            return n
        except Exception:
            return 0

    def expire(self) -> int:
        """Drop everything past ttl/idle now; returns how many rows went away."""
        try:
            now = self._now()
            dead = [k for k, r in self._rows.items()
                    if now - r["created"] > self.ttl_s or now - r["last_seen"] > self.idle_s]
            for k in dead:
                self._rows.pop(k, None)
            return len(dead)
        except Exception:
            return 0

    def count(self) -> int:
        try:
            return len(self._rows)
        except Exception:
            return 0


if __name__ == "__main__":
    clock = [1_000_000.0]
    store = SessionStore(ttl_s=100, idle_s=50, now=lambda: clock[0])
    tok = store.create("owner", {"ua": "test"})
    assert tok and store.get(tok) and store.get(tok)["subject"] == "owner"
    # only the hash is stored: the raw token appears nowhere in the store
    assert tok not in store._rows and hash_token(tok) in store._rows
    assert store.count() == 1
    # rotation kills the old token immediately
    tok2 = store.refresh(tok)
    assert tok2 and tok2 != tok and store.get(tok) is None and store.get(tok2)
    # idle + ttl expiry
    clock[0] += 51
    assert store.get(tok2) is None, "idle expiry"
    tok3 = store.create("owner")
    clock[0] += 101
    assert store.get(tok3) is None, "ttl expiry"
    tok4 = store.create("owner")
    assert store.revoke(tok4) and not store.revoke(tok4)
    store.create("owner") and store.create("owner")
    assert store.revoke_all() == 2 and store.count() == 0
    # a second store knows nothing about the first one's token (restart = invalid)
    a = SessionStore(); t = a.create()
    assert SessionStore().get(t) is None, "sessions do not survive a restart"
    # rotation of an unknown token is None; junk never raises
    for junk in (None, 42, [], {}):
        assert store.get(junk) is None and store.refresh(junk) is None and store.revoke(junk) is False
    # origin policy
    assert origin_ok(None, "127.0.0.1:8888", None) is True, "non-browser clients still work"
    assert origin_ok("http://127.0.0.1:8888", "127.0.0.1:8888", None) is True
    assert origin_ok("http://localhost:8888", "localhost:8888", "same-origin") is True
    assert origin_ok("http://evil.example", "127.0.0.1:8888", None) is False
    assert origin_ok("http://127.0.0.1:8888", "127.0.0.1:8888", "cross-site") is False
    assert origin_ok("http://127.0.0.1:8888", "127.0.0.1:8888", "same-site") is False, "same-site is still cross-origin"
    assert origin_ok("file:///etc/passwd", "127.0.0.1:8888", None) is False
    assert origin_ok("http://127.0.0.1.evil.com", "127.0.0.1:8888", None) is False
    assert origin_ok("http://127.0.0.1:9999", "127.0.0.1:8888", None) is False, "port matters"
    assert origin_ok("junk", "127.0.0.1", None) is False and origin_ok(42, None, "cross-site") is False
    assert session_cookie_flags() == {"httponly": True, "secure": True, "samesite": "strict"}
    print("[self-check] api_sessions OK - hashed in-memory sessions (restart-invalid), rotation, "
          "ttl/idle expiry, cross-site origin refusal, strict cookie posture")
