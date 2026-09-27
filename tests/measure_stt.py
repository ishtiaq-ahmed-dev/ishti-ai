"""Measure faster-whisper latency per pattern, so every choice in ishti/config.json + ishti/voice.py is evidence.

Why a separate tool: a measurement needs a model *alone* in VRAM (the 14B planner holds ~10 GB), and the
before/after comparison (large-v3 vs large-v3-turbo) must never load both models at once - so this script is
started as a fresh subprocess per model and reports a table plus JSON.

Patterns measured (16 kHz mono float32 in, as every caller of voice.transcribe feeds):
  load      WhisperModel(...) - cold load from disk
  warmup    first transcribe call (CTranslate2 builds its kernels here; the new voice.py warms up at load time)
  detect    model.detect_language(pcm) - the extra encoder pass the old path paid per utterance
  old       pick_language() + transcribe(language=lang, beam_size=1, vad_filter=True)   <- the current code
  new       transcribe(language=lang, without_timestamps=True, temperature=0.0, ...)    <- the new path
  nolang    transcribe(language=None) - language detected *inside* the call (2 encoder passes)
  batched   BatchedInferencePipeline.transcribe(language=lang, batch_size=8)
  partial   Stream.partial(): tail window only, no VAD, no timestamps, no language pass

Run:  D:\\ISHTI\\venv\\Scripts\\python.exe D:\\ISHTI\\tests\\measure_stt.py [--model M] [--compute C] [--json F]
"""
import argparse
import io
import json
import os
import sys
import time
import wave

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

RATE = 16000
SENTENCE = "Tomorrow in Peshawar it will be sunny and thirty four degrees, with a light breeze in the evening."


def pcm16k(text: str) -> np.ndarray:
    """Kokoro -> 16 kHz int16, exactly like wake.pcm16k (the STT input format)."""
    from ishti.voice import synthesize
    with wave.open(io.BytesIO(synthesize(text))) as wf:
        pcm, rate = np.frombuffer(wf.readframes(wf.getnframes()), np.int16), wf.getframerate()
    t = np.arange(0, len(pcm) / rate, 1 / RATE)
    return np.interp(t, np.arange(len(pcm)) / rate, pcm).astype(np.int16)


def clip(seconds, sentence=SENTENCE) -> np.ndarray:
    """A spoken clip of ~`seconds` (synthesized once, then cut or repeated): the shapes real commands arrive in."""
    base = pcm16k(sentence)
    if len(base) >= seconds * RATE:
        return base[: int(seconds * RATE)]
    out = base
    while len(out) < seconds * RATE:
        out = np.concatenate([out, pcm16k("Also, please remind me about it later today and thank you.")])
    return out[: int(seconds * RATE)]




def measure(model_ref, compute, device, which, clips):
    from faster_whisper import BatchedInferencePipeline, WhisperModel
    t0 = time.perf_counter()
    model = WhisperModel(model_ref, device=device, compute_type=compute)
    load_ms = (time.perf_counter() - t0) * 1000
    batched = BatchedInferencePipeline(model=model) if "batched" in which else None
    res = {"model": model_ref, "compute": compute, "device": device, "load_ms": round(load_ms), "rows": {}}

    def decode(pcm, language=None, vad=True, tail=None, **kw):
        """Consume the segment generator (it is lazy: the timings would lie otherwise)."""
        x = pcm.astype(np.float32) / 32768
        if tail:
            x = x[-int(tail * RATE):]
        if batched is not None and kw.pop("batch", False):
            segs, _ = batched.transcribe(x, language=language, beam_size=1, vad_filter=vad, **kw)
            return " ".join(s.text.strip() for s in segs)
        segs, _ = model.transcribe(x, language=language, beam_size=1, vad_filter=vad, **kw)
        return " ".join(s.text.strip() for s in segs)

    if "warmup" in which:
        _, res["warmup_ms"] = timed(decode, np.zeros(RATE // 4, np.int16))
    for name, pcm in clips.items():
        row, x = {"seconds": round(len(pcm) / RATE, 2)}, pcm.astype(np.float32) / 32768
        if "detect" in which:
            (lang, prob, _), row["detect_ms"] = timed(model.detect_language, x, vad_filter=True)
            row["detect"] = f"{lang} {prob:.2f}"
        if "old" in which:
            from ishti.voice import pick_language
            lang = pick_language(model, x)
            (row["old_text"], _), row["old_ms"] = timed(decode, pcm, language=lang, condition_on_previous_text=False)
            row["old_lang"] = lang
        if "new" in which:
            lang = (row.get("detect") or "en").split()[0] if row.get("detect") else (row.get("old_lang") or "en")
            (row["new_text"], _), row["new_ms"] = timed(decode, pcm, language=lang, without_timestamps=True,
                                                      temperature=0.0, multilingual=False, condition_on_previous_text=False)
        if "nolang" in which:
            (row["nolang_text"], _), row["nolang_ms"] = timed(decode, pcm, language=None, without_timestamps=True,
                                                             temperature=0.0)
        if "batched" in which:
            (row["batched_text"], _), row["batched_ms"] = timed(decode, pcm, language="en", batch=True,
                                                               without_timestamps=False, batch_size=8)
        if "partial" in which:
            (row["partial_text"], _), row["partial_ms"] = timed(decode, pcm, language="en", vad=False, tail=8,
                                                                without_timestamps=True, temperature=0.0,
                                                                multilingual=False, condition_on_previous_text=False)
        res["rows"][name] = row
        print(f"  [{compute}] {name}: " + " ".join(f"{k}={v}" for k, v in row.items() if k.endswith("_ms")))
    import gc
    del model, batched
    gc.collect()
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=r"D:\ISHTI\data\models\whisper-large-v3-turbo")
    ap.add_argument("--compute", default="float16")
    ap.add_argument("--device", default="cuda")
    ap.add_argument("--which", default="load,warmup,detect,new,nolang,batched,partial")
    ap.add_argument("--json", default="")
    args = ap.parse_args()

    clips = {"4s": clip(4), "12s": clip(12), "35s": clip(35)}
    from ishti import gpu
    free_before = gpu.free_mb()
    res = measure(args.model, args.compute, args.device, set(args.which.split(",")), clips)
    res["vram_free_before_mb"], res["vram_free_after_mb"] = free_before, gpu.free_mb()
    print(f"[measure] {args.model} {args.compute}: load {res['load_ms']} ms, warmup {res.get('warmup_ms')} ms, "
          f"VRAM free {free_before} -> {gpu.free_mb()} MB")
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=1)
        print(f"[measure] wrote {args.json}")


if __name__ == "__main__":
    main()

def timed(fn, *a, **kw):
    t = time.perf_counter()
    out = fn(*a, **kw)
    return out, (time.perf_counter() - t) * 1000
