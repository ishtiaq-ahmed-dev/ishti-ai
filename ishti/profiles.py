"""Personal profile (the only profile; the Business option was removed for the public release).

One namespace. The active profile is stored in the runs DB meta table (one source of truth);
each chat binds a profile at creation and pending runs keep theirs. Memory namespace keys are
prefixed so personal and business stores never cross-read (verified in tests).
"""
from __future__ import annotations

from ishti.core.logutil import swallowed

PROFILES = ("personal",)
_active = "personal"


def valid(name: str | None) -> str:
    return name if name in PROFILES else "personal"


def active() -> str:
    return _active


def set_active(name: str) -> str:
    global _active
    _active = valid(name)
    try:
        from ishti import runs
        with runs._lock:
            c = runs.conn()
            c.execute("INSERT INTO meta(key, value) VALUES('active_profile', ?) "
                      "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (_active,))
            c.commit()
    except Exception as exc:
        swallowed("ishti/profiles.py:set_active", exc)
    return _active


def load_active() -> str:
    """Restore the persisted active profile at boot (module import order safe)."""
    global _active
    try:
        from ishti import runs
        with runs._lock:
            row = runs.conn().execute(
                "SELECT value FROM meta WHERE key = 'active_profile'").fetchone()
        if row and row["value"] in PROFILES:
            _active = row["value"]
    except Exception as exc:
        swallowed("ishti/profiles.py:load_active", exc)
    return _active


# ---------------------------------------------------------------- per-profile policy layer

LAYER = {
    "personal": {
        "tone": "warm, casual, personal",
        "skills": [],  # empty = no restriction beyond the skill's own tier
        "tier_default": "T1",
        "crm": "off",
        "memory_ns": "personal",
    },
}


def layer(profile: str | None = None) -> dict:
    return LAYER[valid(profile or active())]


def memory_ns(profile: str | None = None) -> str:
    """Namespace prefix for memory lookups/writes. No cross-reads by construction."""
    return f"profile:{layer(profile)['memory_ns']}"
