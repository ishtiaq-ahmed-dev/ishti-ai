"""Resilience core (Part G): failure classification, retry with backoff+jitter, circuit
breakers, fallbacks, checkpoints, safe mode. The dispatcher/skills stay simple; they wrap
the risky calls in these helpers.

G4's process watchdog already exists (ishti/watchdog.py — hung workers, restarts); this
module adds the per-call machinery around skills/agents/LLM calls. Everything degrades
soft: a broken resilience helper must never itself break a reply.
"""
from __future__ import annotations

import json
import os
import random
import threading
import time

from ishti import ROOT

try:  # best-effort failure log (never breaks the module if logging is unavailable)
    from ishti.core.logutil import swallowed
except Exception:  # pragma: no cover
    def swallowed(*_a, **_k):
        return None


CHECKPOINT_DIR = os.path.join(ROOT, "data", "checkpoints")

TRANSIENT = ("timeout", "timed out", "connection reset", "connection refused", "temporarily",
             "rate limit", "429", "503", "502", "504", "eof", "broken pipe", "read timed out")
CRITICAL = ("killswitch", "cancelled", "shutdown", "out of memory", "cuda error", "disk full",
            "access is denied", "permission denied")


def classify(error) -> str:
    """'transient' (retry helps) | 'permanent' (retry never helps) | 'critical' (escalate now)."""
    text = str(getattr(error, "args", [""])[0] if not isinstance(error, str) else error).lower()
    text += " " + type(error).__name__.lower()
    if any(k in text for k in CRITICAL):
        return "critical"
    if any(k in text for k in TRANSIENT):
        return "transient"
    if isinstance(error, (KeyboardInterrupt, SystemExit)):
        return "critical"
    return "permanent"


def retry(fn, *, attempts: int = 3, base_delay: float = 0.4, max_delay: float = 8.0,
          on_give_up=None, label: str = ""):
    """G1: run fn() with exponential backoff + jitter. Transient failures retry, permanent
    ones fail fast, critical ones re-raise immediately (the kill switch must never be swallowed).
    on_give_up(last_error) -> replacement result when all attempts fail."""
    last = None
    for attempt in range(max(1, attempts)):
        try:
            return fn()
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception as exc:
            last = exc
            kind = classify(exc)
            if kind == "critical":
                raise
            if kind == "permanent" or attempt == attempts - 1:
                break
            delay = min(max_delay, base_delay * (2 ** attempt)) * (0.5 + random.random() / 2)
            time.sleep(delay)
    if on_give_up is not None:
        try:
            return on_give_up(last)
        except Exception as _exc:
            swallowed('ishti/resilience.py:retry', _exc)
    raise last


class CircuitBreaker:
    """G1: after `fail_threshold` consecutive failures the circuit opens for `reset_s`;
    calls during that window fail fast (a fallback answers instead of every caller waiting
    for the same timeout). Half-open lets one probe through to test recovery."""

    def __init__(self, name: str, fail_threshold: int = 3, reset_s: float = 60.0):
        self.name = name
        self.fail_threshold = fail_threshold
        self.reset_s = reset_s
        self._fails = 0
        self._opened_at = 0.0
        self._lock = threading.Lock()

    @property
    def state(self) -> str:
        with self._lock:
            if self._opened_at:
                return "half-open" if time.time() - self._opened_at >= self.reset_s else "open"
            return "closed"

    def allow(self) -> bool:
        if self.state == "open":
            return False
        return True

    def record(self, ok: bool):
        with self._lock:
            if ok:
                self._fails, self._opened_at = 0, 0.0
            else:
                self._fails += 1
                if self._fails >= self.fail_threshold:
                    self._opened_at = time.time()

    def run(self, fn, fallback=None):
        """Call fn() through the breaker; when open (or still failing), fallback() answers."""
        if not self.allow():
            if fallback is not None:
                return fallback(RuntimeError(f"{self.name} circuit open"))
            raise RuntimeError(f"{self.name} circuit open")
        try:
            out = fn()
            self.record(True)
            return out
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception as exc:
            self.record(False)
            if fallback is not None:
                return fallback(exc)
            raise


BREAKERS: dict[str, CircuitBreaker] = {}
_BREAKER_LOCK = threading.Lock()


def breaker(name: str, fail_threshold: int = 3, reset_s: float = 60.0) -> CircuitBreaker:
    """The named shared breaker (one per external dependency: ollama, searxng, flowise...)."""
    with _BREAKER_LOCK:
        if name not in BREAKERS:
            BREAKERS[name] = CircuitBreaker(name, fail_threshold, reset_s)
        return BREAKERS[name]


def run_with_fallback(steps: list, label: str = ""):
    """G1 fallback chain: [(fn, name), ...] — first success wins; a fallback that also fails
    is just the next candidate. Returns (result, used_name) or raises the last error."""
    last = None
    for fn, name in steps:
        try:
            return fn(), name
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception as exc:
            last = exc
    raise last


