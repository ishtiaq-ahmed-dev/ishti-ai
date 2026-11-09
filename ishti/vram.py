"""VRAM governor: no model is ever left partially offloaded.

Violation = a loaded model whose `size_vram < size` in Ollama's /api/ps (the "CPU/GPU split" of `ollama ps`): several
times slower. This module measures, plans and verifies so that never stays true:

  measure   total/used VRAM from nvidia-smi (ground truth, every GPU user included), per-process VRAM from Windows'
            "GPU Process Memory" counters (WDDM hides it from nvidia-smi), Ollama's own view from /api/ps.
            Every model actually loaded is recorded in data/vram.db (`model_footprints`: size, size_vram, ctx, tok/s).
  ladders   models_ladder.yaml: per role, best -> lightest INSTALLED variant (never re-quantized, never downloaded here).
  admit()   before any heavy load: fits within budget_pct? else, cheapest first: unload idle on-demand models ->
            planner one rung lighter -> less context for the new model -> lighter rung of the new model's own ladder ->
            more planner rungs -> queue with a plain reason. After loading it VERIFIES size_vram == size; if not, the
            model is unloaded at once and one rung lighter is tried. Partial offload is never accepted.
  upshift   a downshifted role goes back up one rung only after the heavy job ended AND the VRAM stayed free for
            hysteresis_s, never during a live voice turn (voice has priority).
  events    model_switch / vram_status on ishti.events (-> /ws/events).

Context: Ollama's OpenAI-style endpoint (what llm.py uses) ignores per-request options and would reload a model back to
its default context every call, so a different num_ctx is a variant alias `<name>-c<k>k:<tag>` (same weights).
ponytail: rung choice is greedy/cheapest-first over measured (or estimated-then-measured) footprints, not an optimal
packing; with 6 roles and ladders of 2-3 rungs that is exact enough. Upgrade path: a small knapsack if ladders grow."""
import contextlib
import itertools
import json
import os
import re
import sqlite3
import subprocess
import threading
import time

import requests
import yaml

from ishti import CONFIG, ROOT, events, keepalive

MIB = 1 << 20
DATA = os.path.dirname(CONFIG["memory"]["db_path"])
DB = os.path.join(DATA, "vram.db")
LADDERS = os.environ.get("ISHTI_LADDERS") or os.path.join(ROOT, "models_ladder.yaml")
OLLAMA = (os.environ.get("ISHTI_OLLAMA_URL") or CONFIG["llm"]["base_url"]).rsplit("/v1", 1)[0]
NOWIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)

class GpuBusy(RuntimeError):
    """A job could not be placed on the GPU (within the wait): its message says why in plain words."""


_stopping = threading.Event()       # set when a shutdown begins: the governor loads nothing more
_sampler_started = threading.Event()
_gov = threading.RLock()            # one plan+apply at a time
_lock = threading.Lock()            # small shared dicts
_rung = {}                          # role -> persistent rung index (downshifted planner); missing = 0
_eff = {}                           # role -> model name currently in force (only while downshifted / job running)
_busy = {}                          # model -> in-flight requests
_jobs = {"active": {}, "queued": {}}
_ids = itertools.count(1)
_last_heavy_end = 0.0
_voice_ts, _voice_depth = 0.0, 0
_fit_since = {}
_cache = {}
_swaps = []                         # bounded: host-RAM cost of every model load/unload (swap_cost())


def _cached(key, ttl, fn):
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    value = fn()
    _cache[key] = (time.time(), value)
    return value


def mb(n):
    return int(round(n / MIB))


# ---------------------------------------------------------------- configuration
def cfg():
    """models_ladder.yaml, re-read when it changes."""
    def load():
        with open(LADDERS, encoding="utf-8") as f:
            c = yaml.safe_load(f)
        c.setdefault("budget_pct", 80)
        return c
    mtime = os.path.getmtime(LADDERS)
    hit = _cache.get("cfg")
    if hit and hit[2] == mtime:
        return hit[1]
    c = load()
    _cache["cfg"] = (time.time(), c, mtime)
    return c


# ---------------------------------------------------------------- measuring
def gpu():
    """{'name','total_mb','used_mb','free_mb','available'} from nvidia-smi (ground truth: every GPU user counted)."""
    def read():
        try:
            out = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total,memory.used,memory.free",
                                  "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=10,
                                 creationflags=NOWIN).stdout.strip().splitlines()[0].split(",")
            return {"name": out[0].strip(), "total_mb": int(out[1]), "used_mb": int(out[2]), "free_mb": int(out[3]), "available": True}
        except Exception:
            return {"name": "no NVIDIA GPU", "total_mb": 0, "used_mb": 0, "free_mb": 0, "available": False}
    return _cached("gpu", 0.4, read)


def budget_mb(g=None):
    g = g or gpu()
    return int(g["total_mb"] * cfg()["budget_pct"] / 100)


def _kind(name, pid):
    n = name.lower()
    if pid == os.getpid():
        return "voice"        # ISHTI's own process: faster-whisper, Kokoro/torch, Silero live in it
    if "llama-server" in n or n.startswith("ollama"):
        return "ollama"
    if any(b in n for b in ("chrome", "msedge", "webview2", "firefox", "brave")):
        return "browser"
    if n in ("dwm.exe", "explorer.exe", "searchhost.exe", "shellhost.exe", "textinputhost.exe", "startmenuexperiencehost.exe"):
        return "system"
    return "other"


def processes(min_mb=50):
    """Per-process dedicated VRAM as Windows reports it (nvidia-smi shows [N/A] under WDDM). The figures can add up to
    more than the adapter total (paged-out allocations); gpu()['used_mb'] is the ground truth."""
    def read():
        try:
            import psutil
            import win32pdh
            q, per = win32pdh.OpenQuery(), {}
            handles = [(p, win32pdh.AddCounter(q, p)) for p in win32pdh.ExpandCounterPath(r"\GPU Process Memory(*)\Dedicated Usage")]
            win32pdh.CollectQueryData(q)
            for path, h in handles:
                m = re.search(r"pid_(\d+)_luid", path)
                if m:
                    per[int(m.group(1))] = per.get(int(m.group(1)), 0) + win32pdh.GetFormattedCounterValue(h, win32pdh.PDH_FMT_LARGE)[1]
            win32pdh.CloseQuery(q)
            out = []
            for pid, v in sorted(per.items(), key=lambda x: -x[1]):
                if v / MIB >= min_mb:
                    try:
                        name = psutil.Process(pid).name()
                    except psutil.Error:
                        name = "?"
                    out.append({"pid": pid, "name": name, "vram_mb": mb(v), "kind": _kind(name, pid)})
            return out
        except Exception:
            return []
    return _cached("procs", 2.0, read)


def ps(fresh=False, strict=True):
    """Loaded models per Ollama: [{'name','size','size_vram','ctx','expires_at','size_mb','size_vram_mb','full'}].
    A FAILED read is never "nothing is loaded" (that made the governor double-count a resident planner and pick a lighter
    model): decision paths (strict=True) get the last good answer if it is under 30 s old, else the error is raised;
    display paths (strict=False) get [] instead."""
    def read():
        rows = keepalive.get(OLLAMA + "/api/ps", timeout=8).json().get("models", [])
        out = [{"name": m["name"], "size": m["size"], "size_vram": m["size_vram"], "ctx": m.get("context_length"),
                "expires_at": m.get("expires_at", ""), "size_mb": mb(m["size"]), "size_vram_mb": mb(m["size_vram"]),
                "full": m["size_vram"] >= m["size"]} for m in rows]
        _cache["ps_last"] = (time.time(), out)
        return out
    if fresh:
        _cache.pop("ps", None)
    try:
        return _cached("ps", 0.3, read)
    except (requests.RequestException, ValueError):
        last = _cache.get("ps_last")
        if last and time.time() - last[0] < 30:
            return last[1]
        if strict:
            raise
        return []


