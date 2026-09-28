"""Which architecture answers right now: exactly one owner of that state.

Backends: "arch1" - the existing dispatcher (rules / Laya / skills); "arch2" - this
package's two-pass pipeline. The setting lives in ishti/config.json so it survives
restarts, is hot-reloaded by set_active(), and everything else (dashboard, voice
command, API) goes through here. Last-switch metadata is kept for the dashboard.
"""
import json
import os
import re
import threading
import time

from ishti import CONFIG, ROOT

CFG = CONFIG.setdefault("arch", {"active": "arch1"})
_lock = threading.Lock()
_state = {"active": str(CFG.get("active") or "arch1"), "switched_at": 0.0, "switched_by": "startup"}

DESC = {
    "arch1": "ARCH1 - the proven dispatcher: instant reflexes, deterministic rules, Laya typed-decision "
             "routing, then 50+ specialist skills. Best-tested path.",
    "arch2": "ARCH2 - two-pass brain: intent router, then Qwen3-14B thinks/plans/calls tools, every "
             "tool run is validated, then Qwen3-14B writes the final answer. Four-layer memory "
             "(semantic / episodic / procedural / relationship graph) learns on top.",
}


def active() -> str:
    return _state["active"]


def arch2_cfg() -> dict:
    return CFG.get("arch2", {})


def describe() -> dict:
    return {"active": _state["active"],
            "backends": [{"id": k, "description": v, "selected": k == _state["active"]} for k, v in DESC.items()],
            "switched_at": _state["switched_at"], "switched_by": _state["switched_by"]}


def set_active(backend: str, by: str = "dashboard") -> dict:
    """Switch which pipeline handles requests. Persisted to config.json; threads see it at once."""
    backend = str(backend or "").strip().lower()
    aliases = {"1": "arch1", "2": "arch2", "arch 1": "arch1", "arch 2": "arch2",
               "default": "arch1", "new": "arch2"}
    backend = aliases.get(backend, backend)
    if backend not in DESC:
        raise ValueError(f"unknown architecture '{backend}' (arch1 or arch2)")
    with _lock:
        changed = backend != _state["active"]
        _state.update(active=backend, switched_at=time.time() if changed else _state["switched_at"],
                      switched_by=by if changed else _state["switched_by"])
        CFG["active"] = backend
        cfg_path = os.path.join(ROOT, "ishti", "config.json")
        try:
            with open(cfg_path, encoding="utf-8") as f:
                disk = json.load(f)
            disk.setdefault("arch", {})["active"] = backend
            with open(cfg_path, "w", encoding="utf-8") as f:
                json.dump(disk, f, indent=2, ensure_ascii=False)
        except OSError as exc:
            print(f"[arch] config.json write skipped: {exc}")
    return describe()


ARCH_WORD = re.compile(r"\b(?:arch(?:itecture)?\s*(?P<n>[12])|use\s+the\s+new\s+arch(?:itecture)?|"
                       r"switch\s+(?:to\s+)?(?:the\s+)?(?:new|old|default)\s+arch(?:itecture)?)\b", re.I)


def handle_command(text: str) -> str | None:
    """Voice/chat command: 'use arch 2', 'switch to architecture 1', 'use the new architecture'.
    Returns the reply, or None when the text isn't an architecture command."""
    m = ARCH_WORD.search(text or "")
    if not m or len((text or "").split()) > 8:
        return None
    n = (m.group("n") or "").strip()
    if not n:
        n = "2" if re.search(r"new", text, re.I) else "1"
    try:
        d = set_active(f"arch{n}", by="owner command")
    except ValueError:
        return None
    return f"Switched to {d['active'].upper()}. {DESC[d['active']]}"
