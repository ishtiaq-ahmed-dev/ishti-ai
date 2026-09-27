"""The owner's own voice for things sent in his name (WhatsApp voice notes).

ISHTI speaks the words with its normal voice (Kokoro: fast, and it speaks Urdu), then Chatterbox VC (Resemble AI, MIT)
converts that speech into the owner's voice from a sample he recorded himself (data/owner_voice/sample.wav; the
first 10 s are used). Only his own recorded voice is ever used as the target. Chatterbox adds its inaudible Perth
watermark, which marks the audio as AI-made.
Chatterbox's package pins torch 2.6, which can't drive this RTX 5070 Ti (sm_120): it's installed with --no-deps next
to ISHTI's torch 2.11, and only the converter half (no text-to-speech LLM) is used."""
import io
import os
import tempfile
import threading
import wave

import numpy as np

_vc, _lock = None, threading.Lock()
DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "clone")
FILES = ("s3gen.safetensors", "conds.pt")  # ~1 GB; Settings -> Voices -> engine files can delete / download them


def model():
    """Loaded once: on the GPU when ~2.5 GB is free (next to the planner), else on the CPU."""
    global _vc
    with _lock:
        if _vc is None:
            if not all(os.path.isfile(os.path.join(DIR, f)) for f in FILES):
                raise RuntimeError("the own-voice converter isn't downloaded (Settings -> Voices -> engine files)")
            import torch
            from chatterbox.vc import ChatterboxVC
            dev = "cuda" if torch.cuda.is_available() and torch.cuda.mem_get_info()[0] > 2.5e9 else "cpu"
            _vc = ChatterboxVC.from_local(DIR, dev)
    return _vc


def to_wav(samples, rate):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes((np.clip(samples, -1, 1) * 32767).astype(np.int16).tobytes())
    return buf.getvalue()


def convert(wav_bytes, sample_path):
    """WAV bytes (any voice) -> WAV bytes in the voice of sample_path."""
    src = os.path.join(tempfile.mkdtemp(), "src.wav")
    with open(src, "wb") as f:
        f.write(wav_bytes)
    vc = model()
    with _lock:  # one conversion at a time: the model keeps the target voice between calls
        out = vc.generate(src, target_voice_path=sample_path)
    return to_wav(out.squeeze(0).numpy(), vc.sr)


def similarity(a_path, b_path):
    """Cosine similarity of two recordings' speaker embeddings (SpeechBrain ECAPA, the diarization model)."""
    import torch
    from ishti.diarize import _pcm16k, encoder
    emb = [encoder().encode_batch(torch.from_numpy(_pcm16k(p))[None]).squeeze() for p in (a_path, b_path)]
    return float(torch.nn.functional.cosine_similarity(emb[0], emb[1], dim=0))


if __name__ == "__main__":
    # Self-check with two of ISHTI's own voices (no real person's voice): convert a female sentence into the male
    # "target" voice; the result must sound like the target speaker, not like the source.
    from ishti.voice import roster, synthesize
    voices = {v["name"]: v for v in roster()}
    female, male = voices["Female 1 - Neutral"], voices["Male 1 - Deep/Calm"]
    d = tempfile.mkdtemp()
    paths = {k: os.path.join(d, f"{k}.wav") for k in ("source", "target", "out")}
    open(paths["source"], "wb").write(synthesize("I will be home at eight tonight, please keep dinner ready.", female))
    open(paths["target"], "wb").write(synthesize("This is a reference recording of the speaker whose voice should be "
                                                 "used. It needs about ten seconds of clear speech, like this.", male))
    import time
    t = time.time()
    open(paths["out"], "wb").write(convert(open(paths["source"], "rb").read(), paths["target"]))
    took = time.time() - t
    to_target, to_source = similarity(paths["out"], paths["target"]), similarity(paths["out"], paths["source"])
    print(f"converted in {took:.1f}s on {model().device}; similarity to target {to_target:.2f}, to source {to_source:.2f}")
    assert to_target > to_source + 0.1, "the converted voice doesn't match the target speaker"
    print("[self-check] clone OK - converted speech matches the target speaker, not the source")
