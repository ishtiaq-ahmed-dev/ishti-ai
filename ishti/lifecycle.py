"""Lifecycle: ONE state machine (starting -> ready -> shutting_down -> stopped), real boot stages, single instance,
a Windows Job Object so every child dies with the parent, and a graceful shutdown with progress events.
The contract is docs/API_CONTRACT.md; the HTTP/WS side is ishti/lifecycle_api.py.

  boot       stages run in a worker thread, each verified against reality (Ollama answers, memory opens, skills import,
             the planner is FULLY on the GPU and answered, the voice engine is loaded). A stage that fails or stalls turns
             into a plain-language `error` (with the log path); it is retried, so starting Ollama late heals the boot.
  Ollama     if it is not running we start it (hidden, in our Job Object) and it is "ours": we stop it at shutdown. An
             Ollama that was already running is left running (only our models are unloaded from it).
  shutdown   stop intake -> cancel/finish tasks (bounded) -> flush SQLite/Chroma -> stop voice+mic -> unload our models
             (+ stop our Ollama) -> close the MCP/browser servers we opened (never the owner's own Chrome) -> release the
             port -> exit; anything still alive after a bounded time is force-killed (whole tree)."""
import faulthandler
import glob
import io
import json
import logging
import logging.handlers
import os
import shutil
import sqlite3
import subprocess
import sys
import threading
import time

import psutil
import requests

from ishti import CONFIG, ROOT, events

LOCAL = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
HOME = os.path.join(LOCAL, "ISHTI")
LOG_DIR, RUN_DIR = os.path.join(HOME, "logs"), os.path.join(HOME, "run")
LOG_FILE = os.path.join(LOG_DIR, "ishti.log")
REGISTRY = os.path.join(RUN_DIR, "ishti.json")
DATA = os.path.dirname(CONFIG["memory"]["db_path"])
STOP_FLAG = os.path.join(DATA, "stop.flag")
OLLAMA = (os.environ.get("ISHTI_OLLAMA_URL") or CONFIG["llm"]["base_url"]).rsplit("/v1", 1)[0]
NOWIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)
SHUTDOWN_HARD_S = 40  # the steps can add up to ~30 s in the worst case (cancel 8 s, unload 10 s per model, browsers 5 s)
log = logging.getLogger("ishti.lifecycle")

STAGES = [("backend_up", "Starting ISHTI"), ("ollama_ready", "Waking up the AI engine"), ("memory_loaded", "Loading your memory"),
          ("skills_loaded", "Loading skills"), ("planner_warm", "Warming up the brain"), ("voice_ready", "Getting the voice ready"),
          ("ready", "Ready")]
FRIENDLY = {"ollama_ready": "The AI engine (Ollama) didn't start. Make sure Ollama is installed, then try again.",
            "memory_loaded": "Your memory couldn't be opened.",
            "skills_loaded": "The skills didn't finish loading.",
            "planner_warm": "The assistant's main model couldn't be loaded onto the graphics card.",
            "voice_ready": "The voice engine didn't start (ISHTI works without its voice)."}
TIMEOUTS = {"backend_up": 60, "ollama_ready": 60, "memory_loaded": 90, "skills_loaded": 180, "planner_warm": 300, "voice_ready": 90}

_lk = threading.RLock()
_st = {"state": "starting", "port": 8888, "t0": time.time(), "done": {}, "error": None, "error_stage": None, "detail": {}}
_ollama = {"proc": None, "ours": False}
_server = {"uvicorn": None}
_accepting = threading.Event()
_accepting.set()
_shutdown = {"started": False, "force_timer": None}
_inflight = {"n": 0}
_job = {"handle": None}
_mutex = {"h": None}


# ---------------------------------------------------------------- state
def state():
    return _st["state"]


def accepting():
    return _accepting.is_set()


def ready_snapshot():
    """GET /api/ready (docs/API_CONTRACT.md §2)."""
    with _lk:
        stages = [{"id": i, "label": lab, "done": bool(_st["done"].get(i))} for i, lab in STAGES]
        nxt = next((s["id"] for s in stages if not s["done"]), "ready")
        done_n = sum(s["done"] for s in stages)
        err = _st["error"]
        return {"ready": nxt == "ready" and err is None and _st["state"] in ("ready",), "stage": _st["error_stage"] or nxt, "stages": stages,
                "progress": round(100 * done_n / len(STAGES)), "error": err, "state": _st["state"], "log_path": LOG_FILE,
                "elapsed_s": round(time.time() - _st["t0"], 1), "warnings": list(_st.get("warnings", []))}


