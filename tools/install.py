"""ISHTI installer. Double-click install.bat (it runs this file); or run:

    python tools\\install.py            full, safe-to-repeat install
    python tools\\install.py --check    read-only report of what is present / missing (exit 0 = all good, 1 = not)
    python tools\\install.py --yes      same as a full install, but answers every question with its default

Phase 1 (this file under any Python 3.11): checks the PC, makes venv\\, installs requirements.txt.
Phase 2 (--finish, run by venv\\python): Ollama, the AI models, voice files, desktop icon. It reuses
ishti.manage (downloads) and desktop\\create_shortcut.py instead of re-implementing them.
Standard library only up to the venv, so it runs on a bare Python. Full detail goes to install.log.
"""
import argparse
import ctypes
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import traceback
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENV = os.path.join(ROOT, "venv")
VPY = os.path.join(VENV, "Scripts", "python.exe")
STAMP = os.path.join(VENV, ".ishti_requirements.sha256")
LOG = os.path.join(ROOT, "install.log")
CONFIG = os.path.join(ROOT, "ishti", "config.json")
LADDER = os.path.join(ROOT, "models_ladder.yaml")
REQS = os.path.join(ROOT, "requirements.txt")
DASHBOARD = os.path.join(ROOT, "interface", "app", "dist", "index.html")
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
MIN_OLLAMA = (0, 34, 2)       # models_ladder.yaml: the planner's architecture needs Ollama >= 0.34.2
MIN_FREE_GB = 25              # README: ~25 GB free (packages ~8 GB + models ~12 GB + room to breathe)
WHISPER_GB = 1.6              # tests/download_whisper_turbo.py: model.bin is about 1.6 GB
YES = False
TOTAL_STEPS = 8

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


# ----------------------------------------------------------------------------- small helpers
def log(msg):
    if not LOG:      # --check writes nothing
        return
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")
    except OSError:
        pass


def say(msg=""):
    print(msg, flush=True)
    log(msg)


def step(n, title):
    say(f"\n[{n}/{TOTAL_STEPS}] {title}")


def ask(question, default=True):
    """Y/N in the console. Enter = the capitalised default."""
    hint = "[Y/n]" if default else "[y/N]"
    if YES or not sys.stdin or not sys.stdin.isatty():
        say(f"{question} {hint} -> {'yes' if default else 'no'}")
        return default
    while True:
        a = input(f"{question} {hint} ").strip().lower()
        log(f"asked: {question} -> {a!r}")
        if not a:
            return default
        if a[0] in "yj":
            return True
        if a[0] == "n":
            return False


def wait_key(msg):
    if not YES and sys.stdin and sys.stdin.isatty():
        input(msg)


def gb(n):
    return f"{n / 1e9:.1f} GB" if n >= 1e8 else f"{n / 1e6:.0f} MB"


def config():
    with open(CONFIG, encoding="utf-8") as f:
        return json.load(f)


def run_logged(cmd, **kw):
    """Run a command, full output into install.log. -> (exit code, text)."""
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", creationflags=NO_WINDOW, **kw)
    except OSError as exc:      # e.g. winget not present
        log(f"$ {cmd[0]} -> {exc}")
        return 127, str(exc)
    log(f"$ {' '.join(map(str, cmd))}\n{p.stdout}{p.stderr}[exit {p.returncode}]")
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def norm(name):
    return re.sub(r"[-_.]+", "_", name).lower()


# ----------------------------------------------------------------------------- facts about this PC
def windows_info():
    v = sys.getwindowsversion()
    return v.build, ("Windows 11" if v.build >= 22000 else "Windows 10")


def free_gb(path=ROOT):
    return shutil.disk_usage(path).free / 1e9


def gpu():
    """-> (name, memory MB) of the first NVIDIA card, or (None, None)."""
    try:
        r = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
                           capture_output=True, text=True, timeout=10, creationflags=NO_WINDOW)
        name, mem = r.stdout.splitlines()[0].rsplit(",", 1)
        return name.strip(), int(mem)
    except Exception:
        return None, None


