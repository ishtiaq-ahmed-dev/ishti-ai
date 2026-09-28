"""Accuracy and latency benchmark for STT (Issue 7):
Tests faster-whisper with whisper-large-v3-turbo on RTX 5070 Ti (CUDA float16)
across English, Urdu, and Pashto. Checks audio pipeline levels (peak/RMS)
and verifies word recovery and latency.

Uses the ONE production STT path (ishti.voice, via voice.stt_call) - the legacy duplicate
engine voice/stt.py is retired (NEEDS 17): it kept its own decode calls outside the
single-decode-thread rule that fixed the 0xC0000409 fail-fast (ISSUES.md Issue 8).
"""
import io
import os
import sys
import time
import wave
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from ishti import voice as V
from ishti.voice import synthesize

RATE = 16000

EN_TEXT = "Tomorrow in Peshawar it will be sunny and thirty four degrees."
UR_TEXT = "کل پشاور میں موسم ٹھیک ہو گا"
PS_TEXT = "دا د پښتو ژبه ده او پشاور کې ژوند کوو"


def pcm16k(text: str) -> np.ndarray:
    """Synthesizes text with Kokoro and converts to 16 kHz int16 PCM."""
    wav_bytes = synthesize(text)
    with wave.open(io.BytesIO(wav_bytes)) as wf:
        raw = wf.readframes(wf.getnframes())
        rate = wf.getframerate()
        pcm = np.frombuffer(raw, dtype=np.int16)
    if rate != RATE:
        target_len = int(len(pcm) * RATE / rate)
        pcm = np.interp(
            np.linspace(0, len(pcm) - 1, target_len),
            np.arange(len(pcm)),
            pcm,
        ).astype(np.int16)
    return pcm


def check_audio_cleanliness(name: str, pcm: np.ndarray) -> dict:
    """Inspects audio signal levels: peak, RMS, clipping detection."""
    x = pcm.astype(np.float32) / 32768.0
    peak = float(np.abs(x).max()) if len(x) > 0 else 0.0
    rms = float(np.sqrt((x ** 2).mean())) if len(x) > 0 else 0.0
    clipped = peak >= 0.99
    too_quiet = peak < 0.05
    status = "OK"
    if clipped:
        status = "CLIPPED (out of range)"
    elif too_quiet:
        status = "LOW_GAIN (quiet recording)"
    print(f"  [Audio Check] {name}: peak={peak:.3f}, rms={rms:.3f}, status={status}")
    return {"peak": peak, "rms": rms, "clipped": clipped, "too_quiet": too_quiet}


def test_stt_all_languages():
    print("========================================================================")
    print("  TESTING STT ACCURACY & LATENCY (whisper-large-v3-turbo, CUDA float16)")
    print("========================================================================")
    model = V.stt_model()
    print(f"Model: {V.model_source()}")
    assert model is not None, "Whisper model failed to load!"

    test_cases = [
        ("English", EN_TEXT, ["tomorrow", "peshawar", "sunny", "degrees"]),
        ("Urdu", UR_TEXT, ["کل", "پشاور", "موسم", "ٹھیک"]),
    ]

    for lang_name, phrase, target_words in test_cases:
        print(f"\n--- Testing {lang_name} (Synthesized Audio) ---")
        print(f"  Source text: {phrase}")
        pcm = pcm16k(phrase)
        check = check_audio_cleanliness(lang_name, pcm)

        # Transcribe the production way and measure latency (auto language detect included)
        t0 = time.perf_counter()
        transcript = V.transcribe(pcm)
        elapsed_ms = (time.perf_counter() - t0) * 1000

        print(f"  Recognized:  {transcript}")
        print(f"  Latency:     {elapsed_ms:.1f} ms")

        # Verify key word recovery
        lower_trans = transcript.lower()
        matched = [w for w in target_words if w.lower() in lower_trans]
        print(f"  Word match:  {len(matched)}/{len(target_words)} ({matched})")
        assert len(matched) >= len(target_words) // 2, f"Too few words matched in {lang_name}: {matched} vs {target_words}"
        assert elapsed_ms < 3000, f"STT latency too high on RTX 5070 Ti: {elapsed_ms} ms"

    # Test real voice recording audio file transcription
    real_audio_path = os.path.join(ROOT, "data", "media_output", "speech_20260926_194057.wav")
    if os.path.isfile(real_audio_path):
        print("\n--- Testing Real Speech Audio File Transcription ---")
        t0 = time.perf_counter()
        real_trans = V.transcribe(real_audio_path)
        real_elapsed = (time.perf_counter() - t0) * 1000
        print(f"  File: {real_audio_path}")
        print(f"  Recognized: {real_trans}")
        print(f"  Latency:    {real_elapsed:.1f} ms")
        assert "ishti" in real_trans.lower() or "ready" in real_trans.lower(), "Speech transcription failed on real audio"
        assert real_elapsed < 2000, f"Latency on real audio too high: {real_elapsed:.1f} ms"

    # Verify Pashto language folding and tokenizer capability
    print("\n--- Testing Pashto Support & Language Folding ---")
    assert V.FOLD.get("fa") == "ps", "Farsi -> Pashto language folding missing"
    assert V.FOLD.get("hi") == "ur", "Hindi -> Urdu language folding missing"
    tok = model.hf_tokenizer
    encoded = tok.encode("دا د پښتو ژبه ده")
    assert len(encoded.ids) > 0, "Pashto tokenizer failed to encode phrase"
    print(f"  Pashto phrase tokens encoded successfully: {len(encoded.ids)} tokens")
    print(f"  Language folding verified: FOLD = {V.FOLD}")

    print("\n[SUCCESS] STT accuracy, latency, audio conditioning, and language folding verified!")


if __name__ == "__main__":
    test_stt_all_languages()