def _publish_stage(sid):
    snap = ready_snapshot()
    s = next(x for x in snap["stages"] if x["id"] == sid)
    events.publish("boot_stage", {"id": sid, "label": s["label"], "done": s["done"], "stage": snap["stage"], "progress": snap["progress"], "error": snap["error"]})


def _mark(sid, done=True):
    with _lk:
        _st["done"][sid] = done
        if done and _st["error_stage"] == sid:
            _st["error"], _st["error_stage"] = None, None
        if sid == "ready" and done:
            _st["state"] = "ready"
    _publish_stage(sid)


def _fail(sid, message):
    with _lk:
        _st["error"], _st["error_stage"] = message, sid
    log.error("boot stage %s failed: %s", sid, message)
    _publish_stage(sid)
    events.publish("error", {"code": f"boot_{sid}", "message": message, "stage": sid, "log_path": LOG_FILE})


# ---------------------------------------------------------------- logging, single instance, job object, registry
class _Stream(io.TextIOBase):
    """stdout/stderr -> the rotating log (and the console when there is one). pythonw has no stdout at all."""
    encoding = "utf-8"
    errors = "replace"

    def __init__(self, level, tee):
        self.level, self.tee, self.buf, self.lk = level, tee, "", threading.Lock()

    def write(self, s):
        if self.tee:
            try:
                self.tee.write(s)
            except Exception:
                pass
        with self.lk:  # several threads print at once: keep whole lines together
            self.buf += s
            *lines, self.buf = self.buf.split("\n")
        for line in lines:
            if line.strip():
                logging.getLogger("stdout" if self.level == logging.INFO else "stderr").log(self.level, line.rstrip())
        return len(s)

    def flush(self):
        if self.tee:
            try:
                self.tee.flush()
            except Exception:
                pass

    def isatty(self):
        return False


def setup_logging():
    """Rotating file in %LOCALAPPDATA%\\ISHTI\\logs (5 x 5 MB) for everything the process prints, plus a crash log
    (faulthandler) for native crashes; no console window is needed."""
    os.makedirs(LOG_DIR, exist_ok=True)
    root = logging.getLogger()
    if any(isinstance(h, logging.handlers.RotatingFileHandler) and h.baseFilename == LOG_FILE for h in root.handlers):
        return
    h = logging.handlers.RotatingFileHandler(LOG_FILE, maxBytes=5_000_000, backupCount=5, encoding="utf-8")
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root.addHandler(h)
    root.setLevel(logging.INFO)
    sys.stdout = _Stream(logging.INFO, sys.__stdout__ if sys.__stdout__ and sys.__stdout__.isatty() else None)
    sys.stderr = _Stream(logging.ERROR, sys.__stderr__ if sys.__stderr__ and sys.__stderr__.isatty() else None)
    try:
        faulthandler.enable(open(os.path.join(LOG_DIR, "crash.log"), "a", buffering=1))
    except Exception:
        pass


def single_instance(port):
    """True if this is the first ISHTI on `port`. Otherwise the first instance is told to show its window
    (POST /api/focus -> `show_window` event) and False is returned: no second backend is started."""
    try:
        import win32api
        import win32event
        import winerror
        _mutex["h"] = win32event.CreateMutex(None, False, f"Local\\ISHTI_Backend_{port}")
        taken = win32api.GetLastError() == winerror.ERROR_ALREADY_EXISTS
    except ImportError:  # not Windows: fall back to "is something answering on the port"
        taken = _health(port)
    if not taken:
        return True
    for _ in range(40):  # the first instance may still be starting: wait for it to listen
        try:
            requests.post(f"http://127.0.0.1:{port}/api/focus", timeout=2)
            break
        except requests.RequestException:
            time.sleep(0.25)
    return False


def _health(port):
    try:
        return requests.get(f"http://127.0.0.1:{port}/api/health", timeout=1.5).ok
    except requests.RequestException:
        return False


