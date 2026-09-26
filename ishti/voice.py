"""Voice channel: faster-whisper STT on the GPU and Piper TTS, both local.
Voice is only another input channel: the transcript goes through dispatcher.handle like text."""
import io
import os
import threading
import time
import wave

from ishti import CONFIG
from voice.tts import speakable

_cfg = CONFIG["voice"]
_stt = _tts = None
_load_lock, _play_lock = threading.Lock(), threading.Lock()


def stt_model():
    global _stt
    with _load_lock:
        if _stt is None:
            import importlib.util
            torch_spec = importlib.util.find_spec("torch")
            if torch_spec:  # CTranslate2 needs cuBLAS 12 / cuDNN 9; torch's CUDA build ships them
                os.environ["PATH"] = os.path.join(os.path.dirname(torch_spec.origin), "lib") + os.pathsep + os.environ["PATH"]
            from faster_whisper import WhisperModel
            try:
                _stt = WhisperModel(_cfg["whisper_model"], device=_cfg["whisper_device"],
                                    compute_type=_cfg["whisper_compute"])
            except Exception as exc:  # no CUDA / out of VRAM: slower, but still local
                print(f"[voice] whisper on {_cfg['whisper_device']} failed ({exc}); using CPU int8")
                _stt = WhisperModel(_cfg["whisper_model"], device="cpu", compute_type="int8")
    return _stt


_last_use = 0.0
IDLE_UNLOAD_S = 600  # large-v3 holds ~1.6 GB of VRAM; next to the 14B LLM that spills memory and slows everything


def transcribe(audio) -> str:
    """audio: path or file-like in any format PyAV decodes (browser webm/opus, wav, mp3)."""
    global _last_use
    _last_use = time.time()
    segments, _ = stt_model().transcribe(audio, beam_size=1, vad_filter=True)
    _last_use = time.time()
    _schedule_unload()
    return " ".join(s.text.strip() for s in segments).strip()


def _schedule_unload():
    def unload():
        global _stt
        time.sleep(IDLE_UNLOAD_S + 1)
        with _load_lock:
            if _stt is not None and time.time() - _last_use >= IDLE_UNLOAD_S:
                _stt = None  # CTranslate2 frees its VRAM when the model object goes away
                print("[voice] whisper unloaded after idle")
    threading.Thread(target=unload, daemon=True).start()


def tts_voice():
    global _tts
    with _load_lock:
        if _tts is None:
            from piper import PiperVoice
            _tts = PiperVoice.load(os.path.join(_cfg["piper_dir"], _cfg["piper_voice"] + ".onnx"))
    return _tts


def synthesize(text: str) -> bytes:
    """WAV bytes of the spoken reply (markdown, URLs and citations stripped)."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        tts_voice().synthesize_wav(speakable(text), wf)
    return buf.getvalue()


def speak(text: str, wait: bool = False):
    """Play on this PC's speakers (in the background unless wait); SAPI5 if Piper is unavailable."""
    def play():
        with _play_lock:
            try:
                import numpy as np
                import sounddevice as sd
                with wave.open(io.BytesIO(synthesize(text))) as wf:
                    audio = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16)
                    sd.play(audio, wf.getframerate())
                sd.wait()
            except Exception as exc:
                print(f"[voice] Piper playback failed ({exc}); falling back to SAPI5")
                from voice.tts import speak as sapi_speak
                sapi_speak(speakable(text))
    t = threading.Thread(target=play, daemon=True)
    t.start()
    if wait:
        t.join()


if __name__ == "__main__":
    import time
    t = time.time()
    wav = synthesize("Tomorrow in Peshawar: thirty four degrees, mostly clear.")
    assert wav[:4] == b"RIFF" and len(wav) > 20000, len(wav)
    print(f"piper: {len(wav)} bytes in {time.time() - t:.1f}s")
    t = time.time()
    heard = transcribe(io.BytesIO(wav)).lower()  # round trip: our own TTS through our own STT
    print(f"whisper: {heard!r} in {time.time() - t:.1f}s")
    assert "peshawar" in heard and "clear" in heard, heard
    print("[self-check] voice OK - Piper TTS -> faster-whisper STT round trip")
