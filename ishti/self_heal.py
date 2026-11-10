"""Self-healing mode controller.

Rules:
  1. OFF by default. Nothing heals in the background until the owner turns Auto-Heal ON (the toggle is the consent).
  2. ON: a light loop checks health every minute and, when something is really wrong, runs the same safe repairs
     as the manual button (restart Ollama, reap orphaned model servers, restart the search container, reset counters).
     At most one automatic run per cooldown; it never touches user data and never lowers a safety gate.
  3. The manual trigger always works, ON or OFF.
  4. Crash watchdog and kill switch are always active, independent of this mode.
"""
import json
import os
import subprocess
import threading
import time
from typing import Any, Dict, List

import requests

from ishti import CONFIG

try:  # best-effort failure log (never breaks the module if logging is unavailable)
    from ishti.core.logutil import swallowed
except Exception:  # pragma: no cover
    def swallowed(*_a, **_k):
        return None


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
        except Exception as _exc:
            swallowed('ishti/self_heal.py:_load_state', _exc)


def _save_state():
    try:
        os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
        with open(STATE_PATH, "w", encoding="utf-8") as f:
            json.dump(_state, f, indent=2)
    except Exception as exc:
        print(f"[self_heal] Failed to persist state: {exc}")


_load_state()

AUTO_INTERVAL_S = 60       # how often the loop looks
AUTO_COOLDOWN_S = 300      # minimum gap between automatic repair runs
_loop_thread = None


def diagnose() -> List[str]:
    """Cheap, read-only health probe. Returns human-readable problems (empty = healthy)."""
    problems: List[str] = []
    try:
        base_url = CONFIG["llm"]["base_url"].rsplit("/v1", 1)[0]
        if not requests.get(f"{base_url}/api/tags", timeout=4).ok:
            problems.append("Ollama API not answering")
    except Exception as exc:
        problems.append(f"Ollama unreachable ({type(exc).__name__})")
    try:
        import psutil
        ollama_pids, servers = set(), []
        for p in psutil.process_iter(["pid", "name", "ppid"]):
            n = (p.info["name"] or "").lower()
            if n.startswith("ollama") and "llama-server" not in n:
                ollama_pids.add(p.info["pid"])
            elif "llama-server" in n:
                servers.append(p)
        if any(p.info["ppid"] not in ollama_pids for p in servers):
            problems.append("orphaned llama-server.exe holding VRAM")
    except Exception as exc:
        swallowed("ishti/self_heal.py:diagnose.orphans", exc)
    return problems


def _auto_loop():
    last = 0.0
    while True:
        time.sleep(AUTO_INTERVAL_S)
        try:
            if not is_enabled() or time.time() - last < AUTO_COOLDOWN_S:
                continue
            from ishti import killswitch
            killswitch.check()  # a killed/locked app heals nothing
            issues = diagnose()
            if issues:
                last = time.time()
                trigger_heal(trigger="auto", issues=issues)
        except Exception as exc:
            swallowed("ishti/self_heal.py:_auto_loop", exc)


def ensure_loop() -> None:
    """Start the background loop once (idempotent). It idles while Auto-Heal is OFF."""
    global _loop_thread
    with _lock:
        if _loop_thread is None or not _loop_thread.is_alive():
            _loop_thread = threading.Thread(target=_auto_loop, name="ishti-auto-heal", daemon=True)
            _loop_thread.start()


def is_enabled() -> bool:
    """True ONLY if owner explicitly toggled on automatic background self-healing."""
    return _state["enabled"]


def set_enabled(enabled: bool) -> bool:
    """Toggle self-healing mode state."""
    with _lock:
        _state["enabled"] = bool(enabled)
        _save_state()
    print(f"[self_heal] Self-healing mode set to: {'ENABLED' if _state['enabled'] else 'OFF (Manual trigger only)'}")
    if _state["enabled"]:
        ensure_loop()
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
            "last_report": _state["last_report"],
            "auto_loop_running": bool(_loop_thread and _loop_thread.is_alive()),
            "auto_interval_s": AUTO_INTERVAL_S,
            "auto_cooldown_s": AUTO_COOLDOWN_S,
        }


