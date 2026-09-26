"""Voice channel: faster-whisper STT on the GPU and Piper TTS, both local.
Voice is only another input channel: the transcript goes through dispatcher.handle like text."""
import io
import os
import re
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
    import numpy as np
    from faster_whisper import decode_audio
    pcm = audio if isinstance(audio, np.ndarray) else decode_audio(audio, sampling_rate=16000)
    model = stt_model()
    lang = pick_language(model, pcm)
    segments, _ = model.transcribe(pcm, beam_size=1, vad_filter=True, language=lang, initial_prompt=vocabulary() or None,
                                   condition_on_previous_text=False)
    # noise and very short sounds make whisper invent words ("Arabic", "Thank you."): keep only confident speech
    kept = [s for s in segments if s.no_speech_prob < 0.6 and s.avg_logprob > -1.0]
    text = " ".join(s.text.strip() for s in kept).strip()
    LAST.update(language=lang, confidence=(sum(s.avg_logprob for s in kept) / len(kept)) if kept else -9.0,
                seconds=len(pcm) / 16000)
    _last_use = time.time()
    _schedule_unload()
    return text


LAST = {"language": None, "confidence": 0.0, "seconds": 0.0}  # of the latest transcript (the voice loop reads it)
# The owner speaks only these. Whisper's own guess ranges over ~100 languages and picked Arabic for short/noisy audio;
# the close neighbours are folded in (spoken Hindi = Urdu; Persian is what whisper mistakes Pashto for).
LANGUAGES = ("en", "ur", "ps")
FOLD = {"hi": "ur", "fa": "ps"}


def pick_language(model, pcm):
    allowed = tuple(_cfg.get("languages", LANGUAGES))
    try:
        _, _, probs = model.detect_language(pcm, vad_filter=True)
    except Exception:
        return allowed[0]
    score = {code: 0.0 for code in allowed}
    for code, p in probs:
        code = FOLD.get(code, code)
        if code in score:
            score[code] += p
    return max(score, key=score.get)


def vocabulary():
    """Names whisper should expect (it heard "IMSciences" as "IIM sciences"): the owner, their places, projects."""
    try:
        from ishti.facts import get_facts
        f = get_facts()
        owner = f.get("owner")
        words = [owner.get(k) for k in ("name", "city", "university", "company", "country")]
        with f.lock:
            words += [e for (e,) in f.db.execute("SELECT DISTINCT entity FROM facts WHERE attr='location'")]
        words = [re.sub(r"^(?:the|project)\s+|\s+project$", "", str(w), flags=re.I) for w in words if w]
        seen = {}
        for w in ["ISHTI"] + words:
            seen.setdefault(w.lower(), w)
        return ", ".join(seen.values()) + "."
    except Exception:
        return ""


_unload_timer = None
_timer_lock = threading.Lock()


def _schedule_unload():
    global _unload_timer
    with _timer_lock:
        if _unload_timer is not None:
            _unload_timer.cancel()

        def unload():
            global _stt
            with _load_lock:
                if _stt is not None and time.time() - _last_use >= IDLE_UNLOAD_S:
                    _stt = None  # CTranslate2 frees its VRAM when the model object goes away
                    print("[voice] whisper unloaded after idle")

        _unload_timer = threading.Timer(IDLE_UNLOAD_S + 1, unload)
        _unload_timer.daemon = True
        _unload_timer.start()


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
    """Play on this PC's speakers through the one Talker (sentence by sentence, interruptible by barge-in);
    SAPI5 if Piper/the audio device is unavailable."""
    try:
        from ishti.talk import Sentences, talker
        Sentences(talker()).flush(text)
        if wait:
            talker().wait()
    except Exception as exc:
        print(f"[voice] Piper playback failed ({exc}); falling back to SAPI5")
        from voice.tts import speak as sapi_speak
        sapi_speak(speakable(text))


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
