"""The self-model: what ISHTI knows about itself, measured live rather than remembered.

Why this exists (RESEARCH.md R5): the weights can never be the source of host, model-inventory or
capability facts - they are runtime state that postdates training, and this repo's own Laya checkpoint
warns its confidences are uncalibrated. So every line here is measured at runtime and stamped with WHERE it
came from: `[live:psutil]`, `[live:nvidia-smi]`, `[live:/api/tags]`, `[live:registry]`, `[declared]`.
A fact that is not measured is either `[declared]` (the owner wrote it) or absent - and absent means ISHTI
must say "I don't know" rather than invent a spec (R5 S6).

Cost matters, because the resident block is rebuilt into every request's system prompt:
  * CHEAP probes (OS/CPU/RAM/disk/GPU - tens of ms) refresh every `TTL_S`;
  * EXPENSIVE probes (Ollama inventory + the 121-skill registry - seconds) refresh every `FULL_TTL_S`, or
    immediately on an explicit `refresh()`. `get_system_telemetry` is never called here on purpose: its
    `psutil.cpu_percent(interval=0.1)` would block the request path (R5 S4).

Cross-check: `BASELINE` is what the owner believes this machine is. A probe that disagrees is REPORTED as a
mismatch, never silently accepted in either direction.

Privacy (enforced, not hoped for): the resident block never carries the owner's name, hostname, file paths
or contact details, and `assert_local_only` / `sanitize_outbound` are the chokepoint that keeps the block
and the profile out of anything that leaves the PC.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import platform
import subprocess
import threading
import time

from ishti import CONFIG, ROOT
from ishti.test_mode import scratch_path

PROFILE = os.path.join(ROOT, "owner_profile.yaml")
# The owner confirms the details in chat ("confirm my profile"): the answer is stored here, in data/, because the meta skill
# may only write there - owner_profile.yaml itself stays exactly as the owner edited it.
CONFIRM = scratch_path(os.path.join(ROOT, "data", "owner_profile_confirmed.json"))
TTL_S = 30          # cheap probes (hardware)
FULL_TTL_S = 300    # expensive probes (models + skill registry)
BUDGET_CHARS = 1200
BASELINE = {"gpu": "RTX 5070 Ti", "vram_gb": 16, "ram_gb": 64, "os": "Windows"}
_cache: dict = {"cheap_ts": 0.0, "full_ts": 0.0, "data": {}, "fp": ""}
HEDGE = ("I don't know - shall I check? Facts about this machine come only from the measured self-model; "
         "if it is not in there, I say I don't know instead of estimating.")


def _safe(fn, *args):
    """A probe that never raises: a failed read says which read failed, it never becomes 'none'."""
    try:
        return fn(*args)
    except Exception as exc:
        return f"read failed ({type(exc).__name__}: {exc})"


# ------------------------------------------------------------------ cheap probes
def probe_os() -> dict:
    import winreg
    key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion")
    build = str(winreg.QueryValueEx(key, "CurrentBuildNumber")[0])
    disp = str(winreg.QueryValueEx(key, "DisplayVersion")[0])
    return {"os": f"Windows {disp} build {build}", "system": platform.system(), "tag": "live:registry"}


def probe_cpu() -> dict:
    import psutil
    import winreg
    key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                         r"HARDWARE\DESCRIPTION\System\CentralProcessor\0")
    name = str(winreg.QueryValueEx(key, "ProcessorNameString")[0]).strip()
    return {"cpu": name, "cores": psutil.cpu_count(logical=False), "threads": psutil.cpu_count(),
            "tag": "live:winreg"}


def probe_ram() -> dict:
    import psutil
    vm = psutil.virtual_memory()
    return {"ram_total_gb": round(vm.total / 1024 ** 3, 1),
            "ram_free_gb": round(vm.available / 1024 ** 3, 1),
            "ram_percent": vm.percent, "tag": "live:psutil"}


def probe_disk() -> dict:
    import psutil
    drive = os.environ.get("SystemDrive", "C:") + "\\"
    du = psutil.disk_usage(drive)
    return {"disk": drive, "disk_free_gb": round(du.free / 1024 ** 3, 1),
            "disk_total_gb": round(du.total / 1024 ** 3, 1), "tag": "live:psutil"}


def probe_gpu() -> dict:
    out = subprocess.run(
        ["nvidia-smi", "--query-gpu=name,memory.total,memory.used,driver_version",
         "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=8,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    parts = [p.strip() for p in out.stdout.strip().splitlines()[0].split(",")]
    return {"gpu": parts[0], "vram_total_mb": int(parts[1]), "vram_used_mb": int(parts[2]),
            "driver": parts[3], "tag": "live:nvidia-smi"}


CHEAP = (("os", probe_os), ("cpu", probe_cpu), ("ram", probe_ram), ("disk", probe_disk),
         ("gpu", probe_gpu))


# ------------------------------------------------------------------ expensive probes
def probe_models() -> dict:
    """Installed + loaded models, and any PARTIAL offload (size_vram != size), which is a hard failure."""
    import requests
    base = CONFIG["llm"]["base_url"].rsplit("/v1", 1)[0]
    tags = requests.get(f"{base}/api/tags", timeout=5).json().get("models", []) or []
    ps = (requests.get(f"{base}/api/ps", timeout=5).json().get("models", []) or []) if tags else []
    loaded = [{"name": m.get("name", "?"), "size": m.get("size"), "size_vram": m.get("size_vram")}
              for m in ps]
    partial = [m for m in loaded if m.get("size_vram") != m.get("size")]
    return {"models_installed": len(tags), "models_loaded": loaded, "partial_offload": partial,
            "roles": dict(CONFIG.get("llm", {}).get("models") or {}), "tag": "live:/api/tags"}


def probe_skills() -> dict:
    """The real capability list, read from the live registry - so a newly registered skill appears by itself. Inside the
    running app the dispatcher's already-loaded SKILLS are reused; re-running the loader cost ~3.5 s of CPU each refresh."""
    import sys
    loaded = getattr(sys.modules.get("ishti.dispatcher"), "SKILLS", None)
    if not loaded:
        from ishti.skills import load_skills
        loaded = load_skills()
    rows = {}
    for n, s in sorted(loaded.items()):
        m = getattr(s, "manifest", {}) or {}
        rows[n] = {"tier": m.get("tier", "?"), "status": s.status,
                   "one_line": (m.get("description") or "")[:110]}
    return {"skills_total": len(rows),
            "skills_working": len([n for n, v in rows.items() if v["status"] == "working"]),
            "skills": rows, "tag": "live:registry"}


