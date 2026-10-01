"""Measured benchmark harness for the long-run optimize loop (measure -> change -> measure -> keep/revert).

One command, deterministic medians, machine-readable output:
    venv\\Scripts\\python -m tools.bench_opt --json _work/bench.json [--full]

Benchmarks (each xN runs, median reported):
  import_ishti    cold import of the ishti package (subprocess, fresh interpreter)
  skills_load     load_skills() over all manifests + skill.py imports (subprocess, fresh interpreter)
  route_rule      router.route() on a rule-matched request (no Laya, no LLM)
  reflex          reflex.answer() on an instant command
  llm_gen         /api/generate on the planner model: tok/s, TTFT, load time (warm)
  llm_translate   the gemma3 translator role, 32-token gen (only with --full: it swaps models)
  voice_stt       faster-whisper warm decode of a 2 s clip (only with --full; needs the GPU alone)
  suite_note      full-suite wall time is NOT measured here: run `pytest -q` separately and paste the number

The baseline lives in OPTIMIZATION_BASELINE.md; perf_guard.py compares against the JSON this writes.
"""
import argparse
import json
import os
import statistics
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
NOWIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def median(xs):
    return round(statistics.median(xs), 4) if xs else None


def timed(fn, n):
    xs = []
    for _ in range(n):
        t0 = time.perf_counter()
        fn()
        xs.append((time.perf_counter() - t0) * 1000)
    return xs


def bench_import():
    """Cold `import ishti` in a fresh interpreter (dispatcher is NOT imported here - it starts threads)."""
    code = "import time; t0=time.perf_counter(); import ishti; print(round((time.perf_counter()-t0)*1000, 2))"
    out = subprocess.run([PY, "-c", code], capture_output=True, text=True, cwd=ROOT, creationflags=NOWIN, timeout=120)
    return [float(line) for line in out.stdout.split() if line.replace(".", "").isdigit()]


def bench_skills_load(n=3):
    """load_skills() in a fresh interpreter: manifests + every skill.py import."""
    code = ("import time; t0=time.perf_counter();"
            "from ishti.skills import load_skills; s=load_skills();"
            "print(round((time.perf_counter()-t0)*1000, 2), len(s))")
    xs, count = [], None
    for _ in range(n):
        out = subprocess.run([PY, "-c", code], capture_output=True, text=True, cwd=ROOT, creationflags=NOWIN, timeout=300)
        parts = out.stdout.split()
        if parts:
            xs.append(float(parts[0]))
            count = int(parts[1])
    return xs, count


def bench_route(n=300):
    """router.route() on a rule-matched text with the real skill table (manifest statuses honoured)."""
    from ishti.skills import load_skills
    from ishti.router import route
    skills = load_skills()
    text = "what's the weather in peshawar tomorrow"  # a rule-matched request on the real table
    return timed(lambda: route(text, skills), n), len(skills)


def bench_reflex(n=2000):
    from ishti import reflex
    return timed(lambda: reflex.answer("what time is it"), n)