# ------------------------------------------------------------------ checkpoints (G2)
def checkpoint(name: str, state: dict) -> str:
    """State snapshot before a risky/long operation (resume after a crash, D3).
    One JSON file per task name; returns the path."""
    os.makedirs(CHECKPOINT_DIR, exist_ok=True)
    path = os.path.join(CHECKPOINT_DIR, f"{name}.json")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"saved_at": time.time(), "state": state}, f, ensure_ascii=False, default=str)
    os.replace(tmp, path)
    return path


def restore(name: str) -> dict | None:
    try:
        with open(os.path.join(CHECKPOINT_DIR, f"{name}.json"), encoding="utf-8") as f:
            return json.load(f).get("state")
    except (OSError, ValueError):
        return None


def drop_checkpoint(name: str):
    try:
        os.remove(os.path.join(CHECKPOINT_DIR, f"{name}.json"))
    except OSError:
        pass


# ------------------------------------------------------------------ safe mode (G7)
def core_health() -> dict:
    """The minimum for full functionality: an LLM backend and the skill set."""
    out = {}
    try:
        import urllib.request
        with urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=3) as r:
            out["llm"] = bool(json.load(r).get("models"))
    except Exception as exc:
        out["llm"] = False
        out["llm_detail"] = f"{type(exc).__name__}"
    try:
        from ishti.skills import load_skills
        skills = load_skills()
        broken = [n for n, s in skills.items() if s.status == "broken"]
        out["skills"] = len(skills) > 10
        out["skills_broken"] = broken[:8]
    except Exception as exc:
        out["skills"] = False
        out["skills_detail"] = type(exc).__name__
    return out


def safe_mode_reason() -> str:
    """'' = full functionality; otherwise the plain-language reason (G7)."""
    h = core_health()
    if not h.get("llm"):
        return ("running in safe mode: the local model backend isn't answering "
                + f"({h.get('llm_detail', 'unreachable')}) - reflex answers and skill lookup still work.")
    if not h.get("skills"):
        return "running in safe mode: the skill set failed to load - only built-in commands answer."
    return ""


if __name__ == "__main__":
    import tempfile
    # classification
    assert classify(TimeoutError("read timed out")) == "transient"
    assert classify(ValueError("bad json shape")) == "permanent"
    assert classify(RuntimeError("CUDA error: out of memory")) == "critical"
    # retry: transient succeeds on attempt 3, permanent fails fast, critical re-raises
    calls = {"n": 0}
    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise TimeoutError("timed out")
        return "ok"
    assert retry(flaky, base_delay=0.01) == "ok" and calls["n"] == 3
    calls["n"] = 0
    def perm():
        calls["n"] += 1
        raise ValueError("nope")
    try:
        retry(perm, attempts=3, base_delay=0.01)
        raise SystemExit("permanent error retried!")
    except ValueError:
        assert calls["n"] == 1, calls["n"]  # fail fast
    def crit():
        raise RuntimeError("CUDA error")
    try:
        retry(crit, attempts=3, base_delay=0.01)
        raise SystemExit("critical swallowed!")
    except RuntimeError as _exc:
        swallowed('ishti/resilience.py:<module>', _exc)
    # give-up fallback
    assert retry(lambda: 1 / 0, attempts=1, on_give_up=lambda e: "fallback") == "fallback"
    # circuit breaker: opens after 3 fails, fallback answers while open, closes after reset
    br = CircuitBreaker("test", fail_threshold=3, reset_s=0.2)
    boom = lambda: (_ for _ in ()).throw(TimeoutError("t"))
    for _ in range(3):
        assert br.run(boom, fallback=lambda e: "fb") == "fb"
    assert br.state == "open" and br.run(lambda: "real", fallback=lambda e: "fb") == "fb"
    time.sleep(0.25)
    assert br.state == "half-open"
    assert br.run(lambda: "recovered") == "recovered" and br.state == "closed"
    # shared breakers are singletons
    assert breaker("ollama") is breaker("ollama")
    # fallback chain
    out, who = run_with_fallback([(lambda: 1 / 0, "a"), (lambda: "b works", "b")])
    assert out == "b works" and who == "b"
    # checkpoints round-trip
    tmp = tempfile.mkdtemp()
    CHECKPOINT_DIR = tmp
    p = checkpoint("t1", {"step": 3, "topic": "sony xm5"})
    assert restore("t1")["step"] == 3
    drop_checkpoint("t1")
    assert restore("t1") is None
    # safe-mode probe answers (ollama may or may not be up; it must not raise)
    r = safe_mode_reason()
    assert isinstance(r, str)
    print(f"[self-check] ishti.resilience OK — classify/retry/breakers/fallback/checkpoints/safe-mode{' (llm down)' if r else ''}")