def adopt_job():
    """Put this process in a Job Object that kills every process in it when the last handle closes - i.e. when this
    process exits or crashes. Children (Ollama we start, MCP/Playwright servers, voice workers) inherit membership.
    -> True when active. (Nested jobs are fine on Windows 8+.)"""
    if _job["handle"] is not None:
        return True
    try:
        import win32api
        import win32job
        h = win32job.CreateJobObject(None, "")
        info = win32job.QueryInformationJobObject(h, win32job.JobObjectExtendedLimitInformation)
        # KILL_ON_JOB_CLOSE: everything ISHTI runs dies with it. BREAKAWAY_OK: a launch that asks (CREATE_BREAKAWAY_FROM_JOB) is NOT
        # in the job - the apps ISHTI opens for the owner (Chrome, Notepad...) must outlive it (see install_launch_patches)
        info["BasicLimitInformation"]["LimitFlags"] |= win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE | 0x0800
        win32job.SetInformationJobObject(h, win32job.JobObjectExtendedLimitInformation, info)
        win32job.AssignProcessToJobObject(h, win32api.GetCurrentProcess())
        _job["handle"] = h
        return True
    except Exception as exc:
        log.warning("Job Object unavailable (%s: %s): children can outlive a crash; shutdown still sweeps them", type(exc).__name__, exc)
        return False


DETACHED, NEW_GROUP, BREAKAWAY, NO_WINDOW = 0x00000008, 0x00000200, 0x01000000, 0x08000000
FREE_PIDS = set()  # processes ISHTI launched to outlive it (user-facing apps): never swept at exit


def install_launch_patches():
    """Apps ISHTI opens FOR the owner must not die when ISHTI stops or crashes. A process started from a Job Object member is a
    member too (verified: an os.startfile'd process was killed with its parent), so:
      - os.startfile (and therefore webbrowser.open) goes through `cmd /c start`, launched with CREATE_BREAKAWAY_FROM_JOB
      - any Popen with DETACHED_PROCESS (= "meant to outlive us", e.g. computer_use launching an app) also breaks away
      - any other Popen with no creationflags gets CREATE_NO_WINDOW: this process has no console, and a console child would
        otherwise flash a window
    If breakaway is refused (this process is in someone else's job) the launch is retried without it."""
    if getattr(subprocess.Popen, "_ishti_patched", False):
        return
    orig_init, orig_startfile = subprocess.Popen.__init__, getattr(os, "startfile", None)

    def init(self, *a, **kw):
        flags = kw.get("creationflags", 0)
        free = bool(flags & DETACHED)
        if free:
            kw["creationflags"] = flags | BREAKAWAY
        elif not flags:
            kw["creationflags"] = NO_WINDOW
        try:
            orig_init(self, *a, **kw)
        except OSError as exc:
            if not (free and getattr(exc, "winerror", None) == 5):
                raise
            kw["creationflags"] = flags  # breakaway not permitted here: launch inside the job rather than not at all
            orig_init(self, *a, **kw)
            free = False
        if free:
            FREE_PIDS.add(self.pid)
    subprocess.Popen.__init__ = init
    subprocess.Popen._ishti_patched = True

    if orig_startfile:
        def startfile(path, operation="open", arguments="", cwd=None, show_cmd=1):
            if operation != "open":
                return orig_startfile(path, operation, arguments, cwd, show_cmd)
            cmd = f'cmd.exe /c start "" "{path}" {arguments}'.rstrip()
            subprocess.Popen(cmd, cwd=cwd, creationflags=BREAKAWAY | NO_WINDOW)  # `cmd` is outside the job, so what `start` opens is too
        os.startfile = startfile


def registry_path(port=None):
    """ishti.json for the default port (8888); ishti-<port>.json for any other, so a second instance (a test, another
    profile) never overwrites the real one's pids."""
    port = port or _st["port"]
    return REGISTRY if port == 8888 else REGISTRY.replace(".json", f"-{port}.json")


def registry(update=None, port=None):
    """%LOCALAPPDATA%\\ISHTI\\run\\ishti.json: the pids of what ISHTI started, so a stop never has to guess by name."""
    os.makedirs(RUN_DIR, exist_ok=True)
    path = registry_path(port)
    try:
        with open(path, encoding="utf-8") as f:
            reg = json.load(f)
    except (OSError, ValueError):
        reg = {}
    if update is not None:
        reg.update(update)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(reg, f)
        for attempt in range(10):  # Windows: an antivirus/indexer scanning the file makes the replace fail for a moment (sharing violation)
            try:
                os.replace(tmp, path)
                break
            except PermissionError:
                if attempt == 9:  # still locked: write in place instead (not atomic, but the file must not be left stale)
                    with open(path, "w", encoding="utf-8") as f:
                        json.dump(reg, f)
                    break
                time.sleep(0.05 * (attempt + 1))
    return reg