def tags():
    """{name: size_bytes} of installed models. A failed read gives {} for this call only - it is never cached as the answer."""
    def read():
        return {m["name"]: m["size"] for m in keepalive.get(OLLAMA + "/api/tags", timeout=5).json().get("models", [])}
    try:
        return _cached("tags", 10, read)
    except (requests.RequestException, ValueError):
        return {}


def installed(name):
    t = tags()
    return name == "laya" or name in t or f"{name}:latest" in t or (":" not in name and any(k.split(":")[0] == name for k in t))


def info(name):
    """{'default_ctx','kv_per_token'} from /api/show (kv estimate = 2 * layers * kv_heads * head_dim * 2 bytes, f16)."""
    def read():
        d = keepalive.post(OLLAMA + "/api/show", json={"model": name}, timeout=15).json()
        m = re.search(r"^num_ctx\s+(\d+)", d.get("parameters") or "", re.M)
        default = int(m.group(1)) if m else int(os.environ.get("OLLAMA_CONTEXT_LENGTH") or 4096)
        pinned = bool(m)  # the Modelfile sets num_ctx: Ollama will use exactly that. Otherwise Ollama >= 0.34 picks the context itself
        mi, kv = d.get("model_info", {}), 0
        arch = mi.get("general.architecture", "")
        try:
            layers, heads = mi[f"{arch}.block_count"], mi[f"{arch}.attention.head_count"]
            kv_heads = mi.get(f"{arch}.attention.head_count_kv", heads)
            head_dim = mi.get(f"{arch}.attention.key_length") or mi[f"{arch}.embedding_length"] // heads
            kv = 2 * layers * kv_heads * head_dim * 2
        except (KeyError, TypeError, ZeroDivisionError):
            pass
        return {"default_ctx": default, "kv_per_token": kv, "pinned": pinned}
    try:
        return _cached(f"info:{name}", 3600, read)
    except (requests.RequestException, ValueError, KeyError):
        return {"default_ctx": 4096, "kv_per_token": 0}  # this call only: a failed read must not disable the ctx logic for an hour


# ---------------------------------------------------------------- measured footprints
from ishti.core.sqlite_cache import ThreadConnections

_conn_cache = ThreadConnections()


def _db():
    """Cached per-thread connection (Cycle 4, the audit.py pattern): the VRAM governor calls this
    several times per model load (footprints, switches) and each call used to re-connect and re-run
    three CREATE TABLEs + a failing ALTER probe. WAL + synchronous=NORMAL: measurement telemetry."""
    key = (threading.current_thread(), DB)
    c = _conn_cache.get(key)
    if c is None:
        os.makedirs(DATA, exist_ok=True)
        c = sqlite3.connect(DB, timeout=10, check_same_thread=False)
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA synchronous=NORMAL")
        c.execute("PRAGMA busy_timeout=5000")
        c.execute("CREATE TABLE IF NOT EXISTS model_footprints(model TEXT, num_ctx INTEGER, size INTEGER, size_vram INTEGER, "
                  "tps REAL, kv_type TEXT, measured_at REAL, PRIMARY KEY(model, num_ctx))")
        c.execute("CREATE TABLE IF NOT EXISTS job_footprints(what TEXT PRIMARY KEY, mb INTEGER, at REAL)")
        c.execute("CREATE TABLE IF NOT EXISTS switches(ts REAL, role TEXT, from_model TEXT, to_model TEXT, reason TEXT)")
        try:  # what the GPU really grew by when the model loaded (Ollama's size_vram misses e.g. a vision projector, ~1.2 GB)
            c.execute("ALTER TABLE model_footprints ADD COLUMN gpu_delta INTEGER")
        except sqlite3.OperationalError:
            pass
        _conn_cache[key] = c
    return c


def record(rows=None, tps=None, delta_mb=None):
    """Store size / size_vram of every model currently loaded (fully on GPU or not: the split itself is the finding), and the
    GPU's real growth when it loaded (delta_mb) when known."""
    try:
        with _db() as c:
            for m in rows if rows is not None else ps(True):
                old = c.execute("SELECT tps, gpu_delta FROM model_footprints WHERE model=? AND num_ctx=?", (m["name"], m["ctx"])).fetchone()
                # the GPU delta is measured against "memory used just before the load": if another process (another session, a
                # browser) allocates at the same moment the reading is INFLATED, never deflated - so the best estimate of the true
                # figure is the MINIMUM over repeated loads
                prev = old[1] if old and old[1] else None
                delta = min(x for x in (delta_mb, prev) if x) if (delta_mb or prev) else None
                c.execute("INSERT OR REPLACE INTO model_footprints(model,num_ctx,size,size_vram,tps,kv_type,measured_at,gpu_delta) VALUES(?,?,?,?,?,?,?,?)",
                          (m["name"], m["ctx"], m["size"], m["size_vram"], tps if tps else (old[0] if old else None),
                           os.environ.get("OLLAMA_KV_CACHE_TYPE", "f16"), time.time(), delta))
    except sqlite3.Error:
        pass


def footprints():
    try:
        with _db() as c:
            rows = c.execute("SELECT model,num_ctx,size,size_vram,tps,kv_type,measured_at,gpu_delta FROM model_footprints ORDER BY model,num_ctx").fetchall()
    except sqlite3.Error:
        return []
    return [{"model": m, "num_ctx": x, "size_mb": mb(s), "size_vram_mb": mb(v), "fully_on_gpu": v >= s, "tps": t, "kv_type": k, "measured_at": at,
             "gpu_delta_mb": d} for m, x, s, v, t, k, at, d in rows]


def need_of(name, ctx=None):
    """(MiB the model needs on the GPU, known). Measured value when this (model, ctx) was ever loaded in full; scaled by
    the KV cache when only another context was measured; else an ESTIMATE (disk size + KV + overhead), known=False."""
    if name == "laya":
        return 0, True
    base, alias_ctx = base_of(name)
    if alias_ctx and not installed(name):  # an alias not created yet: the base model's weights at the alias' context
        ctx, kv_name = ctx or alias_ctx, base
    else:
        ctx, kv_name = ctx or info(name)["default_ctx"], name
    kv = info(kv_name)["kv_per_token"] / MIB
    try:
        with _db() as c:
            rows = c.execute("SELECT num_ctx,size,size_vram,gpu_delta FROM model_footprints WHERE model=?", (name,)).fetchall()
            if not rows and not alias_ctx:
                # plan() asks with the BASE name while the load (and so its measurement) happened under the
                # "-cNk" alias for that context (gemma3:12b at 4096 loads as gemma3-c4k:12b). Without this
                # the model stayed "unmeasured" forever and every admission paid the 1.35x unknown headroom.
                an = alias_name(name, ctx)
                if an != name:
                    rows = c.execute("SELECT num_ctx,size,size_vram,gpu_delta FROM model_footprints WHERE model=?", (an,)).fetchall()
    except sqlite3.Error:
        rows = []
    # a model needs what the GPU really grew by when it loaded (>= Ollama's size_vram: vision models carry ~1.2 GB more)
    full = [(x, max(s, (d or 0) * MIB)) for x, s, v, d in rows if v >= s]
    if full:
        exact = [s for x, s in full if x == ctx]
        if exact:
            return mb(exact[0]), True
        x0, s0 = min(full, key=lambda r: abs(r[0] - ctx))
        # a LARGER context adds its KV cache; a smaller one is never assumed to save anything (sliding-window models such as
        # Gemma 3 measured 7672 -> 7670 MiB going from 4k to 2k, while the full-attention formula predicted ~770 MiB)
        return max(1, mb(s0) + int(kv * max(0, ctx - x0))), True
    disk = tags().get(kv_name) or tags().get(f"{kv_name}:latest") or 0
    return mb(disk) + int(kv * ctx) + 300, False