def webview2_installed():
    try:
        import winreg
        guid = r"SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
        for hive, sub in ((winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node" + guid[8:]), (winreg.HKEY_LOCAL_MACHINE, guid),
                          (winreg.HKEY_CURRENT_USER, guid)):
            try:
                with winreg.OpenKey(hive, sub) as k:
                    if winreg.QueryValueEx(k, "pv")[0] not in ("", "0.0.0.0"):
                        return True
            except OSError:
                pass
    except Exception:
        return True   # cannot tell: do not nag
    return False


def desktop_dir():
    buf = ctypes.create_unicode_buffer(260)
    ctypes.windll.shell32.SHGetFolderPathW(0, 0x10, 0, 0, buf)   # CSIDL_DESKTOPDIRECTORY (follows OneDrive redirects)
    return buf.value or os.path.join(os.path.expanduser("~"), "Desktop")


# ----------------------------------------------------------------------------- requirements.txt vs venv
def requirement_pins():
    """[(name, version)] for every pinned line of requirements.txt."""
    out = []
    for line in open(REQS, encoding="utf-8"):
        m = re.match(r"^([A-Za-z0-9_.\-]+)(?:\[[^\]]*\])?==([^;\s#]+)", line.strip())
        if m:
            out.append((m.group(1), m.group(2)))
    return out


def installed_dists():
    """{normalised name: version} read from venv\\Lib\\site-packages\\*.dist-info (no imports: fast)."""
    sp = os.path.join(VENV, "Lib", "site-packages")
    out = {}
    try:
        for d in os.listdir(sp):
            if d.endswith(".dist-info"):
                n, _, v = d[:-len(".dist-info")].rpartition("-")
                out[norm(n)] = v
    except OSError:
        pass
    return out


def missing_packages():
    have = installed_dists()
    miss = [n for n, _ in requirement_pins() if norm(n) not in have]
    if "chatterbox_tts" not in have:     # installed in its own step (--no-deps), see requirements.txt
        miss.append("chatterbox-tts")
    return miss


def requirements_hash():
    return hashlib.sha256(open(REQS, "rb").read()).hexdigest()


# ----------------------------------------------------------------------------- Ollama and models
def ollama_exe():
    p = shutil.which("ollama")
    if p:
        return p
    for base in (os.environ.get("LOCALAPPDATA"), os.environ.get("ProgramFiles")):
        for sub in (r"Programs\Ollama", "Ollama"):
            c = os.path.join(base or "", sub, "ollama.exe")
            if base and os.path.isfile(c):
                return c
    return None


def ollama_url():
    return config()["llm"]["base_url"].rsplit("/v1", 1)[0]


def ollama_tags(timeout=1.5):
    """Names of the installed models when the Ollama server answers, else None."""
    try:
        with urllib.request.urlopen(ollama_url() + "/api/tags", timeout=timeout) as r:
            return {m["name"] for m in json.load(r)["models"]}
    except Exception:
        return None


def models_on_disk():
    """Installed model names read from Ollama's manifest folder (works while the server is off)."""
    root = os.path.join(os.environ.get("OLLAMA_MODELS") or os.path.join(os.path.expanduser("~"), ".ollama", "models"),
                        "manifests", "registry.ollama.ai")
    out = set()
    for dp, _, files in os.walk(root):
        for f in files:
            parts = os.path.relpath(os.path.join(dp, f), root).split(os.sep)   # ns, model, tag
            if len(parts) == 3:
                out.add((parts[1] if parts[0] == "library" else f"{parts[0]}/{parts[1]}") + ":" + parts[2])
    return out


def installed_models():
    tags = ollama_tags()
    return tags if tags is not None else models_on_disk()


def has_model(name, installed):
    return (name if ":" in name else name + ":latest") in installed


def ollama_version(exe):
    try:
        out = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=15, creationflags=NO_WINDOW).stdout
        return tuple(int(x) for x in re.search(r"(\d+)\.(\d+)\.(\d+)", out).groups())
    except Exception:
        return None