def created(pid):
    """The process' creation time (unique per process even when Windows reuses the pid), or None."""
    try:
        return round(psutil.Process(int(pid)).create_time(), 2)
    except (psutil.Error, TypeError, ValueError):
        return None


def _alive(pid, name=None, ct=None):
    """Is `pid` still THE process we started? ct = the creation time recorded with it: a reused pid has another one."""
    try:
        p = psutil.Process(int(pid))
        if ct is not None and abs(round(p.create_time(), 2) - ct) > 1.0:
            return False
        return p.is_running() and p.status() != psutil.STATUS_ZOMBIE and (not name or name in p.name().lower())
    except (psutil.Error, TypeError, ValueError):
        return False


def kill_tree(pid):
    """Kill a process and everything under it. -> number of processes killed."""
    try:
        root = psutil.Process(pid)
        procs = root.children(recursive=True) + [root]
    except psutil.Error:
        return 0
    for p in procs:
        try:
            p.kill()
        except psutil.Error:
            pass
    psutil.wait_procs(procs, timeout=8)
    return len(procs)


# ---------------------------------------------------------------- Ollama (ours or not)
DEFAULT_OLLAMA = {"num_parallel": 1, "max_loaded_models": 4, "keep_alive": "10m", "kv_cache_type": "f16", "flash_attention": None}
OLLAMA_SETTINGS = os.path.join(DATA, "ollama_settings.json")


def ollama_settings():
    s = dict(DEFAULT_OLLAMA)
    try:
        with open(OLLAMA_SETTINGS, encoding="utf-8") as f:
            s.update({k: v for k, v in json.load(f).items() if k in DEFAULT_OLLAMA})
    except (OSError, ValueError):
        pass
    return s


def ollama_env(s=None):
    """The OLLAMA_* variables for an Ollama that ISHTI starts. num_gpu is never set (partial offload is a hard failure)."""
    s = s or ollama_settings()
    env = {"OLLAMA_NUM_PARALLEL": str(s["num_parallel"]), "OLLAMA_MAX_LOADED_MODELS": str(s["max_loaded_models"]), "OLLAMA_KEEP_ALIVE": str(s["keep_alive"])}
    if s.get("kv_cache_type") and s["kv_cache_type"] != "f16":
        env["OLLAMA_KV_CACHE_TYPE"] = s["kv_cache_type"]
        env["OLLAMA_FLASH_ATTENTION"] = "1"  # a quantized KV cache needs flash attention
    elif s.get("flash_attention") is not None:
        env["OLLAMA_FLASH_ATTENTION"] = "1" if s["flash_attention"] else "0"
    return env


def ollama_up():
    try:
        return requests.get(OLLAMA + "/api/tags", timeout=3).ok
    except requests.RequestException:
        return False


def ollama_ours():
    reg = registry()
    return bool(reg.get("ollama_pid") and _alive(reg["ollama_pid"], "ollama", reg.get("ollama_ct")) and reg.get("ollama_url", OLLAMA) == OLLAMA)


def ollama_env_active():
    """The OLLAMA_* environment of the running Ollama (best effort: the one we started, else the `ollama serve` we can read)."""
    try:
        pid = registry().get("ollama_pid") if ollama_ours() else next(
            (p.pid for p in psutil.process_iter(["name", "cmdline"]) if (p.info["name"] or "").lower().startswith("ollama") and "serve" in " ".join(p.info["cmdline"] or [])), None)
        return {k: v for k, v in psutil.Process(pid).environ().items() if k.startswith("OLLAMA_")} if pid else {}
    except (psutil.Error, TypeError):
        return {}


_ollama_lock = threading.Lock()


def start_ollama(env_extra=None):
    """Start `ollama serve` hidden, inside our Job Object; remember it as ours. -> True when it answers. One at a time, and
    never a second copy: if it already answers by the time we hold the lock, that is the answer."""
    with _ollama_lock:
        if ollama_up():
            return True
        return _start_ollama(env_extra)


def _start_ollama(env_extra=None):
    exe = shutil.which("ollama")
    if not exe:
        raise FileNotFoundError("ollama is not installed (not on PATH)")
    os.makedirs(LOG_DIR, exist_ok=True)
    env = {**os.environ, **ollama_env(), **(env_extra or {})}
    if OLLAMA != "http://127.0.0.1:11434":  # a test/alternative port: OLLAMA_HOST tells the server where to listen
        env["OLLAMA_HOST"] = OLLAMA.split("//", 1)[1]
    out = open(os.path.join(LOG_DIR, "ollama.log"), "a", buffering=1)
    proc = subprocess.Popen([exe, "serve"], env=env, stdout=out, stderr=subprocess.STDOUT, creationflags=NOWIN)
    out.close()
    _ollama.update(proc=proc, ours=True)
    registry({"ollama_pid": proc.pid, "ollama_ct": created(proc.pid), "ollama_url": OLLAMA})
    for _ in range(60):
        if ollama_up():
            return True
        if proc.poll() is not None:
            raise RuntimeError(f"ollama exited with code {proc.returncode}")
        time.sleep(0.5)
    return False