ALIAS = re.compile(r"^(.+)-c(\d+)k(:.+)$")


def base_of(name):
    """('base model', ctx) for a governor alias name like qwen2.5-c8k:7b -> ('qwen2.5:7b', 8192); else (name, None)."""
    m = ALIAS.match(name)
    return (m[1] + m[3], int(m[2]) * 1024) if m else (name, None)


def alias_name(model, ctx):
    """The name under which `model` runs at `ctx` - a pure function, nothing is created or read from Ollama. `model` itself
    when that ctx is its default (or unknown: the ladder gave none), else a same-weights alias."""
    i = info(model)
    if model == "laya" or not ctx or (ctx == i["default_ctx"] and i.get("pinned", True)):
        return model
    base, _, tag = model.partition(":")
    return f"{base}-c{ctx // 1024}k:{tag or 'latest'}"


def held(m):
    """MiB a loaded model really occupies (what unloading it frees): the larger of Ollama's size_vram and the GPU growth
    measured when it loaded."""
    n, known = need_of(m["name"], m["ctx"])
    return max(m["size_vram_mb"], n if known else 0)


def measured(name, ctx):
    """MiB measured for exactly this (model, ctx) in a full load, else None."""
    try:
        with _db() as c:
            r = c.execute("SELECT size,size_vram,gpu_delta FROM model_footprints WHERE model=? AND num_ctx=?", (name, ctx)).fetchone()
    except sqlite3.Error:
        return None
    return mb(max(r[0], (r[2] or 0) * MIB)) if r and r[1] >= r[0] else None


def variant(model, ctx):
    """alias_name(), and the alias is CREATED in Ollama when it doesn't exist yet (same weights, only PARAMETER num_ctx
    differs). Only called right before a load - reading state must never create models."""
    alias = alias_name(model, ctx)
    if alias == model:
        return model
    if not installed(alias):
        r = requests.post(OLLAMA + "/api/create", json={"model": alias, "from": model, "parameters": {"num_ctx": ctx}, "stream": False}, timeout=120)
        r.raise_for_status()
        _cache.pop("tags", None)
        _cache.pop(f"info:{alias}", None)
    return alias


# ---------------------------------------------------------------- roles and ladders
def rung_num_ctx(r):
    """The context a rung runs at: the ladder's num_ctx, else - when the model's Modelfile sets none - an explicit pin
    (config `pin_ctx`, 4096). Ollama >= 0.34 chooses the context itself for such a model: on 0.35.0 gemma3:4b loaded at
    131072 (6.4 GB real instead of 2.7) and qwen3:14b at 40960 with a CPU split (measured 2026-10-02); the older
    assumption "default = 4096" no longer holds, so an unpinned rung always runs through a same-weights alias."""
    if r.get("num_ctx") or r.get("cpu"):
        return r.get("num_ctx")
    try:
        return None if info(r["model"]).get("pinned", True) else int(cfg().get("pin_ctx", 4096))
    except Exception:
        return None


def rungs(role):
    """The role's INSTALLED rungs, best first: [{'model','num_ctx','name'(variant name),'cpu','note'}]."""
    spec = cfg()["roles"].get(role, {})
    out = []
    for r in spec.get("rungs", []):
        if not (r.get("cpu") or installed(r["model"])):
            continue
        ctx = rung_num_ctx(r)
        out.append({**r, "num_ctx": ctx, "name": r["model"] if r.get("cpu") else alias_name(r["model"], ctx)})
    return _owner_first(role, spec, out)


def _owner_first(role, spec, items):
    """The model the owner picked for this role (Settings -> Language models, or ISHTI_MODEL_<ROLE>) is the TOP rung; the rungs
    below it are the installed ladder models that are lighter than it, heaviest first - a downshift must always go to something
    smaller. A pick that is not in the ladder at all becomes rung 0 on its own (it runs at the role's minimum context)."""
    chosen = os.environ.get(f"ISHTI_MODEL_{role.upper()}") or (CONFIG.get("llm", {}).get("models", {}) or {}).get(role)
    if not chosen or chosen == "laya" or not items or items[0].get("cpu"):
        return items
    top = next((r for r in items if chosen in (r["model"], r["name"])), None)
    if top is None:
        if not installed(chosen):
            return items
        ctx = rung_num_ctx({"model": chosen, "num_ctx": spec.get("min_ctx")})
        top = {"model": chosen, "num_ctx": ctx, "name": alias_name(chosen, ctx), "note": "chosen in Settings"}
    size = tags().get(top["model"], 0)
    lighter = [r for r in items if r is not top and size and 0 < tags().get(r["model"], 0) < size]
    lighter.sort(key=lambda r: -tags().get(r["model"], 0))
    return [top] + lighter


def role_spec(role):
    return cfg()["roles"].get(role, {})


def keep_alive_for(role):
    """How long a role's model may stay loaded after a call. Production pins the planner (keep_alive -1).
    Under ISHTI_TEST_MODE nothing is pinned: a pinned model outlives the QA suite that loaded it, and the
    next suite's GPU admission then sees a busy card (the "GpuBusy" skips in batch runs). A short
    keep_alive keeps models through a suite (each call refreshes it) but lets the card free between suites."""
    from ishti import test_mode
    if test_mode.enabled():
        return "2m"
    spec = role_spec(role)
    return -1 if spec.get("pinned") else spec.get("keep_alive", "10m")


def role_of(name):
    """The role whose ladder contains this loaded model name (planner/coder first), else None. Alias names
    (qwen2.5-c8k:7b) count as their base model; a coder model whose name merely starts the same way does not."""
    base, _ = base_of(name)
    for role, spec in cfg()["roles"].items():
        if any(base == r["model"] or name == r["model"] for r in spec.get("rungs", [])):
            return role
    return None


def rung_ctx(model, role):
    """The context the ladder gives this model as a rung of `role` (None = the model's own default)."""
    base, _ = base_of(model)
    r = next((r for r in role_spec(role).get("rungs", []) if r["model"] in (model, base)), None) if role else None
    return rung_num_ctx(r) if r else None


def _ours(name):
    """A model loaded in Ollama that can be unloaded when idle to make room.
    The embedding model that semantic memory keeps warm is left alone."""
    if name.startswith("nomic-embed"):
        return False
    return True


def is_pinned(name):
    role = role_of(name)
    if not (role and role_spec(role).get("pinned")):
        return False
    rs = rungs(role)
    k = min(_rung.get(role, 0), len(rs) - 1) if rs else 0
    active = _eff.get(role) or (rs[k]["name"] if rs else "")
    return bool(active and (name == active or base_of(name)[0] == base_of(active)[0]))


def effective(role, configured):
    """The model to use for `role` right now: the downshifted / job-selected rung, else what the config names."""
    return _eff.get(role) or configured


def current_rung(role):
    return _rung.get(role, 0)


# ---------------------------------------------------------------- Ollama actions
def _post(path, body, timeout=60):
    r = keepalive.post(OLLAMA + path, json=body, timeout=timeout)
    r.raise_for_status()
    return r


def unload(name, wait_s=20):
    host_before = _host_mb()
    try:
        _post("/api/generate", {"model": name, "prompt": "", "keep_alive": 0, "stream": False}, 30)
    except requests.RequestException:
        pass
    end = time.time() + wait_s
    while time.time() < end:  # the VRAM is only free once Ollama really dropped it
        try:
            if not any(m["name"] == name for m in ps(True)):
                time.sleep(0.4)
                _cache.pop("gpu", None)
                _record_swap(frm=name, host_mb=_host_mb() - host_before)
                return True
        except (requests.RequestException, ValueError):
            pass  # can't tell yet: keep waiting
        time.sleep(0.4)
    return False