def ladder_planner_rungs():
    """Model names of the planner role in models_ladder.yaml, best first (read at run time, nothing hard-coded)."""
    try:
        text = open(LADDER, encoding="utf-8").read()
    except OSError:
        return []
    m = re.search(r"^  planner:.*?(?=^  \w+:)", text, re.S | re.M)
    return re.findall(r'model:\s*"([^"]+)"', m.group(0)) if m else []


def modelfile_for(name):
    """(path, FROM base) of the ishti\\*.Modelfile whose header says 'ollama create <name>', else (None, name)."""
    d = os.path.join(ROOT, "ishti")
    for f in sorted(os.listdir(d)):
        if f.endswith(".Modelfile"):
            text = open(os.path.join(d, f), encoding="utf-8").read()
            head, base = "\n".join(text.splitlines()[:6]), re.search(r"^FROM\s+(\S+)", text, re.M)
            m = re.search(r"ollama create (\S+)", head)
            if m and base and (m.group(1) == name or m.group(1) == name + ":latest"):
                return os.path.join(d, f), base.group(1)
    return None, name


def registry_size(name):
    """Download size in bytes of an Ollama library model (all layers), None when offline / unknown."""
    ns, _, tag = name.partition(":")
    url = f"https://registry.ollama.ai/v2/{ns if '/' in ns else 'library/' + ns}/manifests/{tag or 'latest'}"
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/vnd.docker.distribution.manifest.v2+json"})
        with urllib.request.urlopen(req, timeout=8) as r:
            d = json.load(r)
        return sum(layer["size"] for layer in d["layers"])
    except Exception:
        return None


def model_plan(installed, vram_mb):
    """[(role, name, modelfile or None, base to pull, size or None, is_installed)] - the minimum first-run set.

    planner = the best rung (config's planner first, then the ladder) that is already installed or fits this GPU
    (the governor keeps everything under 80 % of VRAM); translator and embedder come straight from config.json."""
    c = config()
    planner_names = []
    for n in [c["llm"]["models"]["planner"]] + ladder_planner_rungs():
        if n not in planner_names:
            planner_names.append(n)
    pick = None
    for n in planner_names:
        if has_model(n, installed):
            pick = n
            break
        _, base = modelfile_for(n)
        size = registry_size(base)
        if vram_mb and size and size / 1048576 + 800 <= vram_mb * 0.8:
            pick = n
            break
    pick = pick or planner_names[-1]    # no NVIDIA card or nothing fits: the smallest rung
    wanted = [("planner", pick), ("translator", c["llm"]["models"]["translator"]), ("embedder", c["memory"]["embed_model"])]
    out = []
    for role, n in wanted:
        mf, base = modelfile_for(n)
        have = has_model(n, installed)
        out.append((role, n, mf, base, None if have else registry_size(base), have))
    return out


