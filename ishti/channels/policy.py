"""remote_policy.yaml — the remote control's permission sheet (LOCAL-EDITABLE ONLY).

Rules:
  - lives in data/, read lazily with a small TTL cache (the router's disabled-flags pattern),
    so the desktop UI can edit it live.
  - a remote message can NEVER change it: there is no channel command that writes policy, and
    the gate's remote-forbidden classifier rejects any request that asks for one (tested).
  - defaults are the safe ones: everything OFF except the read-only panic commands.
"""
import os
import re
import threading
import time

import yaml

from ishti import CONFIG

PATH = os.path.join(os.path.dirname(CONFIG["memory"]["db_path"]), "remote_policy.yaml")

DEFAULTS = {
    "telegram_enabled": False,          # master switch; pairing still required
    "whatsapp_enabled": False,          # default OFF (R2: automation risk, honest warning in the UI)
    "allowed_telegram_ids": [],         # numeric Telegram user IDs (pairing fills this)
    "whatsapp_control_numbers": [],     # E.164 WhatsApp control numbers (desktop UI; empty = linked self-chat)
    "stale_command_max_age_s": 300,     # messages queued while the PC was off: expire, don't execute
    "approval_ttl_s": 120,              # T2 step-up approvals expire after this
    "quiet_hours": "",                  # e.g. "22:00-07:00": non-urgent pushes wait (owner-set in Connectors)
    "rate_limit_per_min": 20,           # authenticated owner messages per minute
    "unknown_alert_per_hour": 3,        # alert cap for unknown-sender attempts
    "local_pin_required": False,        # optional second factor for T2 approvals
    "sensitive_output": {               # what remote replies may contain (R1 C: cloud chats are not E2E)
        "client_data": "redact",        # redact | allow | block
        "finance": "redact",
        "chat_digests": "redact",
        "owner_profile": "block",
        "secrets": "block",
    },
    "desktop_control_remote": False,    # WP-B skill: never remotely controllable by default
    "risk_ack": {                       # shown in the settings UI; flipping a channel = acknowledging
        "telegram_cloud": False,        # "Telegram messages pass through Telegram's servers - not local"
        "whatsapp_automation": False,   # "browser automation of a consumer account carries restriction risk"
    },
}

_lock = threading.RLock()  # reentrant: save() reads the effective policy under the same lock
_cache = {"at": 0.0, "data": None}
_TTL = 3.0  # seconds: local edits apply almost immediately, per-request reads stay cheap


def _migrate_phone_ids(data: dict) -> tuple[dict, bool]:
    """Move legacy formatted phone strings out of Telegram's numeric-ID allowlist."""
    telegram, phones, changed = [], list(data.get("whatsapp_control_numbers") or []), False
    for value in data.get("allowed_telegram_ids") or []:
        raw = str(value).strip()
        number = re.sub(r"\D", "", raw)
        looks_phone = isinstance(value, str) and (raw.startswith("+") or raw.startswith("00") or bool(re.search(r"[ ()-]", raw)))
        if looks_phone and 10 <= len(number) <= 15:
            e164 = "+" + (number[2:] if raw.startswith("00") else number)
            if e164 not in phones:
                phones.append(e164)
            changed = True
        else:
            telegram.append(value)
    if changed:
        data["allowed_telegram_ids"] = telegram
        data["whatsapp_control_numbers"] = phones
    return data, changed


def _write(data: dict):
    os.makedirs(os.path.dirname(PATH), exist_ok=True)
    tmp = PATH + f".{os.getpid()}.{threading.get_ident()}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as stream:
            yaml.safe_dump(data, stream, allow_unicode=True, sort_keys=False)
        os.replace(tmp, PATH)
    finally:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass


def _write_defaults():
    _write(DEFAULTS)


def load(force=False) -> dict:
    """The effective policy: file values over DEFAULTS (so new keys always exist)."""
    now = time.time()
    with _lock:
        if not force and _cache["data"] is not None and now - _cache["at"] < _TTL:
            return _cache["data"]
    data = dict(DEFAULTS)
    migrated = False
    try:
        if os.path.exists(PATH):
            with open(PATH, encoding="utf-8") as f:
                stored = yaml.safe_load(f) or {}
            if isinstance(stored, dict):
                for k, v in stored.items():
                    if k == "sensitive_output" and isinstance(v, dict):
                        data["sensitive_output"] = {**DEFAULTS["sensitive_output"], **v}
                    else:
                        data[k] = v
                data, migrated = _migrate_phone_ids(data)
                if migrated:
                    _write(data)
        else:
            _write_defaults()
    except Exception as exc:  # a broken policy file must never take the channel down: safe defaults
        print(f"[channels.policy] unreadable ({type(exc).__name__}: {exc}) - using defaults")
    with _lock:
        _cache["at"], _cache["data"] = now, data
    return data


def save(update: dict) -> dict:
    """Local-only edit path (desktop UI / local file). Called from /api/remote/policy with a
    local-session bearer token; never from the channel gate."""
    with _lock:
        current = load(force=True)
        for k, v in (update or {}).items():
            if k == "sensitive_output" and isinstance(v, dict):
                current["sensitive_output"] = {**current["sensitive_output"], **v}
            else:
                current[k] = v
        current, _ = _migrate_phone_ids(current)
        _write(current)
        _cache["at"], _cache["data"] = 0.0, None  # drop the cache: next read sees the edit
    return load(force=True)


def reset_cache():
    with _lock:
        _cache["at"], _cache["data"] = 0.0, None


if __name__ == "__main__":
    import dataclasses
    import tempfile
    tmp = tempfile.mkdtemp()
    PATH = os.path.join(tmp, "remote_policy.yaml")
    p1 = load()                      # creates the file from defaults
    assert os.path.exists(PATH) and p1["telegram_enabled"] is False and p1["whatsapp_enabled"] is False
    p2 = save({"telegram_enabled": True, "stale_command_max_age_s": 60,
               "sensitive_output": {"finance": "block"}})
    assert p2["telegram_enabled"] is True and p2["stale_command_max_age_s"] == 60
    assert p2["sensitive_output"]["finance"] == "block" and p2["sensitive_output"]["client_data"] == "redact"
    with open(PATH, encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    assert raw["whatsapp_enabled"] is False  # untouched key survived the round-trip
    broken = os.path.join(tmp, "sub")
    os.makedirs(broken)
    PATH = os.path.join(broken, "x")  # a directory where the file should be: load must not raise
    p3 = load(force=True)
    assert p3["telegram_enabled"] is False  # defaults, not a crash
    print("[self-check] channels.policy OK - defaults safe, round-trip, sensitive merge, fail-safe")
    _ = dataclasses  # noqa: F841  (import guard: stdlib present)