def ensure_ollama():
    if ollama_up():
        return "running" if not ollama_ours() else "ours"
    if start_ollama():
        return "started"
    raise TimeoutError("Ollama did not answer within 30 s")


def save_ollama_settings(settings):
    """Validate and store new Ollama settings (no restart). -> the full settings dict."""
    s = ollama_settings()
    bad = [k for k in settings if k not in DEFAULT_OLLAMA]
    if bad:
        raise ValueError(f"unknown setting(s): {', '.join(bad)}")
    s.update(settings)
    if s["kv_cache_type"] not in ("f16", "q8_0", "q4_0"):
        raise ValueError("kv_cache_type must be f16, q8_0 or q4_0")
    if int(s["num_parallel"]) < 1 or int(s["max_loaded_models"]) < 1:
        raise ValueError("num_parallel and max_loaded_models must be at least 1")
    os.makedirs(DATA, exist_ok=True)
    with open(OLLAMA_SETTINGS, "w", encoding="utf-8") as f:
        json.dump(s, f, indent=2)
    return s


def apply_ollama_settings(settings=None, take_over=False):
    """Save new Ollama settings and, when the Ollama is ours, restart it with them (env vars only take effect at start).
    An Ollama we did not start is never restarted unless take_over=True (the person confirmed in the UI)."""
    s = save_ollama_settings(settings) if settings else ollama_settings()
    want = ollama_env(s)
    have = ollama_env_active()
    same = all(have.get(k) == v for k, v in want.items())
    if same:
        return {"applied": True, "restarted": False, "needs_restart": False, "settings": s, "env": want}
    if not ollama_ours() and not take_over:
        return {"applied": False, "restarted": False, "needs_restart": True, "settings": s, "env": want,
                "instructions": "Ollama was started outside ISHTI, so ISHTI will not restart it. Quit Ollama (tray icon -> Quit) and start "
                                "ISHTI again, or confirm in the UI to let ISHTI restart it. The settings are saved and apply when ISHTI starts Ollama."}
    from ishti import vram
    for m in vram.ps(True):
        vram.unload(m["name"])
    reg = registry()
    if reg.get("ollama_pid") and _alive(reg["ollama_pid"], "ollama"):
        kill_tree(reg["ollama_pid"])
    elif take_over:
        for p in psutil.process_iter(["name", "cmdline"]):
            if (p.info["name"] or "").lower().startswith("ollama") and "serve" in " ".join(p.info["cmdline"] or []):
                kill_tree(p.pid)
    time.sleep(1.0)
    start_ollama()
    return {"applied": True, "restarted": True, "needs_restart": False, "settings": s, "env": want}


# ---------------------------------------------------------------- boot
def _stage_backend():
    for _ in range(120):
        if _health(_st["port"]):
            return
        time.sleep(0.25)
    raise TimeoutError("the server never answered its own health check")


def _stage_ollama():
    how = ensure_ollama()
    from ishti import vram
    vram.start_sampler()
    _st["detail"]["ollama"] = how


def _stage_memory():
    from ishti.facts import get_facts
    from ishti.semantic import get_semantic
    get_facts().get("owner")
    get_semantic().col.count()


def _warm_thread():
    try:
        from ishti.dispatcher import warm_up
        return next((t for t in threading.enumerate() if getattr(t, "_target", None) is warm_up), None)
    except Exception:
        return None


def _stage_skills():
    from ishti.dispatcher import SKILLS, route
    t = _warm_thread()
    if t:
        t.join(170)  # the dispatcher's own warm-up (Laya router + voice) runs in this thread: never load it twice
    if not SKILLS:
        raise RuntimeError("no skills loaded")
    route("hello", SKILLS)
    broken = [n for n, s in SKILLS.items() if s.status == "broken"]
    _st["detail"]["skills"] = f"{len(SKILLS) - len(broken)}/{len(SKILLS)}" + (f" (broken: {', '.join(broken)})" if broken else "")


