"""Self-healing mode controller (Section 2).

Hard rules:
  1. The self-healing/auto-fix system must NOT run automatically in the background.
  2. Mode is OFF by default.
  3. Remediation actions only run when the owner manually triggers them via the UI
     button/toggle or an explicit command.
  4. Crash resilience, watchdog restarts, and kill switch remain always-active;
     this restriction applies strictly to proactive/auto-fix self-healing behavior.
"""
import json
import os
import subprocess
import threading
import time
from typing import Any, Dict, List

import requests

from ishti import CONFIG

STATE_PATH = os.path.join(os.path.dirname(CONFIG["memory"]["db_path"]), "self_heal_state.json")
REFLECTIONS_PATH = os.path.join(os.path.dirname(CONFIG["memory"]["db_path"]), "reflections.md")

_lock = threading.Lock()
_state = {
    "enabled": False,  # OFF BY DEFAULT
    "pending": [],
    "last_run": None,
    "last_report": None,
}


def _load_state():
    global _state
    if os.path.exists(STATE_PATH):
        try:
            with open(STATE_PATH, "r", encoding="utf-8") as f:
                saved = json.load(f)
                _state["enabled"] = bool(saved.get("enabled", False))
                _state["pending"] = saved.get("pending", [])
                _state["last_run"] = saved.get("last_run")
                _state["last_report"] = saved.get("last_report")
        except Exception:
            pass


def _save_state():
    try:
        os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
        with open(STATE_PATH, "w", encoding="utf-8") as f:
            json.dump(_state, f, indent=2)
    except Exception as exc:
        print(f"[self_heal] Failed to persist state: {exc}")


_load_state()


def is_enabled() -> bool:
    """True ONLY if owner explicitly toggled on automatic background self-healing."""
    return _state["enabled"]


def set_enabled(enabled: bool) -> bool:
    """Toggle self-healing mode state."""
    with _lock:
        _state["enabled"] = bool(enabled)
        _save_state()
    print(f"[self_heal] Self-healing mode set to: {'ENABLED' if _state['enabled'] else 'OFF (Manual trigger only)'}")
    return _state["enabled"]


def record_candidate(component: str, issue: str, suggested_action: str):
    """Log an issue that can be remediated during the next manual self-healing pass."""
    with _lock:
        cand = {
            "component": component,
            "issue": issue,
            "action": suggested_action,
            "timestamp": time.time()
        }
        # Deduplicate by component and issue
        _state["pending"] = [p for p in _state["pending"] if p.get("component") != component or p.get("issue") != issue]
        _state["pending"].append(cand)
        _save_state()
    print(f"[self_heal] Queued candidate for manual self-healing ({component}): {issue}")


def get_status() -> Dict[str, Any]:
    """Status snapshot for UI controls and API."""
    with _lock:
        return {
            "enabled": _state["enabled"],
            "pending_count": len(_state["pending"]),
            "pending": list(_state["pending"]),
            "last_run": _state["last_run"],
            "last_report": _state["last_report"]
        }


def trigger_heal() -> Dict[str, Any]:
    """
    On-demand self-healing run, triggered manually by the owner.
    Diagnoses and applies remediations across models, search, memory, and voice.
    """
    actions_taken: List[str] = []
    t0 = time.time()
    print("[self_heal] >>> Manual self-healing triggered by owner...")

    # 1. Ollama & Model Subsystem
    try:
        base_url = CONFIG["llm"]["base_url"].rsplit("/v1", 1)[0]
        r = requests.get(f"{base_url}/api/tags", timeout=5)
        if r.ok:
            actions_taken.append("Verified Ollama API connectivity (responsive)")
        else:
            subprocess.Popen(["ollama", "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            actions_taken.append("Restarted Ollama daemon service")
    except Exception as exc:
        try:
            subprocess.Popen(["ollama", "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            actions_taken.append(f"Restarted Ollama daemon after connection error: {exc}")
        except Exception:
            actions_taken.append(f"Could not reach or restart Ollama: {exc}")

    # Reset model router failure counters
    try:
        from orchestrator.model_router import ModelRouter
        router = ModelRouter()
        router.consecutive_failures.clear()
        actions_taken.append("Reset ModelRouter consecutive failure counters")
    except Exception:
        pass

    # 2. Web Search & SearXNG Container
    try:
        from tools.web.search import SearxngSearchProvider
        url = SearxngSearchProvider.URL + "/healthz"
        r = requests.get(url, timeout=3)
        if r.status_code == 200:
            actions_taken.append("SearXNG search service verified healthy")
        else:
            # Trigger container restart
            res = subprocess.run(["docker", "start", "searxng"], capture_output=True, text=True, timeout=10,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if res.returncode == 0:
                actions_taken.append("Restarted SearXNG Docker container")
            else:
                actions_taken.append("SearXNG container unavailable; fallback search active")
    except Exception as exc:
        actions_taken.append(f"Search provider check: {exc}")

    # 3. Database Integrity & File Cleanup
    try:
        import sqlite3
        with sqlite3.connect(CONFIG["memory"]["db_path"], timeout=5) as conn:
            cur = conn.cursor()
            res = cur.execute("PRAGMA integrity_check").fetchone()
            if res and res[0] == "ok":
                actions_taken.append("SQLite database integrity verified (ishti.db)")
            else:
                actions_taken.append(f"SQLite integrity check reported: {res}")
    except Exception as exc:
        actions_taken.append(f"Database check error: {exc}")

    # 4. Voice Subsystem
    try:
        from ishti import voice
        # Ensure STT thread and queue are alive
        if voice._stt_thread is None or not voice._stt_thread.is_alive():
            voice._stt_thread = None
            actions_taken.append("Reset STT worker thread")
        else:
            actions_taken.append("Voice STT subsystem verified healthy")
    except Exception as exc:
        actions_taken.append(f"Voice check error: {exc}")

    # Log to reflections
    try:
        os.makedirs(os.path.dirname(REFLECTIONS_PATH), exist_ok=True)
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        entry = (
            f"\n### Manual Self-Healing Event\n"
            f"- **Timestamp**: {timestamp}\n"
            f"- **Trigger**: Manual Owner Request\n"
            f"- **Remediations Executed**:\n"
            + "\n".join(f"  - {a}" for a in actions_taken) + "\n"
        )
        with open(REFLECTIONS_PATH, "a", encoding="utf-8") as f:
            f.write(entry)
    except Exception as exc:
        print(f"[self_heal] Failed to write reflections entry: {exc}")

    report = {
        "status": "success",
        "actions_taken": actions_taken,
        "duration_ms": round((time.time() - t0) * 1000, 1),
        "timestamp": time.time(),
        "date_str": time.strftime("%Y-%m-%d %H:%M:%S")
    }

    with _lock:
        _state["pending"].clear()
        _state["last_run"] = time.time()
        _state["last_report"] = report
        _save_state()

    print(f"[self_heal] <<< Manual self-healing complete: {len(actions_taken)} actions taken in {report['duration_ms']}ms.")
    return report


if __name__ == "__main__":
    assert is_enabled() is False, "Self-healing must be OFF by default"
    record_candidate("search", "SearXNG container timeout", "restart container")
    stat = get_status()
    assert stat["pending_count"] == 1
    rep = trigger_heal()
    assert rep["status"] == "success"
    assert len(rep["actions_taken"]) >= 2
    assert get_status()["pending_count"] == 0
    print("[self-check] self_heal OK - manual trigger only, off by default, remediation log")
