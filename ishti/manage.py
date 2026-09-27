"""Settings -> Models & voices: choose, delete (free space) and download again - language models, the speech
recogniser and voice engines. Every change is recorded in the audit log as the owner's own action.

Storage is per engine, not per voice: the 6 Kokoro voices share one model + voice pack; each Piper voice is its own
file; Fish S2 is a model file + a Docker image; Whisper models live in the Hugging Face cache. Deleting is refused
for what's in use (switch first), so ISHTI never deletes the model it's thinking with or the voice it's speaking in."""
import json
import os
import shutil
import subprocess
import threading
import time

import requests

from ishti import CONFIG, ROOT

OLLAMA = CONFIG["llm"]["base_url"].rsplit("/v1", 1)[0]
CONFIG_PATH = os.path.join(ROOT, "ishti", "config.json")
DATA = os.path.dirname(CONFIG["memory"]["db_path"])
CATALOG = os.path.join(DATA, "model_catalog.json")  # models seen here once - so a deleted one can be downloaded again
JOBS = {}  # key -> {"status", "done", "total", "error"} for downloads in progress
HF_PIPER = "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/{lang}/{speaker}/{quality}/{name}"
KOKORO_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/{file}"
S2_URL = "https://huggingface.co/rodrigomt/s2-pro-gguf/resolve/main/{file}"
WHISPER_OPTIONS = ["large-v3", "large-v3-turbo", "medium", "small"]
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _audit(action, detail=""):
    from ishti.audit import record
    record(action, detail, initiator="owner", skill="settings")


def _save_config(mutate):
    with open(CONFIG_PATH, encoding="utf-8") as f:
        cfg = json.load(f)
    mutate(cfg)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
        f.write("\n")
    mutate(CONFIG)  # the running process uses it at once


def _size(path):
    if os.path.isfile(path):
        return os.path.getsize(path)
    return sum(os.path.getsize(os.path.join(r, f)) for r, _, fs in os.walk(path) for f in fs) if os.path.isdir(path) else 0


