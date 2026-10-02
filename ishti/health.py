"""Health loop (A1): every few minutes, cheap checks of what ISHTI depends on, so a degrading component is flagged
before it fails outright. A check that turns bad is announced once and logged to NEEDS.md; it's announced again
only after it recovered and broke again. GET /api/health returns the latest snapshot (the watchdog uses it too)."""
import shutil
import subprocess
import threading
import time

import requests

from ishti import CONFIG, keepalive

EVERY_S = 300
_state = {"at": 0.0, "status": "starting", "checks": {}}


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
    r = keepalive.get("http://127.0.0.1:8890/search?q=ping&format=json", timeout=8)
    return r.ok, f"HTTP {r.status_code}"


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


def _browser():
    from ishti.mcp_client import load_tokens
    main = CONFIG.get("browser_profiles", {}).get("default", "main")
    key = CONFIG["mcp_servers"].get(main, {}).get("token_key")
    ok = not key or bool(load_tokens().get(key))
    return ok, "main profile connected" if ok else "main Chrome profile not connected (Settings -> Chrome profiles)"


def _errors():
    from ishti.audit import query
    fails = [r for r in query(time.time() - 3600) if r[3].endswith("failed") or r[3] == "request failed"]
    return len(fails) < 5, f"{len(fails)} failures in the last hour"


CHECKS = {"ollama": _ollama, "search": _searx, "gpu": _gpu, "disks": _disks, "skills": _skills, "browser": _browser,
          "errors": _errors}


def run_checks():
    checks = {}
    for name, fn in CHECKS.items():
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
    except Exception:
        pass
    prev = _state.get("checks", {})
    checks = run_checks()
    _state.update(at=time.time(), checks=checks, status="ok" if all(c["ok"] for c in checks.values()) else "degraded")
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
    import ishti.skills as sk
    logged = []
    sk.log_need = lambda *a: logged.append(a)  # the test's fake fault must not reach the real NEEDS.md
    heard = []
    real = dict(CHECKS)
    CHECKS["gpu"] = lambda: (False, "40 MB free of 16303")  # a degradation appears
    newly = tick(heard.append)
    assert "gpu" in newly and logged and heard and "gpu - 40 MB free" in heard[0], (newly, heard)
    assert tick(heard.append) == [] and len(heard) == 1  # still bad: not announced again
    CHECKS.update(real)
    checks = run_checks()
    print({n: c["ok"] for n, c in checks.items()})
    assert checks["ollama"]["ok"] and checks["disks"]["ok"], checks
    print("[self-check] health OK - all real checks run; a degradation is announced once and logged")