# ----------------------------------------------------------------------------- --check (read-only, fast)
def check():
    problems = 0

    def row(ok, text, required=True):
        nonlocal problems
        tag = "  OK  " if ok else ("MISSING" if required else "optional")
        problems += (not ok) and required
        say(f"  [{tag:^8}] {text}")

    say("ISHTI check (read-only; nothing is changed)\n")
    build, wname = windows_info()
    row(build >= 19041, f"{wname} (build {build})" + ("" if build >= 19041 else " - Windows 10 or 11 (2004 or newer) is needed"))
    free = free_gb()
    row(free >= 5, f"disk space free on this drive: {free:.0f} GB")
    row(os.path.isfile(DASHBOARD), "dashboard files (interface\\app\\dist)" if os.path.isfile(DASHBOARD) else
        "dashboard files are missing - download the full project again from GitHub (green Code button -> Download ZIP)")
    name, mem = gpu()
    row(bool(name), f"NVIDIA graphics card: {name} ({mem / 1024:.0f} GB)" if name else
        "no NVIDIA graphics card found (ISHTI will be very slow without one)", required=False)
    row(webview2_installed(), "Microsoft Edge WebView2 (draws the window)")
    row(os.path.isfile(VPY), "Python workspace (venv)")
    if os.path.isfile(VPY):
        miss = missing_packages()
        row(not miss, "program parts installed" if not miss else f"{len(miss)} program parts missing, for example: {', '.join(miss[:4])}")
        row(os.path.isfile(STAMP) and open(STAMP).read().strip() == requirements_hash(),
            "program parts match requirements.txt", required=False)
    exe = ollama_exe()
    ver = ollama_version(exe) if exe else None
    row(bool(exe), "Ollama is installed" if exe else "Ollama is not installed (https://ollama.com/download)")
    if exe and ver:
        row(ver >= MIN_OLLAMA, f"Ollama version {'.'.join(map(str, ver))}" + ("" if ver >= MIN_OLLAMA else
            f" is too old, please update (need {'.'.join(map(str, MIN_OLLAMA))} or newer)"))
    row(ollama_tags() is not None, "Ollama is running now (ISHTI starts it by itself when needed)", required=False)
    try:
        for role, n, _, _, _, have in model_plan(installed_models(), mem):
            row(have, f"AI model for {role}: {n}" + ("" if have else " - not downloaded yet"))
        v = config()["voice"]
        kok = os.path.join(v["kokoro_dir"], v["kokoro_model"])
        row(os.path.isfile(kok), "voice files (spoken answers)", required=False)
        row(os.path.isdir(v["whisper_model"]), "speech recognition model (microphone)", required=False)
        row(ROOT.lower() in json.dumps(config()).replace("\\\\", "\\").lower(), "settings point at this folder")
    except Exception as exc:
        log(traceback.format_exc())
        row(False, f"could not read settings or models ({exc})")
    row(os.path.isfile(os.path.join(desktop_dir(), "ISHTI.lnk")), "ISHTI icon on the desktop")
    say("\nAll good - ISHTI is ready." if not problems else
        f"\n{problems} thing(s) need attention. Double-click install.bat to fix them (it is safe to run again).")
    return 1 if problems else 0