# ---------------------------------------------------------------- language models (Ollama)
def _catalog():
    try:
        with open(CATALOG, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def llm_state():
    tags = requests.get(f"{OLLAMA}/api/tags", timeout=10).json().get("models", [])
    loaded = {m["name"] for m in requests.get(f"{OLLAMA}/api/ps", timeout=10).json().get("models", [])}
    roles = dict(CONFIG["llm"]["models"])
    cat = _catalog()
    for m in tags:
        cat[m["name"]] = m.get("size", 0)
    os.makedirs(DATA, exist_ok=True)
    with open(CATALOG, "w", encoding="utf-8") as f:
        json.dump(cat, f)
    installed = {m["name"]: m for m in tags}
    try:
        from ishti.models import tier
    except Exception:
        tier = lambda m: ""
    used = protected_roles()
    weights = {n: _weights(n) for n in installed}  # models built on the same weights share one file on disk
    models = []
    for n in sorted(set(installed) | set(cat)):
        twins = sorted(m for m, w in weights.items() if m != n and w and w == weights.get(n))
        models.append({"name": n, "installed": n in installed, "size": installed[n]["size"] if n in installed else cat.get(n, 0),
                       "loaded": n in loaded, "roles": [r for r, v in used.items() if _same(v, n)],
                       "residency": tier(n) if n in installed else "", "shares_weights_with": twins,
                       "frees": 0 if twins else (installed[n]["size"] if n in installed else 0)})
    return {"roles": roles, "models": models}


def protected_roles():
    """Every model ISHTI uses: the chat roles plus the embedding model (semantic memory) and the fallback."""
    return {**CONFIG["llm"]["models"], "embeddings": CONFIG["memory"]["embed_model"], "fallback": CONFIG["llm"].get("fallback", "")}


def _weights(name):
    """The digest of a model's weight blob (FROM sha256-... in its Modelfile)."""
    try:
        mf = requests.post(f"{OLLAMA}/api/show", json={"model": name}, timeout=15).json().get("modelfile", "")
        import re
        m = re.search(r"^FROM\s+\S*?(sha256[-:][0-9a-f]{64})", mf, re.M)
        return m.group(1).replace(":", "-") if m else ""
    except Exception:
        return ""


def _same(a, b):
    return a == b or a + ":latest" == b or b + ":latest" == a


def set_role(role, model):
    if role not in CONFIG["llm"]["models"]:
        raise ValueError(f"unknown role {role}")
    if not any(_same(m["name"], model) and m["installed"] for m in llm_state()["models"]):
        raise ValueError(f"{model} isn't installed - download it first")
    _save_config(lambda c: c["llm"]["models"].__setitem__(role, model))
    _audit(f"model role {role} -> {model}")


def delete_model(name):
    used = [r for r, v in protected_roles().items() if _same(v, name)]
    if used:
        raise ValueError(f"{name} is in use as {', '.join(used)} - pick another model for that first")
    r = requests.delete(f"{OLLAMA}/api/delete", json={"model": name}, timeout=60)
    if r.status_code not in (200, 404):
        raise RuntimeError(r.text[:200])
    _audit(f"deleted model {name}")


def pull_model(name):
    def job():
        JOBS[name] = {"status": "starting", "done": 0, "total": 0}
        try:
            with requests.post(f"{OLLAMA}/api/pull", json={"model": name, "stream": True}, stream=True, timeout=None) as r:
                for line in r.iter_lines():
                    if line:
                        d = json.loads(line)
                        JOBS[name].update(status=d.get("status", ""), done=d.get("completed", JOBS[name]["done"]),
                                          total=d.get("total", JOBS[name]["total"]))
                        if d.get("error"):
                            raise RuntimeError(d["error"])
            JOBS[name]["status"] = "done"
            _audit(f"downloaded model {name}")
        except Exception as exc:
            JOBS[name].update(status="error", error=str(exc)[:200])
    threading.Thread(target=job, daemon=True).start()


# ---------------------------------------------------------------- speech recognition (Whisper)
def _whisper_dir(name):
    from faster_whisper.utils import _MODELS
    repo = _MODELS.get(name, f"Systran/faster-whisper-{name}")
    return os.path.join(os.path.expanduser("~"), ".cache", "huggingface", "hub", "models--" + repo.replace("/", "--"))


def whisper_state():
    current = CONFIG["voice"]["whisper_model"]
    def ok(n):  # a finished download has its model.bin (a cancelled one leaves only metadata)
        d = _whisper_dir(n)
        return any(f == "model.bin" for _, _, fs in os.walk(d) for f in fs) if os.path.isdir(d) else False
    return [{"name": n, "installed": ok(n), "size": _size(_whisper_dir(n)), "selected": n == current} for n in WHISPER_OPTIONS]


def set_whisper(name):
    if name not in WHISPER_OPTIONS:
        raise ValueError(name)
    _save_config(lambda c: c["voice"].__setitem__("whisper_model", name))
    import ishti.voice as v
    v._stt = None  # loads the new one on the next use
    _audit(f"speech recognition -> whisper {name}")


def delete_whisper(name):
    if name == CONFIG["voice"]["whisper_model"]:
        raise ValueError("that's the speech model in use - pick another first")
    shutil.rmtree(_whisper_dir(name), ignore_errors=True)
    _audit(f"deleted whisper {name}")


def download_whisper(name):
    def job():
        JOBS[f"whisper:{name}"] = {"status": "downloading", "done": 0, "total": 0}
        try:
            from faster_whisper import download_model
            download_model(name)
            JOBS[f"whisper:{name}"]["status"] = "done"
            _audit(f"downloaded whisper {name}")
        except Exception as exc:
            JOBS[f"whisper:{name}"].update(status="error", error=str(exc)[:200])
    threading.Thread(target=job, daemon=True).start()


# ---------------------------------------------------------------- voices and their engine files
def engines():
    """Downloadable/deletable packages: key -> {"label", "files": [(path, url)], "voices": [names], "note"}."""
    v = CONFIG["voice"]
    kdir = v.get("kokoro_dir", os.path.join(DATA, "kokoro"))
    out = {}
    for build, label in (("kokoro-v1.0.onnx", "Kokoro (full quality, fastest here)"), ("kokoro-v1.0.int8.onnx", "Kokoro int8 (smaller)")):
        out[f"kokoro:{build}"] = {"label": label, "files": [(os.path.join(kdir, build), KOKORO_URL.format(file=build)),
                                                            (os.path.join(kdir, "voices-v1.0.bin"), KOKORO_URL.format(file="voices-v1.0.bin"))],
                                  "voices": [p["name"] for p in v.get("voices", []) if p.get("engine") == "kokoro"],
                                  "selected": v.get("kokoro_model", "kokoro-v1.0.onnx") == build, "shared": [os.path.join(kdir, "voices-v1.0.bin")]}
    for p in v.get("voices", []):
        if p.get("engine", "piper") == "piper":
            name = p["piper_voice"]
            lang, speaker, quality = name.split("-")[0], name.split("-")[1], name.split("-")[2]
            base = os.path.join(v["piper_dir"], name)
            out[f"piper:{name}"] = {"label": p["name"], "voices": [p["name"]],
                                    "files": [(base + ext, HF_PIPER.format(lang=lang, speaker=speaker, quality=quality, name=name + ext))
                                              for ext in (".onnx", ".onnx.json")]}
    s2 = os.path.join(DATA, "s2", "models")
    out["s2"] = {"label": "Fish Audio S2 (studio quality; 2.7x slower than real time here, ~4 GB VRAM - not for live talk)",
                 "voices": [], "files": [(os.path.join(s2, f), S2_URL.format(file=f)) for f in ("s2-pro-q4_k_m.gguf", "tokenizer.json")],
                 "docker_image": "ishti-s2cpp"}
    from ishti.clone import DIR as CLONE, FILES as CLONE_FILES
    out["clone"] = {"label": "Your own voice converter (Chatterbox VC) - voice notes in your voice", "voices": [],
                    "files": [(os.path.join(CLONE, f), f"https://huggingface.co/ResembleAI/chatterbox/resolve/main/{f}") for f in CLONE_FILES]}
    return out


def _docker_size(image):
    try:
        out = subprocess.run(["docker", "image", "inspect", image, "--format", "{{.Size}}"], capture_output=True, text=True,
                             timeout=20, creationflags=NO_WINDOW).stdout.strip()
        return int(out) if out.isdigit() else 0
    except Exception:
        return 0


def voices_state():
    from ishti.voice import current_voice, installed, roster
    cur = current_voice()["name"]
    voices = [{"name": p["name"], "engine": p.get("engine", "piper"), "gender": p.get("gender", ""), "installed": installed(p),
               "selected": p["name"] == cur, "default": p["name"] == CONFIG["voice"].get("default_voice")} for p in roster()]
    files = []
    for key, e in engines().items():
        size = sum(_size(p) for p, _ in e["files"]) + (_docker_size(e["docker_image"]) if e.get("docker_image") else 0)
        files.append({"key": key, "label": e["label"], "installed": all(os.path.isfile(p) for p, _ in e["files"]),
                      "size": size, "voices": e["voices"], "selected": e.get("selected", False), "in_use": cur in e["voices"],
                      "job": JOBS.get(key)})
    return {"voices": voices, "files": files}


def select_voice(name, default=False):
    from ishti.facts import get_facts
    from ishti.voice import installed, roster
    p = next((v for v in roster() if v["name"] == name), None)
    if not p:
        raise ValueError(name)
    if not installed(p):
        raise ValueError(f"{name} isn't downloaded - download its engine first")
    get_facts().set("owner", "voice", name)
    if default:
        _save_config(lambda c: c["voice"].__setitem__("default_voice", name))
    _audit(f"voice -> {name}" + (" (default)" if default else ""))


def delete_files(key):
    from ishti.voice import current_voice
    e = engines()[key]
    if current_voice()["name"] in e["voices"] and len(e["voices"]):
        raise ValueError("that's the voice in use - pick a voice from another engine first")
    if key.startswith("kokoro:") and e.get("selected"):
        other = next((k for k, x in engines().items() if k.startswith("kokoro:") and k != key and os.path.isfile(x["files"][0][0])), None)
        if other:  # the other Kokoro build takes over
            _save_config(lambda c: c["voice"].__setitem__("kokoro_model", other.split(":", 1)[1]))
    shared = set(e.get("shared", []))
    other_kokoro_present = any(k.startswith("kokoro:") and k != key and os.path.isfile(x["files"][0][0]) for k, x in engines().items())
    for path, _ in e["files"]:
        if path in shared and other_kokoro_present:
            continue  # the voice pack is still needed by the other Kokoro build
        if os.path.isfile(path):
            os.remove(path)
    if e.get("docker_image"):
        subprocess.run(["docker", "rmi", "-f", e["docker_image"]], capture_output=True, timeout=120, creationflags=NO_WINDOW)
    if key == "clone":
        import ishti.clone
        ishti.clone._vc = None  # frees its GPU memory too
    _audit(f"deleted voice files {key}")


def download_files(key):
    e = engines()[key]

    def job():
        JOBS[key] = {"status": "downloading", "done": 0, "total": 0}
        try:
            for path, url in e["files"]:
                if os.path.isfile(path):
                    continue
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with requests.get(url, stream=True, timeout=60) as r:
                    r.raise_for_status()
                    JOBS[key]["total"] += int(r.headers.get("content-length", 0))
                    with open(path + ".part", "wb") as f:
                        for chunk in r.iter_content(1 << 20):
                            f.write(chunk)
                            JOBS[key]["done"] += len(chunk)
                os.replace(path + ".part", path)
            if e.get("docker_image") and not _docker_size(e["docker_image"]):
                JOBS[key]["status"] = "building the engine (Docker, ~10 min)"
                subprocess.run(["docker", "build", "-t", e["docker_image"], os.path.join(DATA, "s2", "docker")], capture_output=True,
                               timeout=7200, creationflags=NO_WINDOW)
            JOBS[key]["status"] = "done"
            _audit(f"downloaded voice files {key}")
        except Exception as exc:
            JOBS[key].update(status="error", error=str(exc)[:200])
    threading.Thread(target=job, daemon=True).start()


def owner_voice_state():
    from ishti.voice import OWNER_SAMPLE
    if not os.path.isfile(OWNER_SAMPLE):
        return {"exists": False}
    import wave
    with wave.open(OWNER_SAMPLE) as w:
        secs = w.getnframes() / w.getframerate()
    return {"exists": True, "seconds": round(secs, 1), "recorded": time.strftime("%d %b %Y %H:%M", time.localtime(os.path.getmtime(OWNER_SAMPLE)))}


def save_owner_voice(raw):
    """The browser's recording -> mono 24 kHz WAV. Refused if shorter than 8 s or nearly silent: a bad sample would
    make every voice note sound wrong."""
    import tempfile
    import wave
    import numpy as np
    from ishti.skills.media.skill import ff
    from ishti.voice import OWNER_SAMPLE
    if len(raw) < 2000:
        raise ValueError("the recording is empty - check the microphone and record again")
    src = os.path.join(tempfile.mkdtemp(), "rec.bin")
    with open(src, "wb") as f:
        f.write(raw)
    tmp = src + ".wav"
    ff("-i", src, "-ac", "1", "-ar", "24000", "-af", "silenceremove=start_periods=1:start_threshold=-45dB", tmp)
    with wave.open(tmp) as w:
        pcm = np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768
        secs = w.getnframes() / w.getframerate()
    if secs < 8:
        raise ValueError(f"only {secs:.0f} s of speech - record at least 10 seconds")
    if np.sqrt((pcm ** 2).mean()) < 0.01:
        raise ValueError("the recording is almost silent - speak closer to the microphone")
    os.makedirs(os.path.dirname(OWNER_SAMPLE), exist_ok=True)
    shutil.move(tmp, OWNER_SAMPLE)
    return {"ok": True, "seconds": round(secs, 1)}


def delete_owner_voice():
    from ishti.voice import OWNER_SAMPLE
    if os.path.isfile(OWNER_SAMPLE):
        os.remove(OWNER_SAMPLE)
    return {"ok": True}


def preview(name):
    from ishti.voice import roster, synthesize
    p = next((v for v in roster() if v["name"] == name), None)
    if not p:  # a bare next() would raise StopIteration, which asyncio can't carry out of the executor: the request hangs
        raise ValueError(f"no voice named {name}")
    return synthesize("Hi, I'm ISHTI. This is how I sound - clear, natural, and ready when you are.", p)


if __name__ == "__main__":
    import tempfile
    st = llm_state()
    assert st["roles"]["planner"] and any(m["roles"] == ["planner"] or "planner" in m["roles"] for m in st["models"]), st
    try:
        delete_model(CONFIG["llm"]["models"]["planner"])
        raise AssertionError("deleted the planner model")
    except ValueError:
        pass
    vs = voices_state()
    assert any(v["selected"] for v in vs["voices"]) and any(f["key"].startswith("kokoro:") and f["installed"] for f in vs["files"])
    from ishti.voice import current_voice
    in_use = next(f["key"] for f in vs["files"] if current_voice()["name"] in f["voices"])
    try:
        delete_files(in_use)
        raise AssertionError("deleted the voice in use")
    except ValueError:
        pass
    assert preview("Female 1 - Neutral")[:4] == b"RIFF"
    try:
        preview("no such voice")
        raise AssertionError("previewed a voice that doesn't exist")
    except ValueError:
        pass
    ws = whisper_state()
    assert sum(w["selected"] for w in ws) == 1 and next(w for w in ws if w["selected"])["installed"]
    try:
        delete_model(CONFIG["memory"]["embed_model"])
        raise AssertionError("deleted the embedding model semantic memory needs")
    except ValueError:
        pass
    print(f"[self-check] manage OK - {len(st['models'])} models ({sum(m['installed'] for m in st['models'])} installed), "
          f"{len(vs['voices'])} voices, {len(vs['files'])} engine packages; in-use model and voice can't be deleted; preview works")
