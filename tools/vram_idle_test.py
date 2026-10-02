"""Does idle-unload really free the GPU?  python tools/vram_idle_test.py [--idle-s 600] [--out _work/idle.json]

Uses the real ISHTI code paths with a real GPU (nvidia-smi is the judge, not a timer firing):
  planner      pinned: must still be resident at the end (never unloaded by idleness)
  translator   Ollama model through ishti.llm            (ishti.models idle timer + keep_alive)
  whisper      speech recognition through ishti.voice     (ishti.voice idle timer)
  converter    own-voice converter through ishti.clone    (ishti.clone idle timer)
Each is used once, then left alone. The script polls every 5 s until it is gone (or 1.5x the threshold passes) and
records: when it unloaded (seconds after its last use), how much nvidia-smi memory dropped, and what the next use cost
(cold reload vs warm) - the price of the idle-unload saving. --idle-s overrides the 10-minute default for quick runs.
Run it with ISHTI stopped and nothing else using the GPU."""
import argparse
import io
import json
import os
import subprocess
import sys
import time
import wave

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def smi():
    out = subprocess.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"], capture_output=True, text=True).stdout
    return int(out.strip().splitlines()[0])


def silence_wav(seconds=1.0, rate=16000):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(rate * seconds))
    buf.seek(0)
    return buf


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--idle-s", type=int, default=600)
    ap.add_argument("--out", default=os.path.join(ROOT, "_work", "idle.json"))
    a = ap.parse_args()
    for k in ("ISHTI_IDLE_TIMEOUT_S", "ISHTI_WHISPER_IDLE_S", "ISHTI_CLONE_IDLE_S"):
        os.environ[k] = str(a.idle_s)
    from ishti import clone, llm, vram, voice

    for m in vram.ps(True):
        vram.unload(m["name"])
    time.sleep(2)
    res = {"idle_threshold_s": a.idle_s, "baseline_mib": smi(), "items": {}}
    print("baseline GPU MiB", res["baseline_mib"], flush=True)

    # --- planner (pinned) + translator
    t = time.time()
    llm.ask("Reply with one word: ok", role="planner", system=None, temperature=0, max_tokens=3)
    planner = llm.model_for("planner")
    translator = llm.model_for("translator")
    t0 = time.time()
    llm.ask("Translate to Urdu: good morning", role="translator", system=None, temperature=0, max_tokens=20)
    tr_cold = time.time() - t0
    t0 = time.time()
    llm.ask("Translate to Urdu: good night", role="translator", system=None, temperature=0, max_tokens=20)
    tr_warm = time.time() - t0
    used_tr = {"model": translator, "last_use": time.time(), "cold_first_use_s": round(tr_cold, 2), "warm_s": round(tr_warm, 2)}

    # --- whisper
    t0 = time.time()
    voice.transcribe(silence_wav())
    st_cold = time.time() - t0
    t0 = time.time()
    voice.transcribe(silence_wav())
    st_warm = time.time() - t0
    used_st = {"last_use": time.time(), "cold_first_use_s": round(st_cold, 2), "warm_s": round(st_warm, 2)}

    # --- own-voice converter (two of ISHTI's own voices; nobody's real voice)
    conv = None
    if all(os.path.isfile(os.path.join(clone.DIR, f)) for f in clone.FILES):
        try:
            roster = {v["name"]: v for v in voice.roster()}
            src = voice.synthesize("I will be home at eight tonight.", roster["Female 1 - Neutral"])
            tgt = voice.synthesize("This is a reference recording of the voice that should be used for the test.", roster["Male 1 - Deep/Calm"])
            import tempfile
            tp = os.path.join(tempfile.mkdtemp(), "t.wav")
            open(tp, "wb").write(tgt)
            t0 = time.time()
            clone.convert(src, tp)
            cv_cold = time.time() - t0
            t0 = time.time()
            clone.convert(src, tp)
            conv = {"last_use": time.time(), "cold_first_use_s": round(cv_cold, 2), "warm_s": round(time.time() - t0, 2), "target": tp, "src": src}
        except Exception as exc:
            res["items"]["converter"] = {"skipped": f"{type(exc).__name__}: {exc}"}
    else:
        res["items"]["converter"] = {"skipped": "converter files not installed"}

    peak = smi()
    res["peak_mib"] = peak
    print("all in use: GPU MiB", peak, "resident:", [(m["name"], m["size_vram_mb"]) for m in vram.ps(True)], flush=True)
    pending = {"translator": used_tr, "whisper": used_st, **({"converter": conv} if conv else {})}
    gone = {}
    limit = time.time() + a.idle_s * 1.5 + 30
    while pending and time.time() < limit:
        time.sleep(5)
        loaded = {m["name"] for m in vram.ps(True)}
        state = {"translator": translator not in loaded and not any(n.startswith(translator.split(":")[0]) for n in loaded if n == translator),
                 "whisper": voice._stt is None, "converter": clone._vc is None}
        for k in list(pending):
            if state[k]:
                gone[k] = {"unloaded_after_last_use_s": round(time.time() - pending[k]["last_use"], 1), "gpu_mib_now": smi()}
                res["items"][k] = {**{x: pending[k][x] for x in ("cold_first_use_s", "warm_s")}, **gone[k]}
                del pending[k]
                print(f"  {k} unloaded {gone[k]['unloaded_after_last_use_s']}s after last use, GPU now {gone[k]['gpu_mib_now']} MiB", flush=True)
    for k in pending:
        res["items"][k] = {"NOT_UNLOADED": True, **{x: pending[k][x] for x in ("cold_first_use_s", "warm_s")}}
    final_loaded = [(m["name"], m["size_vram_mb"]) for m in vram.ps(True)]
    res.update(final_mib=smi(), planner=planner, planner_resident_at_end=any(vram.role_of(n) == "planner" for n, _ in final_loaded),   # the governor may have moved it a rung lighter for voice
               resident_at_end=final_loaded)
    # --- what the next use costs after the unload
    if "translator" in gone:
        t0 = time.time()
        llm.ask("Translate to Urdu: good evening", role="translator", system=None, temperature=0, max_tokens=20)
        res["items"]["translator"]["reload_after_unload_s"] = round(time.time() - t0, 2)
    if "whisper" in gone:
        t0 = time.time()
        voice.transcribe(silence_wav())
        res["items"]["whisper"]["reload_after_unload_s"] = round(time.time() - t0, 2)
    if "converter" in gone:
        t0 = time.time()
        clone.convert(conv["src"], conv["target"])
        res["items"]["converter"]["reload_after_unload_s"] = round(time.time() - t0, 2)
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    json.dump(res, open(a.out, "w", encoding="utf-8"), indent=1, default=str)
    print(json.dumps(res, indent=1, default=str))
    clone.drop()
    voice.evict_idle(0)
    sys.exit(0 if not any(v.get("NOT_UNLOADED") for v in res["items"].values()) and res["planner_resident_at_end"] else 1)


if __name__ == "__main__":
    main()