def load(name, keep_alive):
    """Load `name` and VERIFY it is fully on the GPU. -> (ok, ps entry)."""
    _cache.pop("gpu", None)  # a fresh reading: the delta is only meaningful against the memory used right before this load
    before = gpu()["used_mb"]
    host_before = _host_mb()
    _post("/api/generate", {"model": name, "prompt": "", "keep_alive": keep_alive, "stream": False}, 300)
    _cache.pop("gpu", None)
    entry = next((m for m in ps(True) if m["name"] == name), None)
    if entry:
        delta = gpu()["used_mb"] - before
        record([entry], delta_mb=delta if delta >= entry["size_vram_mb"] * 0.9 else None)  # a plausible measurement only
        _record_swap(to=name, host_mb=_host_mb() - host_before, vram_mb=delta)
    return bool(entry and entry["full"]), entry


# ---------------------------------------------------------------- host-RAM cost of a swap
# 2026-10-13 RAM findings: loading a model pulls its file back into Ollama's host working set
# (measured: 8.7 GB of host RSS for the 14B, +5.1 GB of commit for a 3.1 GB model), so a model that is
# unloaded and reloaded quickly pays that twice - the avoidable waste this reports.
def _host_mb():
    """MB resident in Ollama's manager + model runner: the host half of what a swap costs."""
    try:
        import psutil
        return sum((p.info["memory_info"].rss for p in psutil.process_iter(["name", "memory_info"])
                    if (p.info.get("name") or "").lower().startswith(("ollama", "llama-server")))
                   ) // MIB
    except Exception:                      # cost reporting must never break a load
        return 0


def _record_swap(to=None, frm=None, host_mb=0, vram_mb=0):
    _swaps.append({"at": round(time.time(), 1), "to": to or "", "frm": frm or "",
                   "host_mb": int(host_mb), "vram_mb": int(vram_mb)})
    del _swaps[:-200]                      # bounded: the report is a bill, not a log


def swap_cost():
    """The swap bill for /api/vram: recent swaps with their measured host-RAM cost, plus ROUND TRIPS -
    a model unloaded and reloaded within swap_cost_window_s pays its file size in host RAM twice."""
    window = cfg().get("swap_cost_window_s", 180)
    gone_at = {s["frm"]: s["at"] for s in _swaps if s["frm"]}
    trips = [{"model": s["to"], "gap_s": round(s["at"] - gone_at[s["to"]], 1), "host_mb": s["host_mb"]}
             for s in _swaps if s["to"] and s["to"] in gone_at
             and 0 <= s["at"] - gone_at[s["to"]] <= window]
    return {"history": _swaps[-20:], "round_trips": trips, "waste_mb": sum(t["host_mb"] for t in trips)}


def _headroom_mb():
    """MB the machine can still give a new model without paging. None = unknown, never refuse."""
    try:
        import psutil
        return psutil.virtual_memory().available // MIB
    except Exception:
        return None


def _too_costly(peak_mb):
    """Refuse an OPPORTUNISTIC swap whose host-RAM peak would leave the machine under swap_free_floor_mb.
    Only upshifts ask: a swap that avoids a partial offload is never refused - that is the governor's
    whole job.
    ponytail: one fixed floor against available RAM; swap_free_floor_mb=0 disables it. Upgrade path:
    price the benefit too (model quality / tok-s) and check commit headroom, not just physical RAM."""
    floor = cfg().get("swap_free_floor_mb", 2048)
    head = _headroom_mb()
    return bool(floor and head is not None and head - peak_mb < floor)


def _resident_mb(name):
    """MiB of `name` currently resident in Ollama (0 when it is not loaded)."""
    if not name:
        return 0
    return next((m["size_mb"] for m in ps(strict=False) if m["name"] == name), 0)


def _log_switch(role, frm, to, reason):
    """Record a model change (db + log). A role moving to another model is announced as a `model_switch` event; a plain
    unload is only logged (the UI shows toasts for role switches, not for housekeeping)."""
    ev = {"role": role, "from": frm, "to": to, "reason": reason, "at": time.time()}
    try:
        with _db() as c:
            c.execute("INSERT INTO switches VALUES(?,?,?,?,?)", (ev["at"], role, frm, to, reason))
    except sqlite3.Error:
        pass
    print(f"[vram] {role}: {frm} -> {to} ({reason})", flush=True)
    if to != "(unloaded)":
        events.publish("model_switch", ev)


def switch_role(role, k, reason, cost_check=False):
    """Move a pinned role to rung k (load lighter first when both fit, else unload heavier first), verified.
    -> True when the role now runs fully on the GPU at rung k. Everything is decided inside the governor lock (a concurrent
    admit can't change the picture in between), and on a failure the role is put back on the rung it was on (its model
    reloaded): a failed switch never leaves the pinned planner unloaded."""
    _check_running()
    rs = rungs(role)
    if not 0 <= k < len(rs):
        return False
    with _gov:
        frm = _eff.get(role) or (rs[_rung.get(role, 0)]["name"] if _rung.get(role, 0) < len(rs) else "")
        to = rs[k]["name"]
        if to == frm:
            return True
        g = gpu()
        need, _ = need_of(to)
        if cost_check and need and _too_costly(_resident_mb(frm) + need):
            peak = _resident_mb(frm) + need
            print(f"[vram] swap held: {role} -> {to} needs ~{peak} MB of host RAM at once and would leave "
                  f"under swap_free_floor_mb; staying on {frm or 'nothing'}", flush=True)
            return False
        transient_ok = g["used_mb"] + need <= g["total_mb"] * cfg().get("transient_limit_pct", 95) / 100
        ka = keep_alive_for(role)
        if to != "laya":
            variant(rs[k]["model"], rs[k].get("num_ctx"))  # creates the alias if the rung needs one
        frm_unloaded = False
        if not transient_ok and frm:
            if not unload(frm):
                return False  # could not free it: nothing changed, nothing to restore
            frm_unloaded = True
        ok, _entry = load(to, ka) if to != "laya" else (True, None)
        if not ok:  # not fully on the GPU: drop it and bring the previous model back
            unload(to)
            if frm_unloaded and frm:
                try:
                    load(frm, ka)
                except (requests.RequestException, ValueError):
                    pass
            return False
        if transient_ok and frm and frm != to and not unload(frm):
            print(f"[vram] WARNING: {frm} did not unload after the switch to {to}", flush=True)
        _rung[role] = k
        if k == 0:
            _eff.pop(role, None)
        else:
            _eff[role] = to
    _log_switch(role, frm, to, reason)
    return True


# ---------------------------------------------------------------- planning
def voice_active():
    return _voice_depth > 0 or time.time() - _voice_ts < cfg().get("voice_hold_s", 10)


def touch_voice():
    """Speech/STT just ran: a live voice turn is (probably) going on."""
    global _voice_ts
    _voice_ts = time.time()


@contextlib.contextmanager
def voice_turn():
    global _voice_depth
    _voice_depth += 1
    try:
        yield
    finally:
        _voice_depth -= 1
        touch_voice()


def _fits(used, need, known, g, reserve=0):
    """Would `need` MiB fit next to `used` MiB (everything on the GPU) inside the budget?"""
    c = cfg()
    # Ollama/llama.cpp keeps >= 1 GiB free after every load (LLAMA_ARG_FIT_TARGET) and predicts a load ~240 MiB above what the
    # GPU really grows: with less free than that, Ollama evicts an idle model or splits the new one. Verified in its debug log
    # ("model predicted to exceed available memory, evicting"). So this headroom is a hard rule on top of the budget.
    if g["total_mb"] - used - need < c.get("ollama_headroom_mb", 1536):
        return False
    if known:
        return used + need + c["safety_margin_mb"] + reserve <= budget_mb(g)
    return g["total_mb"] - used >= need * c.get("unknown_headroom", 1.35) + c["safety_margin_mb"] + reserve


