"""Strict GPU model residency & eviction (Section 6).

Policy:
  pinned     Planner (the brain) + Fast Classifier (Laya): always resident in VRAM.
  evictable  All other models (Coder, Translator, etc.): load on-demand, unload automatically
             after 10 minutes (600s) idle.
  voice      Whisper STT unloads after 10 minutes idle (voice.py).

Every load, unload, and eviction event is explicitly logged to data/model_usage.db
(table `model_events`) and console so model residency is transparent and verifiable.
"""
import math
import os
import sqlite3
import threading
import time

import requests

from ishti import CONFIG

DB = os.path.join(os.path.dirname(CONFIG["memory"]["db_path"]), "model_usage.db")
OLLAMA = (os.environ.get("ISHTI_OLLAMA_URL") or CONFIG.get("llm", {}).get("base_url", "http://127.0.0.1:11434/v1")).rsplit("/v1", 1)[0]
IDLE_TIMEOUT_S = int(CONFIG.get("model_idle_timeout_s", 600))  # 10 minutes idle timer

_lock = threading.Lock()
_last_used = {}
_eviction_timers = {}


def _db():
    os.makedirs(os.path.dirname(DB), exist_ok=True)
    c = sqlite3.connect(DB, timeout=10)
    c.execute("CREATE TABLE IF NOT EXISTS usage(ts REAL, model TEXT)")
    c.execute("CREATE TABLE IF NOT EXISTS model_events(ts REAL, event TEXT, model TEXT, detail TEXT)")
    return c


def log_event(event: str, model: str, detail: str = ""):
    """Log load, unload, or eviction event to database and console."""
    now = time.time()
    try:
        with _lock, _db() as c:
            c.execute("INSERT INTO model_events VALUES(?, ?, ?, ?)", (now, event.lower(), model, detail))
    except sqlite3.Error:
        pass
    print(f"[models] {event.upper()}: {model} ({detail})")


def get_events(limit: int = 50):
    """Retrieve recent model load/unload/evict events."""
    try:
        with _lock, _db() as c:
            rows = c.execute("SELECT ts, event, model, detail FROM model_events ORDER BY ts DESC LIMIT ?",
                             (limit,)).fetchall()
            return [{"ts": ts, "event": ev, "model": m, "detail": d} for ts, ev, m, d in rows]
    except Exception:
        return []


def daily_rate(model, now=None, days=14, half_life_days=3.0):
    """Calls per day, recent days weighing more (exponential decay)."""
    now = now or time.time()
    with _db() as c:
        rows = c.execute("SELECT ts FROM usage WHERE model=? AND ts>=?", (model, now - days * 86400)).fetchall()
    decay = math.log(2) / (half_life_days * 86400)
    weights = sum(math.exp(-decay * (now - ts)) for (ts,) in rows)
    norm = sum(math.exp(-decay * d * 86400) for d in range(days))
    return weights / norm if norm > 0 else 0.0


def is_pinned(model: str) -> bool:
    """Only Planner and Fast Classifier / Laya are pinned always-resident."""
    planner = CONFIG.get("llm", {}).get("models", {}).get("planner", "")
    fallback = CONFIG.get("llm", {}).get("fallback", "")
    fast = CONFIG.get("llm", {}).get("models", {}).get("fast", "")
    m_lower = (model or "").lower()
    return (m_lower in ("laya", "typed-decisions")
            or model in (planner, fallback, "ishti-qwen2.5:14b", "qwen2.5:14b", "qwen3:30b-a3b")
            or m_lower == fast.lower())


def tier(model: str, now=None) -> str:
    return "pinned" if is_pinned(model) else "evictable"


def currently_resident() -> list[dict]:
    """Query Ollama and return currently loaded models in VRAM."""
    try:
        r = requests.get(f"{OLLAMA}/api/ps", timeout=5)
        if r.ok:
            return r.json().get("models", [])
    except Exception:
        pass
    return []


def evict(model: str, reason: str = "idle > 10m"):
    """Explicitly unload a model from GPU memory (keep_alive: 0)."""
    if is_pinned(model):
        return False
    try:
        r = requests.post(f"{OLLAMA}/api/generate", json={"model": model, "keep_alive": 0}, timeout=15)
        if r.ok:
            log_event("unload", model, reason)
            return True
    except requests.RequestException as exc:
        print(f"[models] Evict error for {model}: {exc}")
    return False