# ----------------------------------------------------------------------------- phase 1
def phase1():
    say("ISHTI setup - this takes a while the first time (about 20-40 minutes, mostly downloading).")
    say("You can leave it running. It is safe to close this window and run install.bat again later.\n")
    log(f"python {sys.version}; root {ROOT}")

    step(1, "Checking your PC")
    build, wname = windows_info()
    if build < 19041:
        say(f"This PC runs an old Windows (build {build}). ISHTI needs Windows 10 (version 2004 or newer) or Windows 11.")
        return 1
    say(f"  {wname}: OK")
    if sys.version_info[:2] != (3, 11):
        say(f"  This installer was started with Python {sys.version_info[0]}.{sys.version_info[1]}, but ISHTI needs Python 3.11.")
        say("  Please double-click install.bat instead of running this file by hand.")
        return 1
    if not os.path.isfile(DASHBOARD):
        say("  The dashboard files are missing (interface\\app\\dist). Part of the download got lost.")
        say("  Please download the full project again: GitHub page -> green Code button -> Download ZIP.")
        return 1
    say("  Dashboard files: OK")
    free = free_gb()
    if free < MIN_FREE_GB and not os.path.isfile(STAMP):
        say(f"  Only {free:.0f} GB free on this drive; ISHTI needs about {MIN_FREE_GB} GB (programs plus AI models).")
        if not ask("  Continue anyway?", default=False):
            say("  Free some space (or move this folder to a bigger drive) and run install.bat again.")
            return 1
    else:
        say(f"  Free disk space: {free:.0f} GB: OK")
    name, mem = gpu()
    say(f"  Graphics card: {name} ({mem / 1024:.0f} GB): OK" if name else
        "  No NVIDIA graphics card found. ISHTI will still install, but it will be very slow without one.")
    if not webview2_installed():
        say("  Microsoft Edge WebView2 (draws the ISHTI window) is missing.")
        if ask("  Install it now with winget (small download)?"):
            run_logged(["winget", "install", "-e", "--id", "Microsoft.EdgeWebView2Runtime",
                        "--accept-package-agreements", "--accept-source-agreements"])
        if not webview2_installed():
            say("  Could not confirm WebView2. If the ISHTI window does not open, install it from https://go.microsoft.com/fwlink/p/?LinkId=2124703")

    step(2, "Preparing ISHTI's own Python workspace (the 'venv' folder)")
    if os.path.isfile(VPY) and run_logged([VPY, "-c", "import sys"])[0] == 0:
        say("  Already there.")
    else:
        shutil.rmtree(VENV, ignore_errors=True)    # a half-made one would only cause confusing errors later
        code, out = run_logged([sys.executable, "-m", "venv", VENV])
        if code:
            say("  Could not create the workspace. See install.log for details.")
            return 1
        say("  Created.")

    step(3, "Installing ISHTI's program parts (about 5 GB download, the longest step)")
    if os.path.isfile(STAMP) and open(STAMP).read().strip() == requirements_hash() and not missing_packages():
        say("  Already installed.")
    else:
        run_logged([VPY, "-m", "pip", "install", "--upgrade", "pip", "--disable-pip-version-check"])
        for attempt in range(1, 4):
            if pip_install(["-r", REQS], "program parts") == 0:
                break
            say("  The internet connection hiccuped. Trying again..." if attempt < 3 else "")
        else:
            say("  The programs could not be installed. Please check your internet connection and run install.bat again")
            say("  (nothing is lost: it continues where it stopped). Details: install.log")
            return 1
        if "chatterbox_tts" not in installed_dists():     # own-voice feature; --no-deps on purpose (see requirements.txt)
            if pip_install(["--no-deps", "chatterbox-tts==0.1.7"], "own-voice add-on") != 0:
                say("  (The optional 'speak in my own voice' add-on could not be installed. Everything else will work.)")
        with open(STAMP, "w") as f:
            f.write(requirements_hash())
        say("  Done.")

    step(4, "Pointing ISHTI at this folder")
    fix_config_paths()

    # phase 2 needs the venv's packages (requests, ishti.manage, win32com)
    return subprocess.call([VPY, os.path.abspath(__file__), "--finish"] + (["--yes"] if YES else []))


def pip_install(args, label):
    """pip with calm progress: a counter and a 'still working' note instead of pip's wall of text."""
    cmd = [VPY, "-m", "pip", "install", "--progress-bar", "off", "--disable-pip-version-check", *args]
    log("$ " + " ".join(cmd))
    start, state = time.time(), {"n": 0, "done": False}

    def heartbeat():
        while not state["done"]:
            time.sleep(20)
            if not state["done"]:
                say(f"  ...still working on the {label} ({int((time.time() - start) / 60)} min so far, {state['n']} items looked at)")
    threading.Thread(target=heartbeat, daemon=True).start()
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
                         creationflags=NO_WINDOW)
    for line in p.stdout:
        log(line.rstrip())
        if line.startswith("Collecting"):
            state["n"] += 1
        elif line.startswith("Installing collected packages"):
            say(f"  Downloaded. Now installing the {label} (a few minutes)...")
    state["done"] = True
    return p.wait()


def fix_config_paths():
    """ishti\\config.json names D:\\ISHTI\\... paths. On any other folder those would not exist: rewrite them once."""
    text = open(CONFIG, encoding="utf-8").read()
    old = "D:\\\\ISHTI"
    if old not in text or norm(ROOT) == norm("D:\\ISHTI"):
        say("  Already set.")
        return
    new_text = text.replace(old, ROOT.replace("\\", "\\\\"))
    json.loads(new_text)       # never write a broken settings file
    tmp = CONFIG + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        f.write(new_text)
    os.replace(tmp, CONFIG)
    say(f"  Settings now point at {ROOT}")


