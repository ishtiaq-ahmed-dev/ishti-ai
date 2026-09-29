"""Voice channel: faster-whisper STT on the GPU and Piper TTS, both local.
Voice is only another input channel: the transcript goes through dispatcher.handle like text.

How the STT is shaped, and why (measured - see tests/measure_stt.py):
  * large-v3-turbo at float16, warm: 4 decoder layers instead of large-v3's 32 - the decoder was most of the
    seconds per utterance (including a 20453 ms outlier in data/logs/server.log).
  * one decode per utterance: the language is picked *once* and then handed to transcribe(). Passing
    language=None costs a second encoder pass inside faster-whisper (detect_language encodes the audio, then
    transcribe encodes it again), and so did the old pick_language() + transcribe() pair.
  * the whisper encoder always runs one padded 30 s window, so a call costs roughly the same for 2 s and 25 s of
    audio; only the decoder grows with the words. That is why live partials (Stream) decode a bounded tail and
    why long audio goes through the batched pipeline, which encodes its 30 s chunks side by side.
  * the model stays loaded (whisper_idle_unload_s): a warm decode is ~0.3 s, a cold load is seconds of disk plus
    first-call kernel setup, and the wake loop needs it the moment the owner stops talking.
  * one thread decodes it (stt_call): CTranslate2's CUDA/cuBLAS state belongs to the thread that decoded, and a
    process that decoded the same model from two threads fails fast (0xC0000409 in ucrtbase, logged as an
    "Application Error" crash and a non-zero exit code) the moment it ends - measured on this PC 2026-09-27, always
    reproducible with one partial in a worker thread plus the final decode on the command thread. Every decode - the
    partials, the language pick, the warm-ups, the unload - runs on one dedicated worker now, which also serialises
    the GPU instead of letting a partial queue behind the final decode.
"""
import io
import os
import queue
import re
import threading
import time
import wave

import numpy as np

from ishti import CONFIG
from voice.tts import speakable

RATE = 16000                             # every caller feeds 16 kHz mono (wake.py, Live mode, /api/stt, voice notes)
_cfg = CONFIG["voice"]
_stt = _batched = _tts = None
_load_lock, _play_lock = threading.Lock(), threading.Lock()

# ---- the one STT thread -----------------------------------------------------------------------------------
# Decodes of the whisper model never run on a caller's thread: see the module docstring - a second decoding thread
# makes the whole process fail fast at exit. Callers submit a function and wait for its result.
_stt_thread, _stt_queue, _stt_exec = None, None, threading.Lock()
DECODE_THREADS = set()  # the threads decodes actually ran on (the self-check asserts there is exactly one)


def _run(fn):
    DECODE_THREADS.add(threading.get_ident())  # what the self-check asserts: exactly one thread, ever
    return fn()


def _stt_worker():
    while True:
        fn, box, done = _stt_queue.get()
        try:
            box.append((True, _run(fn)))
        except BaseException as exc:  # re-raised in the caller, where the caller can handle it
            box.append((False, exc))
        finally:
            done.set()


def stt_call(fn):
    """Run fn() - a decode or any other touch of the whisper model - on the single STT thread, and return its result.
    Called on that thread already (a decode calling stt_model/batched_model): runs inline, no deadlock possible."""
    global _stt_thread, _stt_queue
    if threading.current_thread() is _stt_thread:
        return _run(fn)
    with _stt_exec:
        if _stt_thread is None or not _stt_thread.is_alive():
            _stt_queue = queue.Queue()
            _stt_thread = threading.Thread(target=_stt_worker, name="stt", daemon=True)
            _stt_thread.start()
    box, done = [], threading.Event()
    _stt_queue.put((fn, box, done))
    done.wait()
    ok, value = box[0]
    if not ok:
        raise value
    return value


def _drop_model():
    global _stt
    _stt = None  # CTranslate2 frees its VRAM when the model object goes away


def is_stt_loaded() -> bool:
    return _stt is not None


def release_model():
    """Drop the loaded STT model (Settings -> a different whisper model). Freed on the STT thread that allocated it."""
    stt_call(_drop_model)
    try:
        from ishti import models
        models.log_event("unload", "whisper", "manual release")
    except Exception:
        pass


