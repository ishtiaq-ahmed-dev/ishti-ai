"""Accuracy test for the STT (issue 7): do the three languages the owner speaks survive the new model + pipeline?

English and Urdu are synthesized here with the project's own TTS (Kokoro, CPU - no VRAM next to the planner) and
resampled to 16 kHz int16 exactly like wake.pcm16k() does, because that is literally the audio the wake loop feeds.
Kokoro cannot speak Pashto, so the Pashto check uses a real recording instead:

  Google FLEURS ps_af (CC-BY-4.0), sentence id 1582 - https://huggingface.co/datasets/google/fleurs
  Downloaded once to D:\\ISHTI\\data\\models\\stt_samples\\fleurs_ps_af_1582.wav (16 kHz mono, ~5 s) together with
  its ground-truth transcription, so word survival can be scored instead of "some Arabic script appeared".

Before/after: large-v3 (the old config, int8_float16) vs large-v3-turbo (the new one), each in its own subprocess
so the two models never sit in VRAM together. The table shows seconds per 4 s clip for both code paths - the old
"detect, then decode" pair and the new single pass - plus the word match per language.

Run:  D:\\ISHTI\\venv\\Scripts\\python.exe D:\\ISHTI\\tests\\test_stt_accuracy.py
"""
import io
import json
import os
import subprocess
import sys
import time
import wave

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

RATE = 16000
SAMPLE_DIR = os.path.join(ROOT, "data", "models", "stt_samples")
PASHTO_URL = ("https://datasets-server.huggingface.co/cached-assets/google/fleurs/--/"
              "70bb2e84b976b7e960aa89f1c648e09c59f894dd/5/1387/ps_af/validation/1582.wav")
PASHTO_FILE = os.path.join(SAMPLE_DIR, "fleurs_ps_af_1582.wav")
PASHTO_TEXT = "د عبراني کورنۍ ډیری ژوند په آزاد اسمان لاندي تير شوي"   # FLEURS ground truth, kept with the audio
PASHTO_LETTERS = "ټځڅډړږښګڼۍې"   # letters Urdu does not use: their presence means whisper wrote Pashto, not Urdu

EN_TEXT = "Tomorrow in Peshawar it will be sunny and thirty four degrees."
UR_TEXT = "کل پشاور میں موسم ٹھیک ہو گا"
UR_WORDS = ("پشاور", "پشاوار", "پشاور", "موسم", "ٹھیک", "ٹهیک", "ٹہیک", "ہو")


def pcm16k(text: str) -> np.ndarray:
    """Kokoro (24 kHz) -> 16 kHz int16, exactly what wake.pcm16k() hands to the recognizer."""
    from ishti.voice import synthesize
    with wave.open(io.BytesIO(synthesize(text))) as wf:
        pcm, rate = np.frombuffer(wf.readframes(wf.getnframes()), np.int16), wf.getframerate()
    t = np.arange(0, len(pcm) / rate, 1 / RATE)
    return np.interp(t, np.arange(len(pcm)) / rate, pcm).astype(np.int16)


def peak_rms(pcm: np.ndarray) -> tuple[int, float]:
    """Levels of what we feed the model: a clipped (or near-silent) sample would make the test meaningless."""
    x = pcm.astype(np.float32) / 32768
    return int(np.abs(x).max() * 32767), float(np.sqrt((x ** 2).mean()))


def pashto_sample() -> np.ndarray:
    """The FLEURS clip, downloaded once (offline afterwards): a real Pashto sentence, not TTS."""
    if not os.path.isfile(PASHTO_FILE):
        import requests
        os.makedirs(SAMPLE_DIR, exist_ok=True)
        r = requests.get(PASHTO_URL, timeout=120)
        r.raise_for_status()
        with open(PASHTO_FILE, "wb") as f:
            f.write(r.content)
    with wave.open(PASHTO_FILE) as wf:
        assert wf.getframerate() == RATE and wf.getnchannels() == 1, "the sample must be 16 kHz mono"
        return np.frombuffer(wf.readframes(wf.getnframes()), np.int16)


def pad_to(pcm: np.ndarray, seconds: float) -> np.ndarray:
    """Exactly `seconds` long: the timing row must compare equal work, not equal sentences."""
    want = int(seconds * RATE)
    return pcm[:want] if len(pcm) >= want else np.pad(pcm, (0, want - len(pcm)))


def norm_words(text: str) -> list[str]:
    """Words for the match: punctuation and Arabic diacritics dropped, so a spelling variant still counts."""
    import re
    return [w for w in re.sub(r"[^\w\u0600-\u06FF]+", " ", text).split() if w]