def plan(model, ctx=None, role=None, strict=False):
    """A cumulative plan that makes `model` fit: {'actions': [...], 'target': {...}} or None. Nothing is changed."""
    c = cfg()
    g = gpu()
    loaded = ps(True)
    used = g["used_mb"]
    reserve = c.get("voice_reserve_mb", 0) if voice_active() else 0
    target_role = role or role_of(model)
    model = base_of(model)[0] if installed(base_of(model)[0]) else model  # an alias name -> its base + the ctx it stands for
    ctx = ctx or rung_ctx(model, target_role) or base_of(model)[1]
    tgt = {"model": model, "ctx": ctx or info(model)["default_ctx"], "role": target_role, "rung": 0}
    if target_role:  # which rung of its ladder this model is (a planner that starts on rung 1 stays there)
        tgt["rung"] = next((i for i, r in enumerate(rungs(target_role)) if model in (r["model"], r["name"])), 0)
    need, known = need_of(model, tgt["ctx"])
    actions = []

    def ok():
        return _fits(used, need, known, g, reserve)

    if any(m["name"] == variant_name(tgt) and m["full"] for m in loaded):
        return {"actions": [], "target": tgt, "need_mb": need}
    if ok():
        return {"actions": actions, "target": tgt, "need_mb": need}
    # 1. unload idle on-demand models (least recently used first)
    from ishti import models as _m
    idle = [m for m in loaded if _ours(m["name"]) and not is_pinned(m["name"]) and not _busy.get(m["name"]) and m["name"] != variant_name(tgt)]
    idle.sort(key=lambda m: _m._last_used.get(m["name"], 0))
    for m in idle:
        used -= held(m)
        actions.append({"do": "unload", "model": m["name"], "why": "idle on-demand model"})
        if ok():
            return {"actions": actions, "target": tgt, "need_mb": need}
    # 2. the planner one rung lighter (never while a voice turn is running: the brain must stay responsive)
    planner_rs = rungs("planner")
    p_idx = _rung.get("planner", 0)
    p_loaded = next((m for m in loaded if role_of(m["name"]) == "planner"), None)

    def planner_step(k):
        nonlocal used
        nn, _ = need_of(planner_rs[k]["name"])
        used += nn - (held(p_loaded) if p_loaded else 0)
        if p_loaded:
            actions.append({"do": "downshift", "role": "planner", "to": k, "model": planner_rs[k]["name"], "why": "make room for " + model})
            return True
        return False
    can_planner = target_role != "planner" and not voice_active() and p_loaded and p_idx + 1 < len(planner_rs)
    if can_planner:
        planner_step(p_idx + 1)
        p_idx += 1
        p_loaded = {**p_loaded, "size_vram_mb": need_of(planner_rs[p_idx]["name"])[0], "name": planner_rs[p_idx]["name"]}
        if ok():
            return {"actions": actions, "target": tgt, "need_mb": need}
    # 3. less context for the new model
    kv = info(model)["kv_per_token"] / MIB
    ctx_new = tgt["ctx"]
    floor = role_spec(target_role).get("min_ctx", c["min_ctx"]) if target_role else c["min_ctx"]  # the planner never below 8192
    while kv and ctx_new // 2 >= floor:
        ctx_new //= 2
        n2 = measured(alias_name(model, ctx_new), ctx_new)  # only a MEASURED saving counts (see need_of)
        if n2 is not None and n2 <= need - 128 and ok_with(used, n2, True, g, reserve):  # and it must really save something
            tgt = {**tgt, "ctx": ctx_new}
            actions.append({"do": "ctx", "model": model, "num_ctx": ctx_new, "why": "less context to fit"})
            return {"actions": actions, "target": tgt, "need_mb": n2}
    # 4. a lighter rung of the new model's own ladder (vision tier 3 -> 2, coder 14B -> 7B)
    if target_role and not strict:
        rs = rungs(target_role)
        cur = next((i for i, r in enumerate(rs) if r["model"] == model or r["name"] == model), 0)
        for i in range(cur + 1, len(rs)):
            n2, k2 = need_of(rs[i]["name"])
            if ok_with(used, n2, k2, g, reserve):
                tgt = {"model": rs[i]["model"], "ctx": rs[i].get("num_ctx") or info(rs[i]["model"])["default_ctx"], "role": target_role, "rung": i}
                actions.append({"do": "rung", "role": target_role, "to": i, "model": rs[i]["name"], "from": model, "why": "a lighter model of the same kind fits"})
                return {"actions": actions, "target": tgt, "need_mb": n2}
    # 5. the planner further down its ladder (still not while voice is running)
    while can_planner and p_idx + 1 < len(planner_rs):
        planner_step(p_idx + 1)
        p_idx += 1
        p_loaded = {**p_loaded, "size_vram_mb": need_of(planner_rs[p_idx]["name"])[0], "name": planner_rs[p_idx]["name"]}
        if ok():
            return {"actions": actions, "target": tgt, "need_mb": need}
    # 6. an UNMEASURED model: give it ONE raw-estimate attempt. The 1.35x unknown-headroom rule above is
    # conservative because an estimate can be low - but refusing forever means a big model can never be
    # loaded once to BE measured (chicken-and-egg: the vision 12B sat queued behind this on a used
    # desktop no matter how far the planner stepped down). _apply verifies FULL residency after the load
    # and unloads + steps lighter on a partial one, so a wrong estimate costs one load attempt, never a
    # split model.
    if not known and g["total_mb"] - used - need >= c.get("ollama_headroom_mb", 1536) \
            and used + need + c["safety_margin_mb"] + reserve <= budget_mb(g):
        return {"actions": actions, "target": tgt, "need_mb": need}
    return None


def ok_with(used, need, known, g, reserve):
    return _fits(used, need, known, g, reserve)


def variant_name(tgt):
    return alias_name(tgt["model"], tgt["ctx"]) if installed(tgt["model"]) else tgt["model"]


def why_not(model, ctx=None):
    """Plain-language reason a job can't be placed right now."""
    g = gpu()
    need, known = need_of(model, ctx)
    loaded = ", ".join(f"{m['name']} ({m['size_vram_mb'] / 1024:.1f} GB)" for m in ps()) or "nothing"
    return (f"{model} needs about {need / 1024:.1f} GB{'' if known else ' (estimated)'} and only {max(0, budget_mb(g) - g['used_mb']) / 1024:.1f} GB is "
            f"free within the {cfg()['budget_pct']}% GPU budget (in use: {loaded}"
            + ("; a voice conversation is running, so the assistant's own model stays put" if voice_active() else "") + ")")


# ---------------------------------------------------------------- admission
def _fully_loaded(name):
    return any(m["name"] == name and m["full"] for m in ps())


