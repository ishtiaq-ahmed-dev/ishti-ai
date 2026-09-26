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


# ---- voices: a roster in config (voice.voices), the owner picks one ("switch to the deep male voice") ----
_voices = {}                                           # piper model name -> loaded PiperVoice
TAG = re.compile(r"\[([a-z][a-z ]{1,20})\]", re.I)     # inline style tags, e.g. "Sure [pause] here it is"
PAUSES = {"pause": 0.35, "short pause": 0.2, "long pause": 0.8, "breath": 0.2}  # the rest are for expressive engines


def roster():
    """[{"name", "piper_voice", "length_scale", ...}]; the old single piper_voice if no roster is configured."""
    return _cfg.get("voices") or [{"name": "Default", "piper_voice": _cfg["piper_voice"]}]


def current_voice():
    """The owner's chosen voice (fact owner.voice, so it survives restarts), else default_voice, else the first."""
    from ishti.facts import get_facts
    by_name = {v["name"].lower(): v for v in roster()}
    chosen = (get_facts().get("owner").get("voice") or _cfg.get("default_voice") or "").lower()
    return by_name.get(chosen) or roster()[0]


def pick_voice(text):
    """The roster voice a request names: its full name, "male 2", or its words ("deep", "energetic", "female")."""
    t = text.lower()
    for v in roster():
        if v["name"].lower() in t:
            return v
    m = re.search(r"\b(male|female|voice)\s*(?:number\s*)?(\d)\b", t)
    if m:
        kind = [v for v in roster() if m.group(1) == "voice" or v["name"].lower().startswith(m.group(1) + " ")]
        return kind[int(m.group(2)) - 1] if 0 < int(m.group(2)) <= len(kind) else None
    words = set(re.findall(r"[a-z]+", t)) - {"voice", "the", "a", "to", "use", "your", "switch", "change", "male"}
    for v in roster():  # a describing word: "deep", "calm", "energetic", "clear", "neutral", "female"
        if words & set(re.findall(r"[a-z]+", v["name"].lower())):
            return v
    return next((v for v in roster() if v["name"].lower().startswith("male ")), None) if re.search(r"\bmale\b", t) else None


def tts_voice(profile=None):
    profile = profile or current_voice()
    with _load_lock:
        if profile["piper_voice"] not in _voices:
            from piper import PiperVoice
            _voices[profile["piper_voice"]] = PiperVoice.load(os.path.join(_cfg["piper_dir"], profile["piper_voice"] + ".onnx"))
    return _voices[profile["piper_voice"]]


def synthesize(text: str, profile=None) -> bytes:
    """WAV bytes of the spoken reply in the owner's voice (markdown, URLs and citations stripped; [pause] tags
    become short silences, other style tags are dropped)."""
    from piper import SynthesisConfig
    profile = profile or current_voice()
    pv = tts_voice(profile)
    cfg = SynthesisConfig(length_scale=profile.get("length_scale"), noise_scale=profile.get("noise_scale"))
    rate, pcm = pv.config.sample_rate, []
    for i, part in enumerate(TAG.split(text)):
        if i % 2:
            pcm.append(b"\0\0" * int(rate * PAUSES.get(part.lower().strip(), 0)))
        elif words := speakable(part):
            pcm += [chunk.audio_int16_bytes for chunk in pv.synthesize(words, cfg)]
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(b"".join(pcm))
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

    # the roster: every voice understandable and audibly different (pitch or pace), the choice survives a restart
    import numpy as np
    import tempfile
    import ishti.facts

    def pitch_and_length(wav):
        with wave.open(io.BytesIO(wav)) as wf:
            x, r = np.frombuffer(wf.readframes(wf.getnframes()), np.int16).astype(float), wf.getframerate()
        f0 = []
        for i in range(0, len(x) - 1024, 512):  # autocorrelation pitch of the voiced frames
            fr = x[i:i + 1024]
            if np.abs(fr).mean() > 500:
                ac = np.correlate(fr, fr, "full")[1023:]
                k = r // 400 + int(np.argmax(ac[r // 400:r // 60]))
                if ac[k] > 0.3 * ac[0]:
                    f0.append(r / k)
        return float(np.median(f0)), len(x) / r

    line = "Hello, I am ISHTI. Tomorrow in Peshawar it will be sunny and warm."
    measured = {}
    for v in roster():
        wav = synthesize(line, v)
        measured[v["name"]] = pitch_and_length(wav)
        heard = transcribe(io.BytesIO(wav)).lower()
        assert "peshawar" in heard and "sunny" in heard, (v["name"], heard)
    names = list(measured)
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            (pa, la), (pb, lb) = measured[a], measured[b]
            assert max(pa, pb) / min(pa, pb) >= 1.08 or max(la, lb) / min(la, lb) >= 1.06, (a, b, measured)
    assert pick_voice("use the deep male voice")["name"] == "Male 1 - Deep/Calm"
    assert pick_voice("switch to male 2")["name"] == "Male 2 - Energetic"
    assert pick_voice("change your voice to the female one")["name"] == "Female 1 - Neutral"
    assert pick_voice("use a nice voice") is None
    db = os.path.join(tempfile.mkdtemp(), "facts.db")
    ishti.facts._facts = ishti.facts.Facts(db)
    ishti.facts._facts.set("owner", "voice", "Male 2 - Energetic")
    ishti.facts._facts = ishti.facts.Facts(db)  # a restart: a fresh store on the same file
    assert current_voice()["name"] == "Male 2 - Energetic"
    plain, paused = synthesize("Sure. Here it is.", current_voice()), synthesize("Sure. [pause] Here it is.", current_voice())
    assert 0.25 < pitch_and_length(paused)[1] - pitch_and_length(plain)[1] < 0.5  # [pause] is a short silence, not words
    print("[self-check] voice OK - Piper TTS -> faster-whisper STT round trip; "
          + ", ".join(f"{n.split(' - ')[0]} {p:.0f} Hz" for n, (p, _) in measured.items())
          + "; all distinct, understood, choice persists, [pause] works")