def evict_idle(idle_timeout_s=None) -> bool:
    """Strict GPU eviction for Whisper STT model when idle (Section 6)."""
    global _stt
    idle = idle_timeout_s if idle_timeout_s is not None else IDLE_UNLOAD_S
    if _stt is not None and (idle == 0 or time.time() - _last_use >= idle):
        stt_call(_drop_model)
        try:
            from ishti import models
            models.log_event("unload", "whisper", f"idle eviction (timeout={idle}s)")
        except Exception:
            pass
        return True
    return False


def model_source():
    """(what WhisperModel loads, one-line report or None): the local turbo directory when it is on disk, otherwise
    the configured fallback name - a missing download degrades to slow-but-working, it must never kill the voice
    channel (and a fresh clone has no model directory at all)."""
    ref = _cfg.get("whisper_model", "")
    if ref and os.path.isdir(ref):
        return ref, None
    fallback = _cfg.get("whisper_model_fallback") or ref
    return fallback, (f"[voice] {ref} is not on disk - falling back to '{fallback}' "
                      f"(run tests/download_whisper_turbo.py for the fast one)")


def _warm(model):
    """The first decode on a fresh CTranslate2 model pays kernel/allocator setup. Do it here, at load time, instead
    of inside the owner's first command."""
    t = time.time()
    stt_call(lambda: " ".join(s.text for s in model.transcribe(np.zeros(RATE // 2, np.float32), language="en", beam_size=1,
                                                               vad_filter=False, without_timestamps=True,
                                                               temperature=0.0)[0]))
    print(f"[voice] whisper warm ({time.time() - t:.1f}s)")


def stt_model():
    global _stt
    created = None
    with _load_lock:
        if _stt is None:
            import importlib.util
            torch_spec = importlib.util.find_spec("torch")
            if torch_spec:  # CTranslate2 needs cuBLAS 12 / cuDNN 9; torch's CUDA build ships them
                os.environ["PATH"] = os.path.join(os.path.dirname(torch_spec.origin), "lib") + os.pathsep + os.environ["PATH"]
            from faster_whisper import WhisperModel
            from ishti import gpu
            ref, note = model_source()
            if note:
                print(note)
            try:
                # measured 29 Sep on the RTX 5070 Ti: loading turbo float16 grew the GPU by 3047 MiB (weights ~1.6 GB plus its
                # CUDA context and cuBLAS/cuDNN workspace) - the old 1.1 GB figure left the planner on top of it at 84% VRAM.
                # The governor makes room (the planner goes a rung lighter: voice has priority) and re-learns this from now on
                with gpu.slot(3000, "speech recognition loading", for_voice=True):
                    _stt = WhisperModel(ref, device=_cfg["whisper_device"], compute_type=_cfg["whisper_compute"])
            except Exception as exc:  # no CUDA / out of VRAM: slower, but still local
                print(f"[voice] whisper on {_cfg['whisper_device']} failed ({exc}); using CPU int8")
                _stt = WhisperModel(ref, device="cpu", compute_type="int8")  # int8: the documented CPU fallback
            created = _stt
    if created is not None:
        from ishti import models
        models.log_event("load", "whisper", f"device={_cfg['whisper_device']}")
        _warm(created)  # on the STT thread, and outside the lock: a decode must never wait for this lock
    return _stt


def batched_model():
    """BatchedInferencePipeline over the same model: its 30 s chunks are encoded and decoded in one batch per
    batch_size chunks. Only worth it for audio far longer than one chunk (voice notes, recordings) - measured
    below BATCH_OVER_S. Warms itself once (batched kernels differ from the single-chunk path)."""
    global _batched
    model = stt_model()
    made = None
    with _load_lock:
        if _batched is None or _batched.model is not model:
            from faster_whisper import BatchedInferencePipeline
            _batched = BatchedInferencePipeline(model=model)
            made = _batched
    if made is not None:
        stt_call(lambda: [s.text for s in made.transcribe(np.zeros(RATE // 2, np.float32), language="en", beam_size=1,
                                                          batch_size=8)[0]])
    return _batched


_last_use = 0.0
# Measured: a warm turbo decode is ~0.3 s, a cold load costs seconds of disk + kernel warm-up, and the wake loop
# wants the model the moment the owner stops talking. Whisper's ~1.1 GB is VRAM the 14B planner would like back, so
# the trade is: keep it while the owner is working with ISHTI, drop it when he walks away (config, in seconds).
IDLE_UNLOAD_S = int(_cfg.get("whisper_idle_unload_s", 600))


_final_active = threading.Event()        # a final decode is running: partials must not queue GPU work behind it
BATCH_OVER_S = float(_cfg.get("whisper_batch_over_s", 8))  # measured: batched wins clearly from ~2 chunks (60 s)


def transcribe(audio, language: str | None = None) -> str:
    """audio: path or file-like in any format PyAV decodes (browser webm/opus, wav, mp3), or 16 kHz pcm
    (float32 in [-1, 1] or int16). language: the language when the caller already knows it (Stream detects it while
    the owner is still speaking) - that saves the extra encoder pass the old code spent detecting first."""
    global _last_use
    _last_use = time.time()
    from ishti import models, vram
    vram.touch_voice()  # a voice turn is running: the governor keeps the planner where it is
    models.used("whisper")
    from faster_whisper import decode_audio
    pcm = audio if isinstance(audio, np.ndarray) else decode_audio(audio, sampling_rate=RATE)
    pcm = pcm.astype(np.float32) / 32768 if pcm.dtype == np.int16 else pcm.astype(np.float32)
    lang = FOLD.get(language, language) if language else None
    model = stt_model()
    if lang is None:
        # one encoder pass over the first 30 s window; this is what maps hi->ur and fa->ps through FOLD (whisper
        # hears spoken Hindi as Hindi and Pashto as Persian), and it is measured at ~0.2 s - it never shows up.
        lang = pick_language(model, pcm)
    beam = int(_cfg.get("whisper_beam", 1))
    prompt = vocabulary() or None

    def decode():
        if len(pcm) > BATCH_OVER_S * RATE:  # long audio: 30 s chunks in parallel instead of one after the other
            segments, _ = batched_model().transcribe(pcm, language=lang, beam_size=beam, batch_size=8,
                                                     vad_filter=True, initial_prompt=prompt, temperature=0.0)
        else:
            segments, _ = model.transcribe(pcm, language=lang, beam_size=beam, vad_filter=True, initial_prompt=prompt,
                                           without_timestamps=True, multilingual=False, temperature=0.0,
                                           condition_on_previous_text=False)  # temperature=0: no 5-step retry loop
        # noise and very short sounds make whisper invent words ("Arabic", "Thank you."): keep only confident speech.
        # The iteration happens here: faster-whisper yields lazily, so the decode is this thread's work.
        kept = [s for s in segments if s.no_speech_prob < 0.6 and s.avg_logprob > -1.0]
        return " ".join(s.text.strip() for s in kept).strip(), kept
    _final_active.set()
    try:
        text, kept = stt_call(decode)
    finally:
        _final_active.clear()
    LAST.update(language=lang, confidence=(sum(s.avg_logprob for s in kept) / len(kept)) if kept else -9.0,
                seconds=len(pcm) / RATE)
    _last_use = time.time()
    _schedule_unload()
    return text


LAST = {"language": None, "confidence": 0.0, "seconds": 0.0}  # of the latest transcript (the voice loop reads it)
# The owner speaks only these. Whisper's own guess ranges over ~100 languages and picked Arabic for short/noisy audio;
# the close neighbours are folded in (spoken Hindi = Urdu; Persian is what whisper mistakes Pashto for).
LANGUAGES = ("en", "ur", "ps")
FOLD = {"hi": "ur", "fa": "ps"}
_forced_language = _cfg.get("forced_language", None)


def set_forced_language(lang: str | None):
    """Manual override (Section 5): lock STT language or return to auto-detect."""
    global _forced_language
    norm = (lang or "").strip().lower()
    if norm in ("en", "english"):
        _forced_language = "en"
    elif norm in ("ur", "urdu"):
        _forced_language = "ur"
    elif norm in ("ps", "pashto"):
        _forced_language = "ps"
    else:
        _forced_language = None
    print(f"[voice] Language mode set to: {_forced_language or 'auto-detect (primary biased)'}")


def get_forced_language() -> str | None:
    return _forced_language


def score_language_probs(probs: list) -> str:
    """Evaluate detected language probabilities according to primary bias and switch threshold (Section 5)."""
    if _forced_language in ("en", "ur", "ps"):
        return _forced_language

    allowed = tuple(_cfg.get("languages", LANGUAGES))
    primary = _cfg.get("primary_language", "en")
    bias = float(_cfg.get("primary_language_bias", 0.25))
    switch_thresh = float(_cfg.get("language_switch_threshold", 0.60))

    score = {code: 0.0 for code in allowed}
    for code, p in probs:
        code = FOLD.get(code, code)
        if code in score:
            score[code] += p

    total = sum(score.values())
    if total > 0:
        for k in score:
            score[k] /= total

    # Check candidates other than primary
    others = [k for k in allowed if k != primary]
    best_other = max(others, key=score.get) if others else None

    # Switch away from primary ONLY when detection confidence is clearly high
    if best_other and score[best_other] >= switch_thresh and score[best_other] > (score.get(primary, 0.0) + bias):
        return best_other

    return primary


def pick_language(model, pcm):
    """Confidence-threshold tuning with primary language bias (Section 5).
    English is the default primary language. Only switch to Urdu or Pashto
    when detection confidence clearly exceeds the switch threshold and margin."""
    if _forced_language in ("en", "ur", "ps"):
        return _forced_language

    try:
        probs = stt_call(lambda: model.detect_language(pcm, vad_filter=True)[2])  # a decode: the STT thread
    except Exception:
        primary = _cfg.get("primary_language", "en")
        allowed = tuple(_cfg.get("languages", LANGUAGES))
        return primary if primary in allowed else allowed[0]

    return score_language_probs(probs)


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

# ---- streaming: the live transcript while the owner is still speaking -----------------------------------------
class Stream:
    """Incremental transcript of one utterance: partial() for the dashboard's live text, final() for the command.

    Why this shape: the old live path re-ran the *whole* accurate decode every ~1 s on the growing buffer, each
    one paying VAD plus its own language detection - two encoder passes per partial, all queued behind each other
    on the one GPU. Six partials for a six second command meant ~14 encoder passes before the final decode even
    started, which is exactly how "[latency] stt 20453 ms" happens. So a partial here
      * never runs while final() (or any final transcribe) owns the GPU - fast text beats queueing behind the answer,
      * reuses the language detected once (the first partial), so the final decode needs a single encoder pass,
      * decodes without VAD, without the vocabulary prompt and without timestamps,
      * decodes only the last TAIL_S seconds (the encoder cost is fixed, the decoder's grows with the words).

    MIN_S bounds the work for a stray quarter second of noise (the VAD hands partials over only after speech is
    heard, but a cough reaches here): below it there is not enough audio to decode or to trust a language.
    """

    TAIL_S = float(_cfg.get("whisper_partial_tail_s", 12))
    MIN_S = 0.5  # do not even try below half a second: too little audio decides the language at random

    def __init__(self, language: str | None = None):
        self.language = language
        self.text = ""
        self._lock = threading.Lock()

    @staticmethod
    def _pcm(pcm):
        pcm = np.asarray(pcm)
        return pcm.astype(np.float32) / 32768 if pcm.dtype == np.int16 else pcm.astype(np.float32)

    def partial(self, pcm) -> str:
        """The utterance so far -> its last TAIL_S seconds as text (empty when it is too short to say anything)."""
        if _final_active.is_set():  # the accurate decode is using the GPU: stale text beats queueing behind it
            return self.text
        pcm = self._pcm(pcm)
        if len(pcm) < self.MIN_S * RATE:
            return self.text
        tail, model = pcm[-int(self.TAIL_S * RATE):], stt_model()
        if self.language is None:
            self.language = pick_language(model, tail)  # once per utterance, while the owner is still speaking

        def decode():
            segments, _ = model.transcribe(tail, language=self.language, beam_size=1, vad_filter=False,
                                           without_timestamps=True, multilingual=False, temperature=0.0,
                                           condition_on_previous_text=False)
            return [s.text.strip() for s in segments if s.no_speech_prob < 0.6 and s.avg_logprob > -1.0]
        kept = stt_call(decode)
        with self._lock:
            self.text = " ".join(kept).strip()
        return self.text

    def final(self, pcm) -> str:
        """The finished utterance through the accurate path: VAD, vocabulary prompt, confidence filter, telemetry."""
        return transcribe(pcm, language=self.language)




_unload_timer = None
_timer_lock = threading.Lock()


def _schedule_unload():
    global _unload_timer
    with _timer_lock:
        if _unload_timer is not None:
            _unload_timer.cancel()

        from ishti import models
        idle = IDLE_UNLOAD_S  # Strict 10-minute idle timer (Section 6)

        def unload():
            # the drop itself runs on the STT thread: the VRAM is freed by the thread that allocated it
            gone = stt_call(lambda: _stt is not None and time.time() - _last_use >= idle and (_drop_model() or True))
            if gone:
                models.log_event("unload", "whisper", f"idle > {idle // 60}m")
                print(f"[voice] whisper unloaded after {idle // 60}m idle")

        _unload_timer = threading.Timer(idle + 1, unload)
        _unload_timer.daemon = True
        _unload_timer.start()


# ---- voices: a roster in config (voice.voices), the owner picks one ("switch to the deep male voice") ----
_voices = {}                                           # piper model name -> loaded PiperVoice
TAG = re.compile(r"\[([a-z][a-z ]{1,20})\]", re.I)     # inline style tags, e.g. "Sure [pause] here it is"
PAUSES = {"pause": 0.35, "short pause": 0.2, "long pause": 0.8, "breath": 0.2}  # the rest are for expressive engines


def roster():
    """[{"name", "piper_voice", "length_scale", ...}]; the old single piper_voice if no roster is configured."""
    return _cfg.get("voices") or [{"name": "Default", "piper_voice": _cfg["piper_voice"]}]


def voice_files(profile):
    """The files a voice needs on disk (for "installed?", delete and download in Settings)."""
    if profile.get("engine") == "kokoro":
        d = _cfg.get("kokoro_dir", os.path.join(os.path.dirname(_cfg["piper_dir"]), "kokoro"))
        return [os.path.join(d, _cfg.get("kokoro_model", "kokoro-v1.0.onnx")), os.path.join(d, "voices-v1.0.bin")]
    if profile.get("engine") == "s2":
        return [os.path.join(os.path.dirname(_cfg["piper_dir"]), "s2", "models", "s2-pro-q4_k_m.gguf")]
    base = os.path.join(_cfg["piper_dir"], profile["piper_voice"])
    return [base + ".onnx", base + ".onnx.json"]


def installed(profile):
    return all(os.path.isfile(f) for f in voice_files(profile))


def current_voice():
    """The owner's chosen voice (fact owner.voice, so it survives restarts), else default_voice, else the first
    installed one - a voice deleted in Settings never leaves ISHTI mute."""
    from ishti.facts import get_facts
    by_name = {v["name"].lower(): v for v in roster()}
    chosen = (get_facts().get("owner").get("voice") or _cfg.get("default_voice") or "").lower()
    pick = by_name.get(chosen) or by_name.get((_cfg.get("default_voice") or "").lower()) or roster()[0]
    if installed(pick):
        return pick
    return next((v for v in roster() if installed(v) and v.get("live", True)), pick)


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
    # describing words ("deep", "british", "female"...): the voice sharing the most of them ("british female" -> Female 2)
    best = max(roster(), key=lambda v: len(words & set(re.findall(r"[a-z]+", v["name"].lower()))))
    if words & set(re.findall(r"[a-z]+", best["name"].lower())):
        return best
    return next((v for v in roster() if v["name"].lower().startswith("male ")), None) if re.search(r"\bmale\b", t) else None


_kokoro_model = {}
URDU_SCRIPT = re.compile(r"[\u0600-\u06FF]")
PASHTO_LETTERS = re.compile(r"[ټځڅډړږښګڼۍې]")


def kokoro(profile):
    """The Kokoro model (82M params, ONNX on the CPU - no VRAM; ~4x faster than real time here), loaded once."""
    from kokoro_onnx import Kokoro
    model, voices = voice_files(profile)
    with _load_lock:
        if model not in _kokoro_model:
            _kokoro_model.clear()  # one model build at a time (fp32 or int8, whichever Settings picked)
            _kokoro_model[model] = Kokoro(model, voices)
    return _kokoro_model[model]


def _kokoro_pcm(words, profile):
    """(int16 bytes, rate). Urdu/Pashto text is spoken with a Hindi-trained voice through the Urdu phonemizer
    (Whisper hears it as correct Urdu); English with the profile's own voice."""
    k = kokoro(profile)
    if URDU_SCRIPT.search(words):
        male = profile.get("gender", "female") == "male"
        voice, lang = profile.get("urdu_voice") or ("hm_omega" if male else "hf_alpha"), "ur"
    else:
        voice, lang = profile["voice"], profile.get("lang", "en-us")
    audio, rate = k.create(words, voice=voice, speed=profile.get("speed", 1.0), lang=lang)
    loud = np.nonzero(np.abs(audio) > 0.01)[0]
    if len(loud):  # Kokoro pads each piece with silence: keep 30 ms before and 80 ms after, so a [pause] stays subtle
        audio = audio[max(0, loud[0] - int(0.03 * rate)):min(len(audio), loud[-1] + int(0.08 * rate))]
    return (np.clip(audio, -1, 1) * 32767).astype(np.int16).tobytes(), rate


def tts_voice(profile=None):
    profile = profile or current_voice()
    with _load_lock:
        if profile["piper_voice"] not in _voices:
            from piper import PiperVoice
            _voices[profile["piper_voice"]] = PiperVoice.load(os.path.join(_cfg["piper_dir"], profile["piper_voice"] + ".onnx"))
    return _voices[profile["piper_voice"]]


OWNER_SAMPLE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "owner_voice", "sample.wav")


def synthesize_as_owner(text):
    """(WAV bytes, whose voice it is) for things sent in the owner's name (WhatsApp voice notes). His own voice when
    he has recorded a sample (Settings -> Voices) and the voice converter is installed; else ISHTI's current voice."""
    wav = synthesize(text)
    if os.path.isfile(OWNER_SAMPLE):
        try:
            from ishti.clone import convert
            return convert(wav, OWNER_SAMPLE), "your own voice"
        except Exception as exc:  # converter missing/failed: still send, in ISHTI's voice, and say so
            print(f"[voice] own-voice conversion failed: {type(exc).__name__}: {exc}")
    return wav, f"ISHTI's voice ({current_voice()['name']}) - record your voice in Settings -> Voices to use your own"


def synthesize(text: str, profile=None) -> bytes:
    """WAV bytes of the spoken reply in the owner's voice (markdown, URLs and citations stripped; [pause] tags
    become short silences, other style tags are dropped)."""
    from ishti import vram
    vram.touch_voice()
    profile = profile or current_voice()
    is_urdu_script = bool(URDU_SCRIPT.search(text))
    engine = "kokoro" if is_urdu_script else profile.get("engine", "piper")
    if engine == "piper":
        from piper import SynthesisConfig
        pv = tts_voice(profile)
        cfg = SynthesisConfig(length_scale=profile.get("length_scale"), noise_scale=profile.get("noise_scale"))
        rate = pv.config.sample_rate
    else:
        rate = 24000  # Kokoro's rate
    pcm = []
    for i, part in enumerate(TAG.split(text)):
        if i % 2:
            pcm.append(b"\0\0" * int(rate * PAUSES.get(part.lower().strip(), 0)))
        elif words := speakable(part):
            if engine == "kokoro" or URDU_SCRIPT.search(words):
                pcm.append(_kokoro_pcm(words, profile)[0])
            else:
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
    from ishti.talk import is_voice_allowed
    if not is_voice_allowed():
        return
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

    # streaming (issue 7): a partial must come from the growing buffer, pick the language once, and the final decode
    # must then run with that language - one encoder pass instead of the two the old live path paid per second
    with wave.open(io.BytesIO(wav)) as wf:
        src, rate = np.frombuffer(wf.readframes(wf.getnframes()), np.int16), wf.getframerate()
    pcm = np.interp(np.arange(0, len(src) / rate, 1 / RATE), np.arange(len(src)) / rate, src).astype(np.int16)
    live = Stream()
    t = time.time()
    live.partial(pcm[: RATE])  # the first second: only there to pick the language
    assert live.language in LANGUAGES, live.language
    said = live.partial(pcm)
    part_ms = (time.time() - t) * 1000
    t = time.time()
    streamed = live.final(pcm).lower()
    final_ms = (time.time() - t) * 1000
    assert "peshawar" in streamed and "clear" in streamed, streamed
    assert live.language == "en" and LAST["language"] == "en", (live.language, LAST["language"])
    print(f"stream: partial+final {part_ms:.0f}/{final_ms:.0f} ms -> {streamed!r} (language {live.language})")
    # the fail-fast guard: decodes of one CTranslate2 model must all happen on the same thread (see the docstring)
    assert len(DECODE_THREADS) == 1, f"whisper decoded on {len(DECODE_THREADS)} threads: {DECODE_THREADS}"

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

    def timbre(wav):
        """Long-term average spectrum in log bands, normalised: the voice's tonal fingerprint."""
        with wave.open(io.BytesIO(wav)) as wf:
            x, r = np.frombuffer(wf.readframes(wf.getnframes()), np.int16).astype(float), wf.getframerate()
        x = np.interp(np.arange(0, len(x) / r, 1 / 16000), np.arange(len(x)) / r, x)
        frames = [x[i:i + 512] * np.hanning(512) for i in range(0, len(x) - 512, 256) if np.abs(x[i:i + 512]).mean() > 300]
        spec = np.mean([np.abs(np.fft.rfft(f)) for f in frames], axis=0)
        edges = np.unique(np.geomspace(3, 256, 41).astype(int))
        bands = np.log([spec[a:b].mean() + 1e-6 for a, b in zip(edges[:-1], edges[1:])])
        return (bands - bands.mean()) / (bands.std() + 1e-9)

    line = "Hello, I am ISHTI. Tomorrow in Peshawar it will be sunny and warm."
    measured = {}
    for v in roster():
        wav = synthesize(line, v)
        measured[v["name"]] = (*pitch_and_length(wav), timbre(wav))
        heard = transcribe(io.BytesIO(wav)).lower()
        assert "peshawar" in heard and "sunny" in heard, (v["name"], heard)
    names = list(measured)
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            (pa, la, ta), (pb, lb, tb) = measured[a], measured[b]
            apart = float(np.mean((ta - tb) ** 2))  # pitch, pace or timbre: at least one clearly different
            assert max(pa, pb) / min(pa, pb) >= 1.08 or max(la, lb) / min(la, lb) >= 1.06 or apart >= 0.04, (a, b, apart)
    assert pick_voice("use the deep male voice")["name"] == "Male 1 - Deep/Calm"
    assert pick_voice("switch to male 2")["name"] == "Male 2 - Energetic"
    assert pick_voice("change your voice to the female one")["name"] == "Female 1 - Neutral"
    assert pick_voice("use the british female voice")["name"] == "Female 2 - British"
    assert pick_voice("use a nice voice") is None
    db = os.path.join(tempfile.mkdtemp(), "facts.db")
    ishti.facts._facts = ishti.facts.Facts(db)
    ishti.facts._facts.set("owner", "voice", "Male 2 - Energetic")
    ishti.facts._facts = ishti.facts.Facts(db)  # a restart: a fresh store on the same file
    assert current_voice()["name"] == "Male 2 - Energetic"
    urdu = synthesize("کل پشاور میں موسم اچھا ہوگا۔", current_voice())
    assert "پشاور" in transcribe(io.BytesIO(urdu)) or "پشاوار" in transcribe(io.BytesIO(urdu))  # Kokoro speaks Urdu
    def longest_silence(wav):
        with wave.open(io.BytesIO(wav)) as wf:
            x, r = np.abs(np.frombuffer(wf.readframes(wf.getnframes()), np.int16)), wf.getframerate()
        best = run = 0
        for q in x[int(0.2 * r):-int(0.2 * r)] < 300:
            run = run + 1 if q else 0
            best = max(best, run)
        return best / r
    gap = longest_silence(synthesize("Sure. [pause] Here it is.", current_voice()))
    assert 0.3 < gap < 0.7 and longest_silence(synthesize("Sure. Here it is.", current_voice())) < 0.3, gap  # a subtle pause
    print("[self-check] voice OK - Piper TTS -> faster-whisper STT round trip; "
          + ", ".join(f"{n.split(' - ')[0]} {p:.0f} Hz" for n, (p, _, _) in measured.items())
          + "; all distinct, understood, choice persists, [pause] works")