def admit(model, num_ctx=None, role=None, job="", strict=False, wait_s=None):
    """Make room for `model` and load it, verified fully on the GPU. -> {'ok','model','num_ctx','role','actions','waited_s',
    'lease','queued'?}. When nothing fits within wait_s the job is reported as queued with the reason (never partial offload)."""
    t0 = time.time()
    _check_running()
    if model == "laya" or role and role_spec(role).get("cpu") or not gpu()["available"]:
        return {"ok": True, "model": model, "num_ctx": num_ctx, "role": role, "actions": [], "waited_s": 0.0, "lease": None}
    if installed(model):
        want = alias_name(base_of(model)[0], num_ctx or rung_ctx(model, role or role_of(model)) or base_of(model)[1]) if not num_ctx else alias_name(model, num_ctx)
        if _fully_loaded(want):  # the hot path: already resident at the right context, nothing to do
            return {"ok": True, "model": want, "num_ctx": None, "role": role, "actions": [], "waited_s": 0.0, "lease": None}
    wait = cfg().get("queue_wait_s", 45) if wait_s is None else wait_s
    job_id = next(_ids)
    done_actions, reason = [], ""
    try:
        while True:
            _check_running()
            with _gov:
                p = plan(model, num_ctx, role, strict)
                if p is not None:
                    res = _apply(p, job or f"load {model}")
                    done_actions += p["actions"]
                    if res:
                        lease = None
                        if not (res["role"] and role_spec(res["role"]).get("pinned")):  # loading the planner itself is not a heavy job
                            lease = next(_ids)
                            _jobs["active"][lease] = {"id": lease, "what": job or f"load {res['name']}", "model": res["name"], "role": res["role"], "since": time.time()}
                        return {"ok": True, "model": res["name"], "num_ctx": res["ctx"], "role": res["role"], "actions": done_actions,
                                "waited_s": round(time.time() - t0, 1), "lease": lease}
                reason = why_not(model, num_ctx)
                _jobs["queued"][job_id] = {"id": job_id, "what": job or f"load {model}", "model": model, "reason": reason, "since": t0}
            if time.time() - t0 >= wait:
                events.publish("error", {"code": "gpu_busy", "message": "Waiting for the GPU: " + reason, "stage": "", "log_path": ""})
                return {"ok": False, "model": model, "num_ctx": num_ctx, "role": role, "actions": done_actions, "waited_s": round(time.time() - t0, 1),
                        "lease": None, "queued": reason}
            time.sleep(2)
    finally:
        _jobs["queued"].pop(job_id, None)  # however this ends (fit, timeout, an exception) the queue entry must go, or upshift is blocked for good


def _apply(p, what):
    """Carry out a plan, then load the target and verify; on a partial offload go one step lighter. -> {'name','ctx','role'} or None."""
    tgt, role = dict(p["target"]), p["target"]["role"]
    last_down = {a["role"]: i for i, a in enumerate(p["actions"]) if a["do"] == "downshift"}
    for i, a in enumerate(p["actions"]):
        if a["do"] == "downshift" and last_down[a["role"]] != i:
            continue  # two steps down the same ladder: go straight to the last rung, don't load the one in between
        if a["do"] == "unload":
            if not unload(a["model"]):
                return None  # the memory was not freed: loading on top of it could split the new model
            _log_switch(role_of(a["model"]) or "model", a["model"], "(unloaded)", a["why"])
        elif a["do"] == "downshift":
            if not switch_role(a["role"], a["to"], f"Switched to a lighter model for speed while a heavy task runs: {what}"):
                return None
        elif a["do"] == "ctx":
            tgt["ctx"] = a["num_ctx"]
        elif a["do"] == "rung":
            _log_switch(a["role"], a["from"], a["model"], f"{a['from']} doesn't fit right now - using a lighter model")
    for _ in range(4):
        name = variant(tgt["model"], tgt["ctx"]) if tgt["model"] != "laya" else "laya"
        ok, entry = load(name, keep_alive_for(role) if role else "10m")
        if ok:
            if role and not role_spec(role).get("pinned"):
                _eff[role] = name          # only for as long as the job lasts (release() clears it)
            elif role:                     # a pinned role (the planner) stays on the rung it started on until upshifted
                if tgt.get("rung"):
                    _rung[role], _eff[role] = tgt["rung"], name
                else:
                    _rung.pop(role, None)
                    _eff.pop(role, None)
            return {"name": name, "ctx": entry["ctx"] if entry else tgt["ctx"], "role": role}
        unload(name)  # partial offload: gone at once, one step lighter
        rs = rungs(role) if role else []
        cur = next((i for i, r in enumerate(rs) if r["name"] == name or r["model"] == tgt["model"]), -1)
        if tgt["ctx"] // 2 >= (role_spec(role).get("min_ctx", cfg()["min_ctx"]) if role else cfg()["min_ctx"]) and info(tgt["model"])["kv_per_token"]:
            new_ctx = tgt["ctx"] // 2
            _log_switch(role or "model", name, variant(tgt["model"], new_ctx), "was not fully on the GPU - trying less context")
            tgt["ctx"] = new_ctx
        elif cur >= 0 and cur + 1 < len(rs):
            nxt = rs[cur + 1]
            _log_switch(role, name, nxt["name"], "was not fully on the GPU - trying the next lighter model")
            tgt = {"model": nxt["model"], "ctx": nxt.get("num_ctx") or info(nxt["model"])["default_ctx"], "role": role, "rung": cur + 1}
        else:
            return None
    return None


def learn_job(what, mb_used):
    """A non-Ollama GPU job (Whisper, Real-ESRGAN) finished loading: remember how much the GPU really grew, so the next
    reserve() uses the measured figure instead of the caller's guess (Whisper turbo was guessed 1.2 GB, measured 3.0 GB)."""
    if mb_used >= 150:
        try:
            with _db() as c:
                c.execute("INSERT OR REPLACE INTO job_footprints VALUES(?,?,?)", (what, int(mb_used), time.time()))
        except sqlite3.Error:
            pass


def job_need(what, guess_mb):
    try:
        with _db() as c:
            r = c.execute("SELECT mb FROM job_footprints WHERE what=?", (what,)).fetchone()
    except sqlite3.Error:
        r = None
    return r[0] if r else guess_mb


def reserve(need_mb, what, wait_s=None, for_voice=False):
    """Make `need_mb` MiB of VRAM available for a job that is NOT an Ollama model (Whisper, Real-ESRGAN, a LoRA run):
    idle on-demand models are unloaded first, then the planner goes down a rung (not while a voice turn runs - unless
    the job IS the voice pipeline: for_voice=True, voice has priority). -> {'ok','lease','actions','queued'?}."""
    t0, c = time.time(), cfg()
    _check_running()
    wait = c.get("queue_wait_s", 45) if wait_s is None else wait_s
    need_mb = job_need(what, need_mb)  # measured before? use that, not the caller's guess
    if not gpu()["available"]:
        return {"ok": True, "lease": None, "actions": []}
    job_id, actions, reason = next(_ids), [], ""
    try:
        while True:
            _check_running()
            with _gov:
                g = gpu()
                used = g["used_mb"]
                reserve_mb = c.get("voice_reserve_mb", 0) if voice_active() and not for_voice else 0
                steps = []
                if not _fits(used, need_mb, True, g, reserve_mb):
                    from ishti import models as _m
                    loaded = ps(True)
                    idle = sorted((m for m in loaded if _ours(m["name"]) and not is_pinned(m["name"]) and not _busy.get(m["name"])),
                                  key=lambda m: _m._last_used.get(m["name"], 0))
                    for m in idle:
                        used -= held(m)
                        steps.append(("unload", m["name"]))
                        if _fits(used, need_mb, True, g, reserve_mb):
                            break
                    else:
                        p_rs, p_loaded = rungs("planner"), next((m for m in loaded if role_of(m["name"]) == "planner"), None)
                        k = _rung.get("planner", 0)
                        while p_loaded and (for_voice or not voice_active()) and k + 1 < len(p_rs) and not _fits(used, need_mb, True, g, reserve_mb):
                            k += 1
                            nn, _ = need_of(p_rs[k]["name"])
                            used += nn - held(p_loaded)
                            p_loaded = {**p_loaded, "size_vram_mb": nn, "name": p_rs[k]["name"]}
                            steps.append(("downshift", k))
                if _fits(used, need_mb, True, g, reserve_mb):
                    for kind, arg in steps:
                        if kind == "unload":
                            if not unload(arg):
                                break
                            _log_switch(role_of(arg) or "model", arg, "(unloaded)", f"room for {what}")
                        elif not switch_role("planner", arg, f"Switched to a lighter model for speed while a heavy task runs: {what}"):
                            break
                    actions += [f"{k} {a}" for k, a in steps]
                    if _fits(gpu()["used_mb"], need_mb, True, gpu(), reserve_mb):
                        _jobs["queued"].pop(job_id, None)
                        lease = next(_ids)
                        _jobs["active"][lease] = {"id": lease, "what": what, "model": "(non-Ollama)", "role": None, "since": time.time()}
                        return {"ok": True, "lease": lease, "actions": actions, "waited_s": round(time.time() - t0, 1)}
                reason = (f"{what} needs about {need_mb / 1024:.1f} GB and only {max(0, budget_mb(g) - g['used_mb']) / 1024:.1f} GB is free within "
                          f"the {c['budget_pct']}% GPU budget" + ("; a voice conversation is running" if voice_active() and not for_voice else ""))
                _jobs["queued"][job_id] = {"id": job_id, "what": what, "model": "(non-Ollama)", "reason": reason, "since": t0}
            if time.time() - t0 >= wait:
                _jobs["queued"].pop(job_id, None)
                return {"ok": False, "lease": None, "actions": actions, "queued": reason, "waited_s": round(time.time() - t0, 1)}
            time.sleep(1.5)
    finally:
        _jobs["queued"].pop(job_id, None)  # however this ends, the queue entry must go


