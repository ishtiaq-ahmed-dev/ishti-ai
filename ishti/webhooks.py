"""Inbound webhooks - OpenClaw-style event triggers. Another service (GitHub, a form, IFTTT, a home sensor, a script)
calls a secret URL and ISHTI runs the instruction the owner attached, like a scheduled task: in its own session, an
action that needs a yes waits in the bell, the answer is announced.

  "create a webhook called deploy that summarizes the latest GitHub release"
  "create a webhook called form that saves a note: new form entry {payload}"   ({payload} = the request body)

Safety: each hook has its own random token (only its SHA-256 is stored; compared in constant time); the request body
reaches the instruction only where the owner wrote {payload}, capped and marked as outside data; 30 calls a minute per
hook at most."""
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from collections import deque

from ishti import ROOT
from ishti.test_mode import scratch_path

PATH = scratch_path(os.path.join(ROOT, "data", "webhooks.json"))
PAYLOAD_MAX = 4000
PER_MINUTE = 30
_lock = threading.Lock()
_calls: dict = {}


def _load() -> dict:
    try:
        with open(PATH, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save(data: dict):
    os.makedirs(os.path.dirname(PATH), exist_ok=True)
    tmp = PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, PATH)


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create(name: str, instruction: str) -> tuple[str, str]:
    """(hook id, token). The token is shown once; only its hash is kept."""
    hook_id, token = secrets.token_hex(6), secrets.token_urlsafe(24)
    with _lock:
        data = _load()
        data[hook_id] = {"name": name, "instruction": instruction, "token_sha256": _digest(token),
                         "created": time.time(), "calls": 0, "last_called": 0}
        _save(data)
    return hook_id, token


def hooks() -> dict:
    return _load()


def delete(key: str) -> str | None:
    """Delete by id or name; the deleted hook's name, or None."""
    with _lock:
        data = _load()
        hook_id = key if key in data else next((k for k, v in data.items() if v.get("name") == key), None)
        if not hook_id:
            return None
        name = data.pop(hook_id).get("name", hook_id)
        _save(data)
    return name


def verify(hook_id: str, token: str):
    """The hook when the token is right, else None (a wrong id and a wrong token look the same to a caller)."""
    hook = _load().get(str(hook_id))
    if not hook or not token or not hmac.compare_digest(hook.get("token_sha256", ""), _digest(str(token))):
        return None
    return hook


def allow(hook_id: str, now: float | None = None) -> bool:
    now = time.time() if now is None else now
    with _lock:
        recent = _calls.setdefault(hook_id, deque())
        while recent and now - recent[0] > 60:
            recent.popleft()
        if len(recent) >= PER_MINUTE:
            return False
        recent.append(now)
    return True


def task_text(hook: dict, payload: str) -> str:
    """The instruction to run; the body goes in only where the owner wrote {payload}, fenced as outside data."""
    instruction = str(hook.get("instruction") or "")
    if "{payload}" not in instruction:
        return instruction
    from ishti import untrusted
    return instruction.replace("{payload}", untrusted.fence(str(payload or "")[:PAYLOAD_MAX], "webhook request"))


def record_call(hook_id: str):
    with _lock:
        data = _load()
        if hook_id in data:
            data[hook_id]["calls"] = int(data[hook_id].get("calls") or 0) + 1
            data[hook_id]["last_called"] = time.time()
            _save(data)


def url(hook_id: str) -> str:
    try:
        from ishti import lifecycle
        port = lifecycle._st.get("port", 8888)
    except Exception:
        port = 8888
    return f"http://127.0.0.1:{port}/api/hooks/{hook_id}"