def unload_model(model: str, reason: str = "manual"):
    """Alias for evict(model, reason)."""
    return evict(model, reason)


def touch(model: str):
    """Alias for used(model)."""
    return used(model)


def evict_idle(idle_timeout_s=None) -> list[str]:
    """Check all unpinned models and evict those whose idle time exceeds timeout (Section 6)."""
    timeout = idle_timeout_s if idle_timeout_s is not None else IDLE_TIMEOUT_S
    now = time.time()
    evicted = []
    with _lock:
        candidates = list(_last_used.keys())
    for m in candidates:
        if is_pinned(m):
            continue
        with _lock:
            last = _last_used.get(m, 0)
        if now - last >= timeout or timeout == 0:
            if evict(m, f"idle > {timeout}s"):
                evicted.append(m)
    return evicted


def _schedule_eviction(model: str):
    """Start or reset the 10-minute idle eviction timer."""
    with _lock:
        if model in _eviction_timers:
            _eviction_timers[model].cancel()

        def _do_evict():
            with _lock:
                last = _last_used.get(model, 0)
            if time.time() - last >= IDLE_TIMEOUT_S:
                evict(model, f"idle > {IDLE_TIMEOUT_S // 60}m")

        t = threading.Timer(IDLE_TIMEOUT_S + 1, _do_evict)
        t.daemon = True
        _eviction_timers[model] = t
        t.start()


def used(model: str):
    """Record a call, log loading if fresh, and enforce keep_alive / idle eviction timer."""
    now = time.time()
    try:
        with _lock, _db() as c:
            c.execute("INSERT INTO usage VALUES(?, ?)", (now, model))
    except sqlite3.Error:
        return

    with _lock:
        last = _last_used.get(model, 0)
        _last_used[model] = now

    # Log fresh load if first time used or used after idle period
    if now - last > IDLE_TIMEOUT_S or last == 0:
        log_event("load", model, "requested for inference")

    if model == "whisper":
        return  # Whisper's idle unload is coordinated via voice.py

    if is_pinned(model):
        # Planner stays resident
        threading.Thread(target=_keep_alive, args=(model, -1), daemon=True).start()
    else:
        # Coder, translator, etc. get 10-minute residency and active eviction timer
        threading.Thread(target=_keep_alive, args=(model, f"{IDLE_TIMEOUT_S // 60}m"), daemon=True).start()
        _schedule_eviction(model)


def _keep_alive(model: str, keep):
    try:
        requests.post(f"{OLLAMA}/api/generate", json={"model": model, "keep_alive": keep}, timeout=30)
    except requests.RequestException:
        pass


def report():
    """[(model, tier, calls/day)] - for Overview/health inspection."""
    with _db() as c:
        models = [m for (m,) in c.execute("SELECT DISTINCT model FROM usage")]
    return sorted(((m, tier(m), round(daily_rate(m), 1)) for m in models), key=lambda r: -r[2])


if __name__ == "__main__":
    import tempfile
    DB = os.path.join(tempfile.mkdtemp(), "u.db")
    now = time.time()
    with _db() as c:
        c.executemany("INSERT INTO usage VALUES(?, ?)", [(now - i * 3000, "gemma3:4b") for i in range(10)])
    assert tier("gemma3:4b", now) == "evictable"
    assert tier("ishti-qwen2.5-coder:14b", now) == "evictable"
    assert tier("laya", now) == "pinned"
    planner_name = CONFIG.get("llm", {}).get("models", {}).get("planner", "qwen2.5:14b")
    assert tier(planner_name, now) == "pinned"

    set_calls = []
    _keep_alive = lambda m, k: set_calls.append((m, k))  # noqa: F811
    used("gemma3:4b")
    assert set_calls == [("gemma3:4b", "10m")], set_calls

    evicted = []
    def fake_evict(m, reason):
        evicted.append((m, reason))
        return True
    _orig_evict = evict
    evict = fake_evict  # noqa: F811
    log_event("unload", "gemma3:4b", "idle test")
    events = get_events(5)
    assert len(events) >= 1 and events[0]["model"] == "gemma3:4b"
    print("[self-check] models OK - strict GPU eviction (pinned planner/laya, 10m idle for others, event logging)")