# ----------------------------------------------------------------------------- phase 2 (runs under venv\python)
def start_ollama():
    """-> (ok, process we started or None)."""
    if ollama_tags() is not None:
        return True, None
    say("  Starting Ollama...")
    proc = subprocess.Popen([ollama_exe(), "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=NO_WINDOW)
    for _ in range(40):
        time.sleep(1)
        if ollama_tags() is not None:
            return True, proc
    proc.terminate()
    return False, None


def show_progress(jobs, key, label):
    last = ""
    while True:
        j = jobs.get(key, {})
        st = j.get("status", "")
        if st in ("done", "error"):
            print(" " * 70, end="\r")
            return st == "done", j.get("error", "")
        pct = f" {min(100, 100 * j['done'] // j['total'])}%  ({gb(j['done'])} of {gb(j['total'])})" if j.get("total") and j["done"] <= j["total"] else ""
        line = f"  {label}: {st[:30]}{pct}"
        if line != last:
            print(line.ljust(70), end="\r", flush=True)
            last = line
        time.sleep(1)


def phase2():
    sys.path.insert(0, ROOT)
    from ishti import manage    # the same download code the Settings page uses
    log("phase 2")

    step(5, "Ollama (runs the AI models on your PC)")
    exe = ollama_exe()
    while not exe:
        say("  Ollama is not installed yet. It is a free program that runs the AI models on your own PC.")
        say("  1. Open https://ollama.com/download  2. Download and run the Windows installer  3. Come back to this window.")
        if YES or not sys.stdin.isatty():
            return 1
        if input("  Press Enter when Ollama is installed (or type Q and Enter to stop): ").strip().lower() == "q":
            say("  Stopped. Run install.bat again after installing Ollama.")
            return 1
        exe = ollama_exe()
    ver = ollama_version(exe)
    while ver and ver < MIN_OLLAMA:
        say(f"  Your Ollama ({'.'.join(map(str, ver))}) is too old for ISHTI's AI model. Please update it from https://ollama.com/download")
        if YES or not sys.stdin.isatty():
            return 1
        wait_key("  Press Enter when it is updated: ")
        ver = ollama_version(ollama_exe())
    say(f"  Ollama {'.'.join(map(str, ver)) if ver else ''}: OK")
    ok, ours = start_ollama()
    if not ok:
        say("  Ollama would not start. Open the Ollama app from the Start menu once, then run install.bat again.")
        return 1
    try:
        step(6, "Downloading the AI models ISHTI needs on its first run")
        if not models_step(manage):
            return 1
        step(7, "Voice files (spoken answers and the microphone)")
        voice_step(manage)
    finally:
        if ours:
            ours.terminate()    # we started it for the install only; ISHTI starts and stops its own when it runs

    step(8, "Putting the ISHTI icon on your desktop")
    code, out = run_logged([VPY, os.path.join(ROOT, "desktop", "create_shortcut.py")], cwd=ROOT)
    if code:
        say("  Could not create the icon. You can still start ISHTI by double-clicking start.bat in this folder.")
    else:
        say("  Done.")
    say("\n" + "=" * 60)
    say("All done - double-click ISHTI on your desktop.")
    say("=" * 60)
    return 0


def models_step(manage):
    name, mem = gpu()
    plan = model_plan(installed_models(), mem)
    todo = [p for p in plan if not p[5]]
    if mem and plan[0][1] != config()["llm"]["models"]["planner"]:
        say(f"  Your graphics card has {mem / 1024:.0f} GB, so ISHTI will use a smaller AI model for thinking: {plan[0][1]}")
    if not todo:
        say("  All needed models are already downloaded.")
        return True
    bases = {}
    for role, n, mf, base, size, _ in todo:
        bases[base] = size
    total = sum(s or 0 for s in bases.values())
    say("  ISHTI needs these models (they are downloaded once and stay on your PC):")
    for role, n, mf, base, size, _ in todo:
        say(f"    - {n}  ({role}):  {gb(size) if size else 'size unknown'}")
    say(f"  Total download: about {gb(total)}.  Free space on this drive: {free_gb():.0f} GB.")
    if not ask("  Download now?"):
        say("  Skipped. ISHTI cannot answer until a model is installed: run install.bat again, or use Settings -> Models & voices.")
        return True
    installed = installed_models()
    for role, n, mf, base, size, _ in todo:
        if not has_model(base, installed):
            say(f"  Downloading {base} ...")
            manage.pull_model(base)
            time.sleep(1)
            ok, err = show_progress(manage.JOBS, base, base)
            if not ok:
                say(f"  The download of {base} stopped: {err or 'unknown reason'}. Check your internet and run install.bat again.")
                return False
            installed.add(base if ":" in base else base + ":latest")
        if mf:
            say(f"  Preparing {n} ...")
            code, out = run_logged([ollama_exe(), "create", n, "-f", mf], cwd=ROOT)
            if code:
                say(f"  Could not prepare {n}. Details: install.log")
                return False
        say(f"  {n}: ready.")
    return True


def voice_step(manage):
    v = config()["voice"]
    key = "kokoro:" + v["kokoro_model"]
    files = manage.engines()[key]["files"]
    need_voice = not all(os.path.isfile(p) for p, _ in files)
    need_stt = not os.path.isdir(v["whisper_model"])
    if not (need_voice or need_stt):
        say("  Already downloaded.")
        return
    parts = ([f"voices ({', '.join(os.path.basename(p) for p, _ in files if not os.path.isfile(p))})"] if need_voice else []) + \
            ([f"speech recognition (about {WHISPER_GB} GB)"] if need_stt else [])
    say(f"  Optional: {' and '.join(parts)}. The microphone stays OFF until you switch it on in the app.")
    if not ask("  Download them now?"):
        say("  Skipped. You can do this later in Settings -> Models & voices.")
        return
    if need_voice:
        say("  Downloading voices ...")
        manage.download_files(key)
        time.sleep(1)
        ok, err = show_progress(manage.JOBS, key, "voices")
        say("  Voices: ready." if ok else f"  Voices could not be downloaded ({err}). Try again in Settings -> Models & voices.")
    if need_stt:
        say("  Downloading speech recognition ...")
        try:
            import importlib.util
            spec = importlib.util.spec_from_file_location("dl_whisper", os.path.join(ROOT, "tests", "download_whisper_turbo.py"))
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            mod.MODEL_DIR = v["whisper_model"]
            say("  Speech recognition: ready." if mod.main() == 0 else "  Speech recognition download incomplete; try again in Settings -> Models & voices.")
        except Exception as exc:
            log(traceback.format_exc())
            say(f"  Speech recognition could not be downloaded ({exc}). Try again in Settings -> Models & voices.")


# ----------------------------------------------------------------------------- entry
def main():
    global YES
    ap = argparse.ArgumentParser(description="ISHTI installer")
    ap.add_argument("--check", action="store_true", help="read-only report; exit 0 if everything is in place, else 1")
    ap.add_argument("--yes", action="store_true", help="answer every question with its default")
    ap.add_argument("--finish", action="store_true", help=argparse.SUPPRESS)
    a = ap.parse_args()
    YES = a.yes
    if a.check:
        global LOG
        LOG = None
        return check()
    try:
        return phase2() if a.finish else phase1()
    except KeyboardInterrupt:
        say("\nStopped. Run install.bat again any time; it continues where it left off.")
        return 1
    except Exception:
        log(traceback.format_exc())
        say("\nSomething unexpected went wrong. Nothing is damaged: run install.bat again.")
        say(f"If it happens again, the file install.log in {ROOT} explains why.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