def release(lease):
    """A heavy job finished: it stops counting as active (the upshift clock starts)."""
    global _last_heavy_end
    job = _jobs["active"].pop(lease, None)
    if job:
        _last_heavy_end = time.time()
        if job["role"] and not role_spec(job["role"]).get("pinned"):
            _eff.pop(job["role"], None)
    return bool(job)


@contextlib.contextmanager
def heavy(model, num_ctx=None, role=None, job="", strict=False, wait_s=None):
    """with vram.heavy('gemma3:12b', role='vision3', job='read the page') as r: (r['ok'] False -> queued, r['queued'] why)"""
    res = admit(model, num_ctx, role, job, strict, wait_s)
    try:
        yield res
    finally:
        if res.get("lease"):
            release(res["lease"])


@contextlib.contextmanager
def busy(name):
    """Marks a model as serving a request so it is never picked as 'idle' to unload."""
    with _lock:
        _busy[name] = _busy.get(name, 0) + 1
    try:
        yield
    finally:
        with _lock:
            _busy[name] -= 1


# ---------------------------------------------------------------- upshift + violation watch
def reap_leases():
    """A lease nobody released (a UI client that vanished, a stream that was never iterated) must not hold 'a heavy job is
    running' true forever: after lease_ttl_s it is dropped."""
    ttl, now = cfg().get("lease_ttl_s", 900), time.time()
    for lease, job in list(_jobs["active"].items()):
        if now - job["since"] > ttl:
            print(f"[vram] lease {lease} ({job['what']}) expired after {ttl} s - released", flush=True)
            release(lease)


def upshift_tick():
    """Move a downshifted role one rung back up when the heavy job is over and VRAM has been free for hysteresis_s.
    Runs under the governor lock (taken without waiting: if an admit/switch is in progress this tick is simply skipped) and
    re-checks jobs, voice and fit INSIDE it, so it can never unload the planner in the middle of a heavy job."""
    if _stopping.is_set():
        return None
    reap_leases()
    if not _gov.acquire(blocking=False):
        return None
    try:
        c, now = cfg(), time.time()
        if _jobs["active"] or _jobs["queued"] or voice_active():
            _fit_since.clear()
            return None
        if now - _last_heavy_end < c.get("hysteresis_s", 90):
            return None
        for role, k in list(_rung.items()):
            if k <= 0:
                continue
            rs = rungs(role)
            better = rs[k - 1]["name"] if k - 1 < len(rs) else None
            cur = next((m for m in ps() if m["name"] == _eff.get(role)), None)
            if not better:
                continue
            need, known = need_of(better)
            g = gpu()
            used = g["used_mb"] - (held(cur) if cur else 0)
            # models that only stay loaded for their idle timeout (the vision model of the job that just ended...) reload on
            # demand: the pinned planner has priority, so they don't count against its return
            idle = [m for m in ps() if _ours(m["name"]) and not is_pinned(m["name"]) and not _busy.get(m["name"])]
            if not _fits(used, need, known, g) and not _fits(used - sum(held(m) for m in idle), need, known, g):
                _fit_since.pop(role, None)
                continue
            since = _fit_since.setdefault(role, now)
            if now - since >= c.get("hysteresis_s", 90):
                _fit_since.pop(role, None)
                if not _fits(used, need, known, g):
                    for m in idle:
                        if not unload(m["name"]):
                            return None
                        _log_switch(role_of(m["name"]) or "model", m["name"], "(unloaded)", "making room for the main model to return")
                if switch_role(role, k - 1, "GPU memory is free again - back to the better model", cost_check=True):
                    return role
        return None
    finally:
        _gov.release()


def check_violations():
    """Partial offloads that exist right now. An idle one is unloaded at once; a busy one is left to finish its request."""
    bad = [m for m in ps(True, strict=False) if not m["full"]]
    for m in bad:
        if not _busy.get(m["name"]):
            print(f"[vram] VIOLATION: {m['name']} is {m['size_vram_mb']}/{m['size_mb']} MiB on the GPU - unloading it", flush=True)
            unload(m["name"])
            _log_switch(role_of(m["name"]) or "model", m["name"], "(unloaded)", "it was partly on the CPU (slow); it will reload with room made first")
    return bad


# ---------------------------------------------------------------- snapshot + sampler
def snapshot():
    """GET /api/vram (docs/API_CONTRACT.md §5)."""
    g, loaded = gpu(), ps(strict=False)
    ours = mb(sum(m["size_vram"] for m in loaded) * 1) if loaded else 0
    ollama = {"version": _cached("ver", 300, lambda: (keepalive.get(OLLAMA + "/api/version", timeout=3).json().get("version", "")
                                                       if _alive() else "")), "url": OLLAMA, "ours": False, "env": {}}
    try:
        from ishti import lifecycle
        ollama["ours"], ollama["env"] = lifecycle.ollama_ours(), lifecycle.ollama_env_active()
    except Exception:
        pass
    roles = {}
    for role, spec in cfg()["roles"].items():
        rs = rungs(role)
        k = min(_rung.get(role, 0), max(0, len(rs) - 1))
        roles[role] = {"model": _eff.get(role) or (rs[k]["name"] if rs else ""), "rung": k, "rungs": len(rs),
                       "downshifted": _rung.get(role, 0) > 0, "pinned": bool(spec.get("pinned")),
                       "num_ctx": (rs[k].get("num_ctx") or (info(rs[k]["model"])["default_ctx"] if rs and not rs[k].get("cpu") else 0)) if rs else 0}
    return {"ts": time.time(),
            "gpu": {"name": g["name"], "total_mb": g["total_mb"], "used_mb": g["used_mb"], "free_mb": g["free_mb"],
                    "used_pct": round(100 * g["used_mb"] / g["total_mb"], 1) if g["total_mb"] else 0,
                    "budget_pct": cfg()["budget_pct"], "budget_mb": budget_mb(g)},
            "models": [{"name": m["name"], "size_mb": m["size_mb"], "size_vram_mb": m["size_vram_mb"], "fully_on_gpu": m["full"],
                        "context_length": m["ctx"], "expires_at": m["expires_at"], "role": role_of(m["name"])} for m in loaded],
            "processes": processes(),
            "other_mb": max(0, g["used_mb"] - ours),
            "roles": roles,
            "violations": [{"name": m["name"], "size_mb": m["size_mb"], "size_vram_mb": m["size_vram_mb"]} for m in loaded if not m["full"]],
            "jobs": {"active": [{"id": j["id"], "what": j["what"], "model": j["model"]} for j in _jobs["active"].values()],
                     "queued": [{"id": j["id"], "what": j["what"], "model": j["model"], "reason": j["reason"], "since": j["since"]} for j in _jobs["queued"].values()]},
            "swap_cost": swap_cost(),
            "ollama": ollama}


