"""Health loop (A1): every few minutes, cheap checks of what ISHTI depends on, so a degrading component is flagged
before it fails outright. A check that turns bad is announced once in the runtime needs log; it's announced again
only after it recovered and broke again - and that "already announced" state is kept ON DISK, because the live
snapshot is process memory and so the loop used to re-announce a component that was still down on every restart
(the owner's bell collected the same dead-search alert seven times). GET /api/health returns the latest snapshot
(the watchdog uses it too)."""
import json
import shutil
import subprocess
import threading
import time
from pathlib import Path

import requests

from ishti import CONFIG, keepalive
from ishti.test_mode import scratch_path

try:  # best-effort failure log (never breaks the module if logging is unavailable)
    from ishti.core.logutil import swallowed
except Exception:  # pragma: no cover
    def swallowed(*_a, **_k):
        return None


EVERY_S = 300
_state = {"at": 0.0, "status": "starting", "checks": {}}
CHECK_STATE = Path(scratch_path(str(Path(CONFIG["memory"]["db_path"]).parent / "health_checks.json")))


def _saved_checks() -> dict:
    """The checks from the loop's previous run, read from disk (see the module docstring)."""
    try:
        with open(CHECK_STATE, encoding="utf-8") as f:
            return json.load(f).get("checks") or {}
    except (OSError, ValueError):
        return {}


def _save_checks(checks: dict) -> None:
    try:
        CHECK_STATE.parent.mkdir(parents=True, exist_ok=True)
        with open(CHECK_STATE, "w", encoding="utf-8") as f:
            json.dump({"at": time.time(), "checks": checks}, f)
    except OSError as exc:
        swallowed("ishti/health.py:_save_checks", exc)


def _ollama():
    tags = keepalive.get("http://127.0.0.1:11434/api/tags", timeout=5).json()
    names = {m["name"] for m in tags.get("models", [])}
    want = CONFIG["llm"]["models"]["planner"]
    ok = any(n == want or n.startswith(want + ":") or n.split(":")[0] == want.split(":")[0] for n in names)
    if not ok:
        from ishti import llm
        fallback = llm.FALLBACK_MAP.get(want, CONFIG["llm"]["models"].get("fallback", ""))
        if fallback and any(n == fallback or n.startswith(fallback + ":") or n.split(":")[0] == fallback.split(":")[0] for n in names):
            return True, f"{len(names)} models (planner {want} using fallback {fallback})"
    return ok, f"{len(names)} models" + ("" if ok else f", planner {want} missing")


def _searx():
    """Local SearXNG (Docker container ishti-searxng). The failure detail ends up in the owner's bell, so it
    says what is down and what it costs - a raw "ConnectionError: HTTPConnectionPool(...) Max retries
    exceeded" told the owner nothing (2026-10-13). The container is started at server boot
    (interface/web_ui/server.py), so this normally only fires when the engine itself cannot run."""
    try:
        from tools.web.search import SearxngSearchProvider
        r = keepalive.get(SearxngSearchProvider.URL + "/healthz", timeout=3)
        return r.ok, f"HTTP {r.status_code}"
    except Exception as exc:
        return False, ("SearXNG (the local Docker search service on 127.0.0.1:8890) is not answering "
                       f"({type(exc).__name__}); web search falls back to the slower providers")


def _gpu():
    out = subprocess.run(["nvidia-smi", "--query-gpu=memory.used,memory.total", "--format=csv,noheader,nounits"],
                         capture_output=True, text=True, timeout=10, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    used, total = (int(x) for x in out.stdout.split(",")[:2])
    free = total - used
    return free > 700, f"{free} MB free of {total}"


def _disks():
    import psutil
    low = [f"{p.mountpoint} {shutil.disk_usage(p.mountpoint).free / 1e9:.0f} GB" for p in psutil.disk_partitions()
           if "fixed" in p.opts or p.fstype == "NTFS" if shutil.disk_usage(p.mountpoint).free < 5e9]
    return not low, "low: " + ", ".join(low) if low else "ok"


def _skills():
    from ishti.dispatcher import SKILLS
    broken = [n for n, s in SKILLS.items() if s.status == "broken"]
    return not broken, f"{len(SKILLS) - len(broken)}/{len(SKILLS)} loaded" + (f"; broken: {', '.join(broken)}" if broken else "")


def _errors():
    from ishti.audit import query
    fails = [r for r in query(time.time() - 3600) if r[3].endswith("failed") or r[3] == "request failed"]
    return len(fails) < 5, f"{len(fails)} failures in the last hour"


CHECKS = {"ollama": _ollama, "search": _searx, "gpu": _gpu, "disks": _disks, "skills": _skills,
          "errors": _errors}


def run_checks():
    checks = {}
    from ishti import llm
    for name, fn in CHECKS.items():
        if name in ("ollama", "gpu") and llm.provider() == "api":
            checks[name] = {"ok": True, "detail": "not required for the selected API provider"}
            continue
        try:
            ok, detail = fn()
        except Exception as exc:
            ok, detail = False, f"{type(exc).__name__}: {str(exc)[:100]}"
        checks[name] = {"ok": bool(ok), "detail": detail}
    return checks


def snapshot():
    return dict(_state)


def tick(announce=None):
    try:  # D4: keep the correction log exported for the periodic fine-tune
        from ishti import corrections
        corrections.export()
    except Exception as _exc:
        swallowed('ishti/health.py:tick', _exc)
    # memory first, disk second: right after a restart memory is empty and the disk copy is what keeps a
    # component that is STILL down from being announced again as if it had just broken
    prev = _state.get("checks") or _saved_checks()
    checks = run_checks()
    _state.update(at=time.time(), checks=checks, status="ok" if all(c["ok"] for c in checks.values()) else "degraded")
    _save_checks(checks)
    newly_bad = [n for n, c in checks.items() if not c["ok"] and prev.get(n, {"ok": True})["ok"]]
    if newly_bad:
        from ishti.skills import log_need
        for n in newly_bad:
            log_need("error", "health", n, checks[n]["detail"])
        if announce:
            announce("Health: " + "; ".join(f"{n} - {checks[n]['detail']}" for n in newly_bad))
    return newly_bad


def start(announce=None):
    def loop():
        time.sleep(20)  # let the server finish starting
        while True:
            try:
                tick(announce)
            except Exception as exc:
                print(f"[health] {exc}")
            time.sleep(EVERY_S)
    threading.Thread(target=loop, daemon=True, name="health").start()


if __name__ == "__main__":
    import tempfile

    import ishti.skills as sk
    globals()["CHECK_STATE"] = Path(tempfile.mkdtemp()) / "health_checks.json"  # never touch the real snapshot
    logged = []
    sk.log_need = lambda *a: logged.append(a)  # the test's fake fault must not reach the real needs log
    heard = []
    real = dict(CHECKS)
    CHECKS["gpu"] = lambda: (False, "40 MB free of 16303")  # a degradation appears
    newly = tick(heard.append)
    assert "gpu" in newly and logged and heard and "gpu - 40 MB free" in heard[0], (newly, heard)
    assert tick(heard.append) == [] and len(heard) == 1  # still bad: not announced again
    _state["checks"] = {}  # a restart: memory is empty, the disk copy has to hold the "already announced"
    assert tick(heard.append) == [] and len(heard) == 1, heard
    CHECKS.update(real)
    checks = run_checks()
    print({n: c["ok"] for n, c in checks.items()})
    assert checks["ollama"]["ok"] and checks["disks"]["ok"], checks
    print("[self-check] health OK - all real checks run; a degradation is announced once and logged")