def _stage_planner():
    if not ollama_up():
        raise ConnectionError("Ollama is not answering")
    from ishti import llm, vram
    model = llm.model_for("planner")
    res = vram.admit(model, role="planner", job="start the assistant")
    if not res["ok"]:
        raise RuntimeError(res.get("queued") or "no room on the GPU")
    first, t0 = [], time.time()
    llm.chat([{"role": "user", "content": "Reply with the single word: ok"}], role="planner", system=None, temperature=0, max_tokens=3,
             on_token=lambda t: first or first.append(time.time() - t0))
    entry = next((m for m in vram.ps(True) if m["name"] == llm.model_for("planner")), None)
    if not entry or not entry["full"]:
        raise RuntimeError("the main model is not fully on the GPU")
    vram.record([entry])
    _st["detail"]["first_token_ms"] = round(1000 * (first[0] if first else time.time() - t0))
    _st["detail"]["planner"] = entry["name"]


def _stage_voice():
    from ishti import voice
    t = _warm_thread()
    if t:
        t.join(60)
    v = voice.current_voice()
    if v.get("engine") == "kokoro":
        voice.kokoro(v)
    else:
        voice.tts_voice(v)


STAGE_FNS = {"backend_up": _stage_backend, "ollama_ready": _stage_ollama, "memory_loaded": _stage_memory, "skills_loaded": _stage_skills,
             "planner_warm": _stage_planner, "voice_ready": _stage_voice}
NON_FATAL = {"voice_ready"}  # ISHTI works without its voice: report it, don't block "ready"


def _run_stage(sid):
    """Run one stage with its timeout. -> None on success, else a plain error message."""
    box = {}

    def work():
        try:
            STAGE_FNS[sid]()
        except Exception as exc:
            box["e"] = exc
            log.exception("boot stage %s", sid)
    t = threading.Thread(target=work, daemon=True, name=f"boot-{sid}")
    t.start()
    t.join(TIMEOUTS[sid])
    if t.is_alive():
        return f"{FRIENDLY.get(sid, sid)} It took longer than {TIMEOUTS[sid]} seconds. Details are in the log."
    if "e" in box:
        return f"{FRIENDLY.get(sid, sid)} Details are in the log."
    return None


def boot():
    """Run every stage in order; a failure is reported and retried (a late Ollama start, a freed GPU) so booting heals."""
    t_stage = time.time()
    timings = {}
    for sid, _label in STAGES[:-1]:
        attempts = 0
        while not _shutdown["started"]:
            err = _run_stage(sid)
            if err is None:
                break
            attempts += 1
            if sid in NON_FATAL:  # ISHTI works without it: "ready", with the problem listed in `warnings`
                _st.setdefault("warnings", []).append(err)
                events.publish("error", {"code": "voice_unavailable", "message": err, "stage": sid, "log_path": LOG_FILE})
                break
            if sid != "ollama_ready" and not ollama_up():  # the real cause: the AI engine went away - say that, and go back to it
                _mark("ollama_ready", False)
                _mark(sid, False)
                _fail("ollama_ready", "The AI engine (Ollama) stopped responding. Trying to start it again.")
                while not _shutdown["started"]:
                    if _run_stage("ollama_ready") is None:
                        break
                    time.sleep(5)
                _mark("ollama_ready")
                continue
            _fail(sid, err)
            time.sleep(min(5 * attempts, 15))
        if _shutdown["started"]:
            return
        _mark(sid)
        timings[sid] = round(time.time() - t_stage, 2)
        t_stage = time.time()
    _mark("ready")
    total = round(sum(timings.values()), 1)
    _st["detail"].update(timings=timings, total_s=total)
    _write_boot_metrics(timings, total)
    log.info("ready in %.1f s: %s", total, timings)


def _write_boot_metrics(timings, total):
    path = os.path.join(DATA, "boot_times.json")
    try:
        with open(path, encoding="utf-8") as f:
            hist = json.load(f)
    except (OSError, ValueError):
        hist = []
    hist.append({"at": time.time(), "total_s": total, "stages": timings, "ollama": _st["detail"].get("ollama"),
                 "first_token_ms": _st["detail"].get("first_token_ms"), "cold": _st["detail"].get("ollama") == "started"})
    with open(path, "w", encoding="utf-8") as f:
        json.dump(hist[-50:], f, indent=1)


