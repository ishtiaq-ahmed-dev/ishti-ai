"""Usage-based model residency (D3): stop loading and unloading the same models all day.

Every model call is logged (data/model_usage.db). How long a model stays in VRAM after a call follows how much the
owner actually uses it - counted over the last 14 days with recent days weighing more:
  pinned    the planner (the brain): always resident
  frequent  used ~20+ times a day      -> stays 60 min after its last call
  normal    used a few times a day     -> 15 min
  rare      less                       -> 2 min, freeing VRAM quickly
Ollama's OpenAI-compatible endpoint ignores keep_alive (checked: it reset to the 5-minute default), so after each
call a tiny native /api/generate request (no prompt, generates nothing) sets that model's residency."""
import math
import os
import sqlite3
import threading
import time

import requests

from ishti import CONFIG

DB = os.path.join(os.path.dirname(CONFIG["memory"]["db_path"]), "model_usage.db")
OLLAMA = CONFIG["llm"]["base_url"].rsplit("/v1", 1)[0]
POLICY = {"pinned": -1, "frequent": "60m", "normal": "15m", "rare": "2m", **CONFIG.get("model_residency", {})}
_lock = threading.Lock()
_last_set = {}


def _db():
    os.makedirs(os.path.dirname(DB), exist_ok=True)
    c = sqlite3.connect(DB, timeout=10)
    c.execute("CREATE TABLE IF NOT EXISTS usage(ts REAL, model TEXT)")
    return c


def daily_rate(model, now=None, days=14, half_life_days=3.0):
    """Calls per day, recent days weighing more (exponential decay)."""
    now = now or time.time()
    with _db() as c:
        rows = c.execute("SELECT ts FROM usage WHERE model=? AND ts>=?", (model, now - days * 86400)).fetchall()
    decay = math.log(2) / (half_life_days * 86400)
    weights = sum(math.exp(-decay * (now - ts)) for (ts,) in rows)
    norm = sum(math.exp(-decay * d * 86400) for d in range(days))  # what 1 call/day for `days` days would weigh
    return weights / norm


def tier(model, now=None):
    if model == CONFIG["llm"]["models"]["planner"]:
        return "pinned"
    rate = daily_rate(model, now)
    return "frequent" if rate >= 20 else "normal" if rate >= 3 else "rare"


def used(model):
    """Record a call and (at most once a minute per model) set how long the model stays loaded."""
    now = time.time()
    try:
        with _lock, _db() as c:
            c.execute("INSERT INTO usage VALUES(?, ?)", (now, model))
    except sqlite3.Error:
        return
    if model == "whisper" or now - _last_set.get(model, 0) < 60:  # whisper is logged; its unload lives in voice.py
        return
    _last_set[model] = now
    keep = POLICY[tier(model, now)]
    threading.Thread(target=_keep_alive, args=(model, keep), daemon=True).start()


def _keep_alive(model, keep):
    try:
        requests.post(f"{OLLAMA}/api/generate", json={"model": model, "keep_alive": keep}, timeout=30)
    except requests.RequestException:
        pass


def report():
    """[(model, tier, calls/day)] - the Overview/health can show it."""
    with _db() as c:
        models = [m for (m,) in c.execute("SELECT DISTINCT model FROM usage")]
    return sorted(((m, tier(m), round(daily_rate(m), 1)) for m in models), key=lambda r: -r[2])


if __name__ == "__main__":
    import tempfile
    DB = os.path.join(tempfile.mkdtemp(), "u.db")
    now = time.time()
    with _db() as c:
        c.executemany("INSERT INTO usage VALUES(?, ?)", [(now - i * 3000, "gemma3:4b") for i in range(400)]  # ~29/day
                      + [(now - i * 40000, "nomic-embed-text") for i in range(30)]  # ~2/day
                      + [(now - 12 * 86400, "coder") for _ in range(50)])  # busy 12 days ago only
    assert tier("gemma3:4b", now) == "frequent", daily_rate("gemma3:4b", now)
    assert tier("nomic-embed-text", now) == "rare", daily_rate("nomic-embed-text", now)
    assert tier("coder", now) == "rare"  # old use fades: recent days weigh more
    assert tier(CONFIG["llm"]["models"]["planner"], now) == "pinned"
    set_calls = []
    _keep_alive = lambda m, k: set_calls.append((m, k))  # noqa: F811 - no real Ollama call in the test
    used("gemma3:4b")
    time.sleep(0.2)
    used("gemma3:4b")  # within a minute: not set again
    time.sleep(0.2)
    assert set_calls == [("gemma3:4b", "60m")], set_calls
    print("[self-check] models OK - usage tiers (frequent/rare/pinned, recency-weighted), residency set once a minute")