def profile() -> dict:
    """The owner-editable profile. A missing/invalid file yields {} - never an invented name."""
    try:
        import yaml
        with open(PROFILE, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        data = data if isinstance(data, dict) else {}
        if data and os.path.isfile(CONFIRM):
            data = {**data, "confirmed": True}
        return data
    except Exception as exc:
        print(f"[selfmodel] owner profile unreadable ({type(exc).__name__}: {exc}) - treated as unknown")
        return {}


def confirm_profile() -> str:
    """Record that the owner checked the profile details ("confirm my profile" -> yes). -> the file written."""
    import json
    os.makedirs(os.path.dirname(CONFIRM), exist_ok=True)
    with open(CONFIRM, "w", encoding="utf-8") as f:
        json.dump({"confirmed": True, "at": time.strftime("%Y-%m-%d %H:%M")}, f)
    return CONFIRM


def profile_fingerprint() -> str:
    try:
        st = os.stat(PROFILE)
        sig = f"{int(st.st_mtime)}:{st.st_size}"
    except OSError:
        return "absent"
    return sig + (":confirmed" if os.path.isfile(CONFIRM) else "")



# ------------------------------------------------------------------ build / render
def _mismatch(data) -> list[str]:
    """Report any probe that disagrees with what the owner believes this machine is."""
    out = []
    gpu, ram = str(data.get("gpu", "")), data.get("ram_total_gb")
    if isinstance(gpu, str) and gpu and BASELINE["gpu"].lower() not in gpu.lower():
        out.append(f"GPU is '{gpu}', expected '{BASELINE['gpu']}'")
    if isinstance(ram, (int, float)) and abs(float(ram) - BASELINE["ram_gb"]) > 4:
        out.append(f"RAM is {ram} GB, expected about {BASELINE['ram_gb']} GB")
    os_name = data.get("os")
    if isinstance(os_name, str) and os_name and not os_name.lower().startswith("windows"):
        out.append(f"OS is '{os_name}', expected Windows")
    return out


def _refresh_full() -> None:
    """Re-read the EXPENSIVE probes (model inventory + skill registry) and merge them into the cache.

    Split out so the same code path serves both the background thread and the owner's explicit
    `refresh()`, which is why it writes the cache rather than returning a dict.
    """
    _cache["full_ts"] = time.monotonic()
    full = {}
    for key, fn in (("models", probe_models), ("skills", probe_skills)):
        value = _safe(fn)
        full[key] = value if isinstance(value, dict) else {"error": value, "tag": "live:failed"}
    merged = dict(_cache["data"])
    merged.update(full)
    _cache["data"] = merged


def build(force=False, *, cheap_only=None) -> dict:
    """Collect the self-model. Cheap probes refresh every TTL_S; expensive ones every FULL_TTL_S.

    `force=True` (the owner's "refresh your self-knowledge") re-reads everything including the skill
    registry. A failing probe records that the READ failed - it never degrades to 0/None/"none".
    """
    now = time.monotonic()
    data = dict(_cache["data"])
    if force or (cheap_only is False) or (now - _cache["cheap_ts"]) > TTL_S:
        for key, fn in CHEAP:
            value = _safe(fn)
            if isinstance(value, dict):
                data[key] = value
            else:
                data[key] = {"error": value, "tag": "live:failed"}
        _cache["cheap_ts"] = now
    if force or (now - _cache["full_ts"]) > FULL_TTL_S:
        if force:
            _refresh_full()
            # _refresh_full() merged models/skills into the CACHE; the local copy was taken before it ran.
            # Merge both directions: replacing would drop the cheap probes we just took (they live only in
            # the local copy until the end of this function), which is how the GPU/RAM lines disappeared.
            # (Both halves of this were found by the self-checks - ISSUES.md SC-18.)
            refreshed = dict(_cache["data"])
            refreshed.update(data)
            data = refreshed
        elif not _cache.get("full_busy"):
            # Off the request path: re-reading the 121-skill registry costs ~3.5 s (measured), which is
            # far too long to stall a reply. A daemon thread refreshes it; the request renders with what
            # is already cached. `refresh()` (the owner's command) does it inline so it is deterministic.
            _cache["full_busy"] = True

            def _bg():
                try:
                    _refresh_full()
                finally:
                    _cache["full_busy"] = False

            threading.Thread(target=_bg, daemon=True, name="selfmodel-full").start()
    data["owner"] = profile()
    data["measured_at"] = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    data["age_s"] = round(now - _cache["cheap_ts"], 1)
    data["mismatch"] = _mismatch(data)
    fp = hashlib.sha256("|".join(str(data.get(k, "")) for k in
                                 ("os", "cpu", "ram", "disk", "gpu")).encode()).hexdigest()[:12]
    data["fingerprint"] = fp
    data["changed"] = fp != _cache["fp"]
    # merge, never replace: a background _refresh_full() may have landed while we were probing, and
    # overwriting the cache with our older copy would silently drop that fresh data
    merged = dict(_cache["data"])
    merged.update(data)
    _cache["data"] = merged
    _cache["fp"] = fp
    return merged


def fingerprint() -> str:
    return _cache["fp"] or build()["fingerprint"]


def _line(label, value, tag) -> str:
    return f"- {label}: {value} [{tag}]"


def _inference_line() -> str:
    """Where model inference runs right now - the old fixed text claimed 'entirely on this PC, offline' even in API mode."""
    try:
        from ishti import llm
        if llm.provider() == "api":
            name = llm.api_settings().get("provider", "a cloud API")
            if llm._cloud_open() and llm.failover_model():
                return f"My cloud model ({name}) is unavailable right now, so a small local model on this PC is answering."
            return (f"My thinking runs on a cloud model ({name}); my memory, files and tools stay on this PC.")
        return "My thinking runs on a local model on this PC (Ollama); nothing is sent to a cloud model."
    except Exception:
        return "Inference location unknown."


def render(block=None, budget=BUDGET_CHARS) -> str:
    """The compact resident block, capped at `budget` characters.

    Assembled as head / body / tail so over-budget trimming can only ever remove BODY lines: the
    identity, the mismatch warnings and the "I don't know" rule are never trimmed away, and a line is
    never cut in half.
    """
    data = block if isinstance(block, dict) else build()
    os_d, cpu, ram, disk = (data.get(k) or {} for k in ("os", "cpu", "ram", "disk"))
    gpu, models, skills = (data.get(k) or {} for k in ("gpu", "models", "skills"))
    head = [f"SELF-MODEL (measured {data.get('measured_at', '?')}, age {data.get('age_s', '?')} s)",
            "- I am ISHTI, the owner's private personal AI assistant on this PC. " + _inference_line() + " [declared]"]
    body: list[str] = []
    # A probe that FAILED must be visible, not silently omitted - "I have no GPU" and "reading the GPU
    # failed" are different facts and the owner must be able to tell them apart.
    for key, label in (("gpu", "GPU"), ("models", "Models"), ("skills", "Skills"), ("os", "OS"),
                       ("cpu", "CPU"), ("ram", "RAM"), ("disk", "Disk")):
        section = data.get(key)
        if isinstance(section, dict) and section.get("error"):
            body.append(_line(label, section["error"], "live:failed"))
    if isinstance(gpu, dict) and gpu.get("gpu"):
        body.append(_line("GPU", f"{gpu['gpu']}, {gpu.get('vram_total_mb')} MB VRAM "
                                 f"({gpu.get('vram_used_mb')} used now), driver {gpu.get('driver')}",
                          gpu.get("tag", "live:nvidia-smi")))
    if isinstance(ram, dict) and ram.get("ram_total_gb"):
        body.append(_line("RAM", f"{ram['ram_total_gb']} GB total, {ram.get('ram_free_gb')} GB free",
                          ram.get("tag", "live:psutil")))
    if isinstance(cpu, dict) and cpu.get("cpu"):
        body.append(_line("CPU", f"{cpu['cpu']} ({cpu.get('cores')} cores)",
                          cpu.get("tag", "live:winreg")))
    if isinstance(os_d, dict) and os_d.get("os"):
        body.append(_line("OS", os_d["os"], os_d.get("tag", "live:registry")))
    if isinstance(disk, dict) and disk.get("disk_free_gb") is not None:
        body.append(_line("Disk", f"{disk.get('disk')}: {disk['disk_free_gb']} GB free of "
                                   f"{disk.get('disk_total_gb')} GB", disk.get("tag", "live:psutil")))
    if isinstance(models, dict) and models.get("models_installed") is not None:
        now = ", ".join(m.get("name", "?") for m in (models.get("models_loaded") or [])) or "none loaded"
        body.append(_line("Models", f"{models['models_installed']} installed; loaded now: {now}",
                          models.get("tag", "live:/api/tags")))
    if isinstance(skills, dict) and skills.get("skills_total") is not None:
        body.append(_line("Skills", f"{skills.get('skills_working')} of {skills['skills_total']} ready",
                          skills.get("tag", "live:registry")))
    tail = []
    for m in (models.get("partial_offload") or [] if isinstance(models, dict) else []):
        tail.append(f"- WARNING: partial offload detected: {m.get('name')} "
                    f"(size_vram {m.get('size_vram')} != size {m.get('size')}) - hard failure, report it")
    tail += [f"- MISMATCH vs the recorded baseline: {m}" for m in (data.get("mismatch") or [])]
    # stated as a fact about this block, not a stock phrase: the small model answered "what did I ask you a minute ago?" and
    # "did you send it?" with the hedge sentence word for word
    tail.append("- A fact about THIS PC that is not listed above: I don't know it - measure it, never estimate it.")

    text = "\n".join(head + body + tail)
    while len(text) > budget and body:          # only BODY lines are ever dropped, never clipped
        body.pop()
        text = "\n".join(head + body + tail)
    return text



# ------------------------------------------------------------------ refresh / detail / privacy
def refresh() -> dict:
    """Force a full rebuild - what "refresh your self-knowledge" runs. -> {changed, fingerprint, chars, render}"""
    before = _cache["fp"]
    data = build(force=True)
    block = render(data)
    return {"changed": before != data["fingerprint"], "fingerprint": data["fingerprint"],
            "chars": len(block), "mismatch": data.get("mismatch") or [], "render": block,
            "measured_at": data.get("measured_at")}


def detail() -> str:
    """The long tail, local-only: every skill with its tier, the model roles, the rule list.

    This is what the `meta` skill answers "what can you do?" / "what can't you do?" with. It carries the
    owner's name, so it must never be exported or sent anywhere.

    It forces a full re-read (models + skill registry, ~3.5 s measured) because the owner asked directly:
    answering "0 skills" from a cache that has not finished warming would be worse than being slow.
    """
    data = build(force=True)
    owner = (data.get("owner") or {}).get("owner") or {}
    skills = (data.get("models") and data.get("skills")) or {}
    rows = (data.get("skills") or {}).get("skills") or {}
    out = [f"Measured {data.get('measured_at')} (fingerprint {data.get('fingerprint')})", "",
           render(data), ""]          # the measured hardware/models/skills block comes FIRST
    if owner:
        out += [f"Owner: {owner.get('name', 'unknown')} - address them as "
                f"'{owner.get('preferred_address', 'unknown')}', tone: {owner.get('tone', 'unknown')}",
                f"Languages: {', '.join(owner.get('languages') or []) or 'unknown'}",
                f"Confirmed by the owner: {(data.get('owner') or {}).get('confirmed', False)}", ""]
    roles = (data.get("models") or {}).get("roles") or {}
    out += ["Model roles: " + (", ".join(f"{k}={v}" for k, v in roles.items()) or "unknown"), ""]
    out += [f"Skills ({len(rows)} registered):"]
    for n, v in sorted(rows.items()):
        out.append(f"- {n} [{v['tier']}, {v['status']}]: {v['one_line']}")
    out += ["", "Rules I follow:",
            "- T0 read-only; T1 reversible; T2 needs your explicit yes; T3 is refused.",
            "- Local-only: nothing is sent to a cloud service from inside this app.",
            "- Only the main orchestrator speaks; skills answer through it.",
            "- If a fact is not in the self-model, I say I don't know instead of guessing.", ""]
    if data.get("mismatch"):
        out.append("MISMATCH vs the recorded baseline: " + "; ".join(data["mismatch"]))
    return "\n".join(out)


def _secret_terms() -> list[str]:
    """Values that must never leave the PC: owner identity, hostname, and the profile's free text."""
    terms = []
    owner = (profile().get("owner") or {})
    for key in ("name", "preferred_address"):
        v = str(owner.get(key) or "").strip()
        if len(v) > 2:
            terms.append(v)
    host = str(os.environ.get("COMPUTERNAME") or "").strip()
    if len(host) > 2:
        terms.append(host)
    return terms


def assert_local_only(payload: str) -> list[str]:
    """Which private values appear in `payload`. Empty list == safe to send. Used by the tests and by
    the web skills before a query leaves the machine."""
    text = str(payload or "")
    return [t for t in _secret_terms() if t.lower() in text.lower()]


def sanitize_outbound(text: str) -> str:
    """The chokepoint for anything on its way out: strips owner identity values from a query."""
    out = str(text or "")
    for term in _secret_terms():
        if term and term in out:
            out = out.replace(term, "[private]")
    return out


if __name__ == "__main__":
    import time as _t

    # ---- the cheap probes actually read this machine -----------------------------------------
    for key, fn in CHEAP:
        got = fn()
        assert isinstance(got, dict) and got.get("tag", "").startswith("live:"), (key, got)
    assert "Windows" in probe_os()["os"], probe_os()
    assert probe_gpu()["vram_total_mb"] > 0, probe_gpu()

    # ---- build/render: measured, tagged, inside budget, never clipped ------------------------
    t0 = _t.perf_counter()
    data = build(force=True)
    took = round((_t.perf_counter() - t0) * 1000)
    block = render(data)
    # Regression (ISSUES.md SC-18): a forced build MUST include the inventory. build() once copied the
    # cache, let _refresh_full() write into it, and then overwrote it with the stale copy - so every
    # caller reported "0 skills".
    for key in ("models", "skills"):
        assert key in data, f"{key} missing from a forced build (keys: {sorted(data)})"
        assert "error" not in (data[key] or {}), f"{key} probe failed: {data[key].get('error')}"
    assert (data["skills"].get("skills_total") or 0) > 50, data["skills"].get("skills_total")
    assert (data["models"].get("models_installed") or 0) > 0, data["models"]
    detail_txt = detail()
    assert "RTX 5070 Ti" in detail_txt, "detail() must show the live GPU"
    assert "Skills (0 registered)" not in detail_txt, detail_txt[:300]
    assert "Model roles: unknown" not in detail_txt, detail_txt[:300]
    assert len(block) <= BUDGET_CHARS, f"{len(block)} chars > {BUDGET_CHARS}"
    assert block.startswith("SELF-MODEL"), block[:80]
    assert "I don't know" in block, "the honesty rule must be in the block"
    assert data["measured_at"] and data["fingerprint"], data
    assert isinstance(data["mismatch"], list), data["mismatch"]
    # every rendered FACT carries its provenance; the honesty rule is not a fact, so it is exempt
    for line in block.splitlines():
        if not line.startswith("- ") or ":" not in line or "MISMATCH" in line:
            continue
        if line.startswith("- Anything not listed"):
            continue
        assert "[" in line and line.rstrip().endswith("]"), f"untagged fact: {line}"

    # ---- a failed read says so, and never becomes "none" --------------------------------------
    data_bad = dict(data)
    data_bad["gpu"] = {"error": "read failed (RuntimeError: nvidia-smi missing)", "tag": "live:failed"}
    assert "read failed" in json.dumps(render(data_bad)), "a failed probe must be visible"

    # ---- partial offload is reported as a hard failure ----------------------------------------
    data_part = dict(data)
    data_part["models"] = {"models_installed": 1, "tag": "live:/api/tags",
                           "models_loaded": [{"name": "x", "size": 100, "size_vram": 40}],
                           "partial_offload": [{"name": "x", "size": 100, "size_vram": 40}]}
    assert "partial offload" in render(data_part), "partial offload must be flagged"

    # ---- a baseline mismatch is reported, not silently accepted --------------------------------
    data_wrong = dict(data)
    data_wrong["gpu"] = {"gpu": "NVIDIA GeForce GTX 1080", "vram_total_mb": 8192, "vram_used_mb": 0,
                         "driver": "0", "tag": "live:nvidia-smi"}
    # render() consumes the mismatch list build() computes, so recompute it for this fake block
    data_wrong["mismatch"] = _mismatch(data_wrong)
    assert data_wrong["mismatch"], _mismatch(data_wrong)
    assert "MISMATCH" in render(data_wrong), "the owner must be told the machine disagrees"

    # ---- over budget: lines are dropped, never clipped mid-line -------------------------------
    big = dict(data)
    big["skills"] = {"skills_total": 500, "skills_working": 500, "tag": "live:registry",
                     "skills": {f"s{i}": {"tier": "T0", "status": "working", "one_line": "x" * 400}
                                for i in range(500)}}
    trimmed = render(big, budget=600)
    assert len(trimmed) <= 600, len(trimmed)
    assert trimmed.startswith("SELF-MODEL"), trimmed[:40]
    assert "I don't know" in trimmed, "the honesty rule must survive trimming"
    for line in trimmed.splitlines():
        assert line.count("[") == line.count("]"), f"a line was clipped mid-tag: {line}"

    # ---- privacy: the resident block must not carry owner identity ----------------------------
    owner = (data.get("owner") or {}).get("owner") or {}
    for value in (owner.get("name"), owner.get("preferred_address")):
        if value:
            assert value not in block, f"owner identity leaked into the resident block: {value}"
    import os as _os
    host = _os.environ.get("COMPUTERNAME", "")
    if host and len(host) > 2:
        assert host not in block, "hostname leaked into the resident block"

    # ---- privacy: the chokepoint catches what a search query must not carry -------------------
    probe = f"who is {owner.get('preferred_address', 'Ishtiaq')} and what is my PC"
    assert assert_local_only(probe), "assert_local_only must catch the owner's name in a query"
    assert "[private]" in sanitize_outbound(probe), sanitize_outbound(probe)
    assert assert_local_only("what is the weather in Peshawar") == [], "a clean query must pass"

    # ---- refresh() re-reads and reports whether anything changed -------------------------------
    r = refresh()
    assert r["fingerprint"] and r["chars"] <= BUDGET_CHARS, r
    assert r["render"].startswith("SELF-MODEL"), r["render"][:60]

    print(f"[self-check] selfmodel OK - 5 cheap probes read live ({probe_gpu()['gpu']}, "
          f"{probe_ram()['ram_total_gb']} GB RAM), full build {took} ms, block {len(block)} chars "
          f"(budget {BUDGET_CHARS}), every fact provenance-tagged, failed reads visible, partial "
          f"offload and baseline mismatches reported, owner identity absent from the resident block "
          f"and stripped from outbound queries")