def start(port):
    """Called once the server object exists: logging is on, the job is set, the registry says who we are; boot runs in a thread."""
    _st["port"] = port
    wd = int(os.environ.get("ISHTI_WATCHDOG_PID") or 0) or None
    registry({"server_pid": os.getpid(), "server_ct": created(os.getpid()), "port": port, "started": time.time(),
              "watchdog_pid": wd, "watchdog_ct": created(wd) if wd else None})
    threading.Thread(target=boot, daemon=True, name="boot").start()


# ---------------------------------------------------------------- shutdown
SHUTDOWN_STEPS = [("stop_intake", "Not accepting new requests"), ("cancel_tasks", "Finishing or cancelling running tasks"),
                  ("flush_data", "Saving your data"), ("stop_voice", "Stopping the voice and microphone"),
                  ("unload_models", "Releasing the graphics card"), ("close_browsers", "Closing ISHTI's browser tools"),
                  ("release_ports", "Closing the server"), ("exit", "Goodbye")]


def _progress(i, done=True, error=None):
    sid, label = SHUTDOWN_STEPS[i]
    events.publish("shutdown_progress", {"step": sid, "label": label, "index": i + 1, "total": len(SHUTDOWN_STEPS), "done": done, "error": error})


def inflight():
    return _inflight["n"]


def begin_shutdown(force=False):
    """POST /api/shutdown. Idempotent. -> the acknowledgement body."""
    with _lk:
        if _shutdown["started"]:
            return {"state": _st["state"], "message": "ISHTI is already shutting down."}
        _shutdown["started"] = True
        _st["state"] = "shutting_down"
    from ishti import vram
    vram.stop()  # no admit/switch/upshift from now on: a model loaded after the unload step would stay pinned in VRAM
    if os.environ.get("ISHTI_WATCHDOG"):  # under the watchdog: tell it this exit is wanted (it would otherwise restart us)
        try:
            with open(STOP_FLAG, "w", encoding="utf-8") as f:
                f.write("stop\n")
        except OSError:
            pass
    timer = threading.Timer(SHUTDOWN_HARD_S, _force_exit, args=("shutdown took longer than %d s" % SHUTDOWN_HARD_S,))
    timer.daemon = True
    timer.start()
    _shutdown["force_timer"] = timer
    threading.Thread(target=_shutdown_sequence, args=(force,), daemon=True, name="shutdown").start()
    return {"state": "shutting_down", "message": "ISHTI is shutting down."}


def _step(i, fn):
    try:
        fn()
        _progress(i)
    except Exception as exc:  # a failing step is reported and skipped: the rest must still happen
        log.exception("shutdown step %s", SHUTDOWN_STEPS[i][0])
        _progress(i, True, f"{type(exc).__name__}: {str(exc)[:120]}")


def _shutdown_sequence(force):
    _accepting.clear()
    _progress(0)
    def cancel():
        from ishti import killswitch
        killswitch.kill("shutdown")
        end = time.time() + (0 if force else 8)
        while inflight() > 0 and time.time() < end:
            time.sleep(0.2)
    _step(1, cancel)
    _step(2, flush_data)
    _step(3, stop_voice)
    _step(4, unload_models)
    _step(5, close_browsers)
    # The last two events are sent BEFORE the port is really released: closing the listener also closes every
    # WebSocket, so a client could never receive them afterwards.
    _progress(6)
    time.sleep(0.3)
    _progress(7)
    time.sleep(0.4)
    srv = _server["uvicorn"]
    if srv is not None:
        srv.should_exit = True
    end = time.time() + 6
    while time.time() < end and _port_open(_st["port"]):
        time.sleep(0.2)
    _force_exit("shutdown complete", clean=True)


def flush_data():
    """Fold every SQLite WAL back into its database (facts, audit, usage, Chroma, ...) and check it is consistent."""
    paths = set(glob.glob(os.path.join(DATA, "*.db")) + glob.glob(os.path.join(DATA, "chroma", "*.sqlite3")) + [CONFIG["memory"]["db_path"]])
    bad = []
    for p in paths:
        if not os.path.isfile(p):
            continue
        try:
            with sqlite3.connect(p, timeout=5) as c:
                busy = c.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()[0]
                if busy:
                    bad.append(f"{os.path.basename(p)} (busy: another writer holds it)")
                if c.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    bad.append(os.path.basename(p))
        except sqlite3.Error as exc:
            bad.append(f"{os.path.basename(p)} ({exc})")
    if bad:
        raise RuntimeError("could not verify: " + ", ".join(bad))