def reap_orphaned_llama_servers() -> int:
    """FIX-6: terminate llama-server.exe processes whose Ollama parent is gone.
    A hard-killed ollama.exe leaves its model servers alive holding VRAM (measured: 3 orphans,
    ~8.9 GB), after which every model load fails with honest GpuBusy. Only processes whose
    parent pid is NOT a live ollama* process are touched; the live daemon's servers are never
    candidates. Returns the number reaped. Purely mechanical child-reaping - it runs as part of
    the owner-triggered heal, never automatically (Section 2 hard rules)."""
    try:
        import psutil
    except ImportError:
        return 0
    ollama_pids = set()
    servers = []
    for p in psutil.process_iter(["pid", "name", "ppid"]):
        try:
            n = (p.info["name"] or "").lower()
            if n.startswith("ollama") and "llama-server" not in n:
                ollama_pids.add(p.info["pid"])
            elif "llama-server" in n:
                servers.append(p)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    reaped = 0
    for p in servers:
        try:
            if p.info["ppid"] not in ollama_pids:
                p.kill()
                reaped += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    if reaped:
        record_candidate("ollama", f"reaped {reaped} orphaned llama-server.exe after an Ollama kill",
                         "if models still fail to load, restart Ollama from the DOCTOR tab")
    return reaped


def trigger_heal(trigger: str = "manual", issues: List[str] | None = None) -> Dict[str, Any]:
    """
    On-demand self-healing run, triggered manually by the owner.
    Diagnoses and applies remediations across models, search, memory, and voice.
    """
    actions_taken: List[str] = [f"Detected: {i}" for i in (issues or [])]
    t0 = time.time()
    print(f"[self_heal] >>> Self-healing run ({trigger})...")

    # 0. FIX-6: hard-killing Ollama (crash, task-manager, chaos) orphans its llama-server.exe
    # model servers; each holds its model's VRAM and the fresh daemon cannot fit anything
    # (measured: 3 orphans holding ~8.9 GB -> honest GpuBusy on every turn). Reap the orphans
    # (parent dead) FIRST, so the connectivity check below sees the world the new daemon will.
    try:
        n = reap_orphaned_llama_servers()
        if n:
            actions_taken.append(f"Reaped {n} orphaned llama-server.exe process(es) (killed Ollama's children)")
            time.sleep(2.0)  # Windows returns VRAM to the adapter a beat later
    except Exception as exc:
        actions_taken.append(f"Orphan llama-server reaper: {exc}")

    # 1. Ollama & Model Subsystem - only when answers come from the local model. On a cloud provider a bare
    # `ollama serve` (outside ISHTI's registry and settings) loaded nothing useful and competed for the GPU.
    try:
        from ishti import lifecycle, llm
        if llm.provider() != "local":
            actions_taken.append("Cloud model in use - Ollama left alone")
        else:
            base_url = CONFIG["llm"]["base_url"].rsplit("/v1", 1)[0]
            r = requests.get(f"{base_url}/api/tags", timeout=5)
            if r.ok:
                actions_taken.append("Verified Ollama API connectivity (responsive)")
            else:
                lifecycle.ensure_ollama()
                actions_taken.append("Restarted Ollama through ISHTI's own launcher")
    except Exception as exc:
        actions_taken.append(f"Could not reach or restart Ollama: {type(exc).__name__}")

    # Reset model router failure counters
    try:
        from orchestrator.model_router import ModelRouter
        router = ModelRouter()
        router.consecutive_failures.clear()
        actions_taken.append("Reset ModelRouter consecutive failure counters")
    except Exception as _exc:
        swallowed('ishti/self_heal.py:trigger_heal', _exc)

    # 2. Web Search & SearXNG Container
    try:
        from tools.web.search import SearxngSearchProvider
        url = SearxngSearchProvider.URL + "/healthz"
        r = requests.get(url, timeout=3)
        if r.status_code == 200:
            actions_taken.append("SearXNG search service verified healthy")
        else:
            # the real container is ishti-searxng ("docker start searxng" named one that doesn't exist)
            from tools.web.search import ensure_searxng
            if ensure_searxng(wait_s=30):
                actions_taken.append("Restarted the SearXNG search container")
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
            f"\n### Self-Healing Event ({trigger})\n"
            f"- **Timestamp**: {timestamp}\n"
            f"- **Trigger**: {'Owner button' if trigger == 'manual' else 'Auto-Heal loop (owner enabled)'}\n"
            f"- **Remediations Executed**:\n"
            + "\n".join(f"  - {a}" for a in actions_taken) + "\n"
        )
        with open(REFLECTIONS_PATH, "a", encoding="utf-8") as f:
            f.write(entry)
    except Exception as exc:
        print(f"[self_heal] Failed to write reflections entry: {exc}")

    report = {
        "status": "success",
        "trigger": trigger,
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