def _generate(model, prompt, num_predict=64, keep_alive=None):
    """One native /api/generate call -> measured timings (eval tok/s, prompt tok/s, load s)."""
    import requests
    base = os.environ.get("ISHTI_OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
    body = {"model": model, "prompt": prompt, "stream": False,
            "options": {"temperature": 0, "num_predict": num_predict}}
    if keep_alive is not None:
        body["keep_alive"] = keep_alive
    t0 = time.perf_counter()
    r = requests.post(f"{base}/api/generate", json=body, timeout=300)
    r.raise_for_status()
    wall = time.perf_counter() - t0
    d = r.json()
    eval_count, eval_dur = d.get("eval_count") or 0, d.get("eval_duration") or 0
    p_count, p_dur = d.get("prompt_eval_count") or 0, d.get("prompt_eval_duration") or 0
    return {"wall_s": round(wall, 3), "load_s": round(d.get("load_duration", 0) / 1e9, 3),
            "tok_s": round(eval_count / (eval_dur / 1e9), 2) if eval_dur else None,
            "prompt_tok_s": round(p_count / (p_dur / 1e9), 2) if p_dur else None,
            "eval_count": eval_count}


def unload_model(model):
    """Drop a model from Ollama's VRAM (keep_alive=0) so measurements leave no residue."""
    import requests
    base = os.environ.get("ISHTI_OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
    try:
        requests.post(f"{base}/api/generate", json={"model": model, "prompt": "", "keep_alive": 0}, timeout=60)
    except requests.RequestException:
        pass


def bench_llm(model, n=5, num_predict=64, prompt="List five colours, one per line.", unload_after=False):
    """Warm the model once (load excluded from later runs), then n timed generations; medians over the warm runs.
    unload_after: drop it again (keep_alive=0) so the next benchmark starts from the same VRAM state."""
    _generate(model, "warm up", num_predict=8)
    runs = [_generate(model, prompt, num_predict=num_predict) for _ in range(n)]
    if unload_after:
        unload_model(model)
    return runs


def bench_stt():
    """Warm whisper decode of a 2 s clip on the single STT thread (the voice.py path), x5."""
    import io
    import wave
    import numpy as np
    from ishti import voice

    clip = os.path.join(tempfile.gettempdir(), "ishti_bench_clip.wav")
    if os.path.isfile(clip):
        os.remove(clip)  # always regenerate: a clip left by a crashed run has no WAV header
        from ishti.voice import synthesize
        wf = wave.open(io.BytesIO(synthesize("Tomorrow in Peshawar it will be sunny and thirty four degrees.")))
        pcm = np.frombuffer(wf.readframes(wf.getnframes()), np.int16)
        pcm16 = np.interp(np.arange(0, len(pcm) / wf.getframerate(), 1 / voice.RATE),
                          np.arange(len(pcm)) / wf.getframerate(), pcm).astype(np.int16)
        w = wave.open(clip, "w")
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(voice.RATE)
        w.writeframes(pcm16[: voice.RATE * 2].tobytes())
        w.close()
    voice.stt_model()  # load + warm-up (excluded)
    xs = []
    for _ in range(5):
        t0 = time.perf_counter()
        text = voice.transcribe(clip, language="en")
        xs.append((time.perf_counter() - t0) * 1000)
    return xs, text


def bench_tts(n=5):
    from ishti.voice import synthesize
    xs = []
    for _ in range(n):
        t0 = time.perf_counter()
        synthesize("This is the baseline text to speech measurement for ishti.")
        xs.append((time.perf_counter() - t0) * 1000)
    return xs


def bench_handle(n=60):
    """dispatcher.handle() end-to-end in a fresh interpreter with SANDBOXED stores (facts/conversation/
    semantic in temp dirs) and the LLM mocked - measures the real per-request overhead (View, pending
    cascade, audit, routing, skill run, _finish persistence) without touching owner data or the GPU.
    Two texts: a reflex command (routing skipped) and a rule-routed local skill (psutil-based)."""
    setup = (
        "import os, tempfile, sys\n"
        "tmp = tempfile.mkdtemp()\n"
        "import ishti.facts as F\n"
        "F._facts = F.Facts(os.path.join(tmp, 'f.db'))\n"
        "import ishti.llm as L\n"
        "L.ask = lambda *a, **k: 'ok'\n"
        "L.chat = lambda *a, **k: type('M', (), {'content': 'ok', 'tool_calls': None})()\n"
        "import ishti.semantic as S\n"
        "S.get_semantic = lambda path=None: S.Semantic(os.path.join(tmp, 'chroma'))\n"
        "import ishti.dispatcher as D\n"
        "class _CM:\n"
        "    active_session_id = 'bench'\n"
        "    def new_chat(self, *a): return 'bench'\n"
        "    def add_message(self, *a, **k): pass\n"
        "D.get_conversation_manager = lambda: _CM()\n"
        "D.SESSIONS.clear()\n"
        "D._inflight('bench')\n"
        "D._inflight('bench')\n"
    )
    run = (
        "import time\n"
        "texts = ['what time is it', 'how much ram am I using']\n"
        "out = {}\n"
        "for t in texts:\n"
        "    D.handle(t, 'bench')  # warm: skills/stores loaded\n"
        "    xs = []\n"
        "    for _ in range({n}):\n"
        "        t0 = time.perf_counter()\n"
        "        D.handle(t, 'bench')\n"
        "        xs.append((time.perf_counter() - t0) * 1000)\n"
        "    out[t] = xs\n"
        "import json\n"
        "print(json.dumps(out))\n"
    ).replace("{n}", str(n))
    code = setup + run
    out = subprocess.run([PY, "-c", code], capture_output=True, text=True, cwd=ROOT, creationflags=NOWIN, timeout=600)
    if out.returncode != 0:
        print(out.stderr[-1500:])
        return None
    import json as _json
    raw = _json.loads(out.stdout.strip().splitlines()[-1])
    return {k: sorted(v)[len(v) // 2] for k, v in raw.items()}  # medians


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default=None, help="write the full result JSON here")
    ap.add_argument("--full", action="store_true", help="also run the model-swapping and GPU-heavy benchmarks")
    ap.add_argument("--quick", action="store_true", help="route/reflex/import only (perf_guard)")
    args = ap.parse_args()

    out = {"when": time.strftime("%Y-%m-%d %H:%M:%S"), "python": sys.version.split()[0]}
    print("[bench] import_ishti ...", flush=True)
    out["import_ishti_ms"] = {"runs": bench_import(), "median": median(bench_import())}
    if not args.quick:
        print("[bench] skills_load ...", flush=True)
        xs, count = bench_skills_load()
        out["skills_load_ms"] = {"runs": xs, "median": median(xs), "skills": count}
    print("[bench] route_rule ...", flush=True)
    xs, nskills = bench_route()
    out["route_rule_ms"] = {"runs": xs, "median": median(xs), "skills": nskills}
    print("[bench] reflex ...", flush=True)
    out["reflex_ms"] = {"runs": bench_reflex(), "median": median(bench_reflex())}
    if not args.quick:
        print("[bench] dispatcher.handle (sandboxed, LLM mocked) ...", flush=True)
        try:
            h = bench_handle()
            if h:
                out["handle_reflex_ms"] = {"median": h["what time is it"]}
                out["handle_skill_ms"] = {"median": h["how much ram am I using"]}
                print(f"[bench] handle_reflex_ms: median {out['handle_reflex_ms']['median']}")
                print(f"[bench] handle_skill_ms: median {out['handle_skill_ms']['median']}")
        except Exception as exc:
            out["handle_error"] = f"{type(exc).__name__}: {exc}"

    if not args.quick:
        from ishti import llm
        planner = llm.model_for("planner")
        print(f"[bench] llm_gen {planner} ...", flush=True)
        runs = bench_llm(planner)
        out["llm_gen"] = {"model": planner, "runs": runs,
                          "median_tok_s": median([r["tok_s"] for r in runs if r["tok_s"]]),
                          "median_wall_s": median([r["wall_s"] for r in runs]),
                          "median_load_s": median([r["load_s"] for r in runs])}
        if not args.full:
            unload_model(planner)  # quick mode leaves VRAM as it found it (a resident planner
            # downshifts later tests' models: bench -> suite interference, found 2026-10-01)
        if args.full:
            from ishti import CONFIG
            tr = CONFIG["llm"]["models"].get("translator", "gemma3:4b")
            print(f"[bench] llm_translate {tr} (swaps models) ...", flush=True)
            runs = bench_llm(tr, num_predict=32, prompt="Translate into Urdu: The weather is nice today.", unload_after=True)
            out["llm_translate"] = {"model": tr, "runs": runs, "median_tok_s": median([r["tok_s"] for r in runs if r["tok_s"]])}
            print("[bench] voice_stt (loads whisper on the GPU) ...", flush=True)
            try:
                xs, text = bench_stt()
                out["voice_stt_ms"] = {"runs": xs, "median": median(xs), "heard": text[:60]}
            except Exception as exc:
                out["voice_stt_ms"] = {"error": f"{type(exc).__name__}: {exc}"}
            try:
                out["voice_tts_ms"] = {"runs": bench_tts(), "median": median(bench_tts())}
            except Exception as exc:
                out["voice_tts_ms"] = {"error": f"{type(exc).__name__}: {exc}"}

    try:
        import requests
        base = os.environ.get("ISHTI_OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
        ps = requests.get(f"{base}/api/ps", timeout=5).json().get("models", [])
        out["ollama_loaded"] = [{"name": m["name"], "size_vram_mb": round(m["size_vram"] / 2**20),
                                 "full": m["size_vram"] >= m["size"]} for m in ps]
        if args.full:  # the full run measured all its models; leave nothing resident behind it
            for m in ps:
                unload_model(m["name"])
    except Exception as exc:
        out["ollama_loaded"] = [{"error": str(exc)}]

    for k, v in out.items():
        if isinstance(v, dict) and "median" in v:
            print(f"[bench] {k}: median {v['median']}" + (f" ({v.get('skills')} skills)" if v.get("skills") else ""))
        elif k == "llm_gen":
            print(f"[bench] {k}: {v['model']} tok/s {v['median_tok_s']} wall {v['median_wall_s']}s")
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(out, f, indent=1)
        print(f"[bench] written {args.json}")


if __name__ == "__main__":
    main()