def stop_voice():
    from ishti import mic_control
    mic_control.mic_off("shutdown", by="shutdown")
    try:
        from ishti.talk import talker
        talker().stop()
    except Exception:
        pass
    try:
        from ishti import voice
        getattr(voice, "unload_whisper", lambda: None)()
    except Exception:
        pass


def unload_models():
    from ishti import vram
    for m in vram.ps(True):
        vram.unload(m["name"], wait_s=10)
    reg = registry()
    if ollama_ours() and reg.get("ollama_pid"):  # only an Ollama we started is stopped
        kill_tree(reg["ollama_pid"])
        registry({"ollama_pid": None, "ollama_ct": None})


def close_browsers():
    """Stop the MCP/Playwright servers ISHTI opened. The owner's own Chrome and its tabs are never touched
    (the extension relay just disconnects)."""
    mod = sys.modules.get("ishti.mcp_client")
    hub = getattr(mod, "_hub", None) if mod else None
    if hub is not None:
        for name in list(hub.live):
            hub.close(name)
        end = time.time() + 5
        while hub.live and time.time() < end:
            time.sleep(0.2)


def _port_open(port):
    try:
        return any(c.laddr.port == port and c.status == "LISTEN" for c in psutil.Process().net_connections(kind="inet"))
    except psutil.Error:
        return False


def _force_exit(why, clean=False):
    """Kill whatever is still under this process (whole tree), drop the registry entries, exit."""
    if not clean:
        log.warning("forcing exit: %s", why)
    _st["state"] = "stopped"
    me = psutil.Process()
    for c in me.children(recursive=True):  # (the Job Object also kills its members when we exit; this covers a job that could not be set up)
        if c.pid in FREE_PIDS:
            continue  # an app the owner asked ISHTI to open: it outlives us
        try:
            c.kill()
        except psutil.Error:
            pass
    try:
        reg = registry()
        if reg.get("server_pid") == os.getpid():
            gone = {"server_pid": None, "server_ct": None, "watchdog_pid": None, "watchdog_ct": None}
            if reg.get("ollama_pid") and (ollama_ours() or not _alive(reg["ollama_pid"], "ollama", reg.get("ollama_ct"))):
                gone.update(ollama_pid=None, ollama_ct=None)  # ours (the Job Object takes it with us) or already dead: no stale entry
            registry(gone)
    except OSError:
        pass
    logging.shutdown()
    os._exit(0)


def serve(app, port):
    """uvicorn with a handle we can stop gracefully (release_ports step) - replaces uvicorn.run()."""
    import uvicorn
    cfg = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", timeout_graceful_shutdown=3)
    srv = uvicorn.Server(cfg)
    _server["uvicorn"] = srv
    srv.run()
    # A graceful shutdown stops uvicorn as one of its steps and then finishes (registry, remaining children, os._exit) in its
    # own thread. If this main thread simply returned, the interpreter could end first and abandon that daemon thread mid-way
    # (stale registry entry, slower exit): wait for the shutdown sequence to finish the process itself.
    end = time.time() + SHUTDOWN_HARD_S + 5
    while _shutdown["started"] and time.time() < end:
        time.sleep(0.1)


if __name__ == "__main__":
    # state machine + snapshot shape + Ollama env mapping (no server, no GPU)
    snap = ready_snapshot()
    assert snap["ready"] is False and snap["stage"] == "backend_up" and snap["progress"] == 0 and snap["error"] is None
    for sid, _ in STAGES:
        _mark(sid)
    snap = ready_snapshot()
    assert snap["ready"] is True and snap["progress"] == 100 and snap["stage"] == "ready" and all(s["done"] for s in snap["stages"]), snap
    _fail("planner_warm", "boom")
    snap = ready_snapshot()
    assert snap["ready"] is False and snap["stage"] == "planner_warm" and snap["error"] == "boom"
    _mark("planner_warm")
    assert ready_snapshot()["error"] is None
    env = ollama_env({**DEFAULT_OLLAMA, "kv_cache_type": "q8_0"})
    assert env["OLLAMA_KV_CACHE_TYPE"] == "q8_0" and env["OLLAMA_FLASH_ATTENTION"] == "1" and env["OLLAMA_NUM_PARALLEL"] == "1"
    assert "OLLAMA_KV_CACHE_TYPE" not in ollama_env(DEFAULT_OLLAMA) and "OLLAMA_NUM_GPU" not in env
    assert adopt_job() in (True, False)
    print("[self-check] lifecycle OK - stages/progress/error snapshot, Ollama env mapping (never num_gpu), job object:", bool(_job["handle"]))