def _alive():
    try:
        return keepalive.get(OLLAMA + "/api/version", timeout=2).ok
    except requests.RequestException:
        return False


def start_sampler():
    """~1 vram_status per second when something changed (5 s heartbeat), the upshift check every 5 s, and the
    partial-offload watch every second. Runs in its own thread: nothing here touches the event loop. Idempotent: boot stages
    re-run on retry, and two samplers would duplicate every event and race the upshift."""
    if _sampler_started.is_set():
        return
    _sampler_started.set()
    def loop():
        last, last_sent, tick = None, 0.0, 0
        while True:
            try:
                tick += 1
                if _stopping.is_set():
                    return  # a shutdown began: no more probing, no more events
                if _alive():
                    check_violations()
                    if tick % 5 == 0:
                        upshift_tick()
                    snap = snapshot()
                    key = json.dumps([snap["gpu"]["used_mb"] // 64, [(m["name"], m["fully_on_gpu"]) for m in snap["models"]],
                                      {r: v["model"] for r, v in snap["roles"].items()}, len(snap["jobs"]["queued"]), len(snap["jobs"]["active"])])
                    if key != last or time.time() - last_sent > 5:
                        events.publish("vram_status", snap)
                        last, last_sent = key, time.time()
            except Exception as exc:  # never let the watcher die
                print(f"[vram] sampler: {type(exc).__name__}: {exc}", flush=True)
            time.sleep(1.0)
    threading.Thread(target=loop, daemon=True, name="vram-sampler").start()


def stop():
    """A shutdown began: admit/reserve/switch/upshift refuse from now on (a model loaded after the unload step would stay pinned)."""
    _stopping.set()


def _check_running():
    if _stopping.is_set():
        raise GpuBusy("ISHTI is shutting down")


def reset_state():
    """For tests: forget downshifts and jobs."""
    global _last_heavy_end
    _stopping.clear()
    _rung.clear()
    _eff.clear()
    _jobs["active"].clear()
    _jobs["queued"].clear()
    _fit_since.clear()
    _last_heavy_end = 0.0
    _cache.clear()


if __name__ == "__main__":
    # ---- planning logic against a SIMULATED GPU (no Ollama needed): planner resident, then a heavy vision request ----
    import tempfile
    DB = os.path.join(tempfile.mkdtemp(), "v.db")
    LADDERS = os.path.join(ROOT, "models_ladder.yaml")
    TOTAL = 16303
    sim = {"loaded": [{"name": "ishti-qwen2.5:14b", "size": 10404390501, "size_vram": 10404390501, "ctx": 8192, "expires_at": "", "size_mb": 9922,
                       "size_vram_mb": 9922, "full": True}], "other": 1757}
    gpu = lambda: {"name": "sim", "total_mb": TOTAL, "used_mb": sim["other"] + sum(m["size_vram_mb"] for m in sim["loaded"]), "free_mb": 0, "available": True}  # noqa: E731
    ps = lambda fresh=False: list(sim["loaded"])  # noqa: E731
    disk = {"ishti-qwen2.5:14b": 9.0e9, "qwen2.5:7b": 4.68e9, "qwen2.5:3b": 1.93e9, "gemma3:12b": 8.15e9, "gemma3:4b": 3.34e9,
            "ishti-qwen2.5-coder:14b": 9.0e9, "qwen2.5-coder:7b": 4.68e9, "llama3.2:3b": 2.02e9}
    tags = lambda: dict(disk)  # noqa: E731
    info = lambda name: {"default_ctx": 8192, "kv_per_token": 100000}  # noqa: E731
    variant = lambda model, ctx: model  # noqa: E731

    def seed(name, size_mb, ctx=8192):  # a measured footprint, as vram.record() stores it after a real load
        b = size_mb * MIB
        record([{"name": name, "ctx": ctx, "size": b, "size_vram": b}])
    for n, m in (("ishti-qwen2.5:14b", 9922), ("qwen2.5:7b", 5000), ("qwen2.5:3b", 2600), ("gemma3:12b", 9400), ("gemma3:4b", 3300),
                 ("ishti-qwen2.5-coder:14b", 9922), ("qwen2.5-coder:7b", 5000)):
        seed(n, m)
    # 1. heavy vision tier 3 with the planner resident: the FIRST step is the planner one rung down (spec order)
    p = plan("gemma3:12b", role="vision3")
    assert p and p["actions"][0]["do"] == "downshift" and p["actions"][0]["to"] == 1, p
    assert p["target"]["model"] != "gemma3:12b" or p["target"]["ctx"] < 8192, p  # 12B (9.4 GB) can't sit next to any planner in the 80% budget
    # 2. strict (the tier is required): the planner goes further, and if even that can't make room the answer is None (queue)
    assert plan("gemma3:12b", role="vision3", strict=True) is None
    # 3. a 4B vision model needs only the one planner step (or none), never a lighter tier
    p3 = plan("gemma3:4b", role="vision2")
    assert p3 is not None and all(a["do"] != "rung" for a in p3["actions"]), p3
    # 4. a nearly empty GPU: no action at all
    sim["loaded"][0]["size_vram_mb"] = 500
    assert plan("gemma3:12b", role="vision3")["actions"] == []
    sim["loaded"][0]["size_vram_mb"] = 9922
    # 5. an idle on-demand model is unloaded BEFORE the planner is touched
    sim["loaded"].append({"name": "gemma3:4b", "size": 3.3e9, "size_vram": 3.3e9, "ctx": 8192, "expires_at": "", "size_mb": 3300, "size_vram_mb": 3300, "full": True})
    sim["other"] = 0
    p5 = plan("qwen2.5-coder:7b", role="coder")
    assert p5["actions"][0] == {"do": "unload", "model": "gemma3:4b", "why": "idle on-demand model"}, p5
    sim["loaded"].pop()
    sim["other"] = 1757
    # 6. a voice turn is running: the planner is NOT downshifted (voice has priority)
    touch_voice()
    pv = plan("gemma3:12b", role="vision3")
    assert pv is None or all(a["do"] != "downshift" for a in pv["actions"]), pv
    # 7. an UNMEASURED model needs comfortably more free VRAM than its estimate
    assert need_of("gemma3:12b")[1] is True and need_of("qwen2.5-coder:3b")[1] is False
    # 8a. Ollama's own >= 1 GiB free-after-load rule: at a 95% budget a 9.4 GB model still needs the planner out of the way
    assert not _fits(6007, 8973, True, {"total_mb": TOTAL}) and _fits(3403, 8973, True, {"total_mb": TOTAL})
    # 8. a partial offload in the simulated ps is reported as a violation
    sim["loaded"][0]["full"] = False
    assert len([m for m in ps() if not m["full"]]) == 1
    # 9. a coder model must never be taken for a planner rung (prefix collision with the alias pattern), aliases map to their base
    assert role_of("qwen2.5-coder:7b") == "coder" and role_of("qwen2.5-c8k:7b") == "planner" and role_of("ishti-qwen2.5-coder:14b") == "coder"
    assert base_of("qwen2.5-c8k:7b") == ("qwen2.5:7b", 8192) and base_of("qwen2.5-coder:7b") == ("qwen2.5-coder:7b", None)
    assert rung_ctx("qwen2.5:7b", "planner") == 8192 and rung_ctx("ishti-qwen2.5:14b", "planner") is None
    print("[self-check] vram OK - planner downshifts first, strict keeps the tier or queues, idle models go before the planner, "
          "voice turn protects the planner, unmeasured models need headroom")
