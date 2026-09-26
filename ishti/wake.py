"""Hands-free, real-time voice: openWakeWord ("hey jarvis" model, CPU) -> record until Silero VAD hears silence
(live partial transcripts on the dashboard) -> faster-whisper -> dispatcher -> reply spoken sentence by sentence
while it is still being written (talk.Talker).

Barge-in: the microphone stays open while ISHTI thinks and speaks. Sustained speech (not a cough: ~120 ms of VAD
speech) pauses the voice at once; a quick transcript of what was heard is compared word by word with what ISHTI is
saying - its own voice through the speakers resumes, anything else stops the reply, cancels the generation, keeps
what was being said (so "continue" picks it up) and records the new command.

States broadcast to the dashboard: idle (waiting for the wake word), listening, thinking, speaking."""
import collections
import re
import threading
import time

import numpy as np

from ishti import CONFIG

RATE, FRAME = 16000, 1280                 # openWakeWord works on 80 ms frames of 16 kHz int16
BLOCK = 640                               # mic read size: 40 ms, the barge-in detector's resolution
PRE_ROLL = 6                              # keep 0.5 s before the trigger: the command often follows in one breath
END_SILENCE, NO_SPEECH, MAX_LEN = 10, 50, 150   # frames: 0.8 s quiet ends it, 4 s of nothing cancels, 12 s cap
PARTIAL_EVERY = 12                        # frames (~1 s) between live partial transcripts
# Regex strips the wake phrase. Covers common Whisper mis-transcriptions of "hey ishti".
WAKE_TEXT = re.compile(r"^\W*(?:hey|hi|ok|okay)?\W*(?:jarvis|sarvis|ishti|ishty|travis|jessica|istie|ishi|isti|yishti|"
                       r"my\s+assistant)\b[,.:!?]*\W*", re.I)
# Detects if Whisper heard a wrong name (not "ishti") so we can correct with humour.
_WRONG_NAME = re.compile(r"\b(?:jarvis|travis|jessica|alexa|siri|cortana|google)\b", re.I)
CONTINUE = re.compile(r"^\W*(?:continue|go on|carry on|keep going|resume|as you were saying)\W*$", re.I)
BARGE = {"enabled": True, "vad_threshold": 0.6, "min_ms": 120, "check_ms": 700, "echo_overlap": 0.5, "cooldown_s": 1.0}
BARGE.update(CONFIG["voice"].get("barge_in", {}))


# Real short commands; any other 1-2 word utterance ("Google.", "Stay on.", "at 4.") is a fragment of room noise or
# half a sentence - acting on it started pointless web searches.
SHORT_OK = re.compile(r"^\W*(?:yes|yeah|no|nope|stop|cancel|continue|go\s+on|pause|resume|louder|quieter|mute|unmute|"
                      r"repeat|again|done|thanks|thank\s+you|haan|ji|nahi|bas|ruko)\W*$", re.I)


def wake_phrases():
    return [p.lower() for p in CONFIG["voice"].get("wake_phrases", ["hey ishti", "hey jarvis", "hey my assistant"])]


# spellings the tiny model produces for "ISHTI"
_ISHTI = {"ishti", "isti", "ishty", "ishtee", "eshti", "ishte", "shti", "ishki", "ishtie", "yishti", "ishtea"}


def phrase_in(text):
    """The configured wake phrase heard in text (fuzzy, word by word), or None."""
    import difflib
    words = [("ishti" if w in _ISHTI else w) for w in re.findall(r"[a-z']+", text.lower())]
    for phrase in wake_phrases():
        target = phrase.split()
        for i in range(len(words) - len(target) + 1):
            window = " ".join(words[i:i + len(target)])
            if difflib.SequenceMatcher(None, window, phrase).ratio() >= 0.8:
                return phrase
    return None


_spot_model = None


def spotter():
    """whisper tiny on the CPU (int8): only wake-phrase checks, cheap enough to run on every speech burst."""
    global _spot_model
    if _spot_model is None:
        from faster_whisper import WhisperModel
        _spot_model = WhisperModel("tiny", device="cpu", compute_type="int8")
    return _spot_model


def chime():
    """Two short rising tones (~0.2 s): 'I'm listening'."""
    try:
        import sounddevice as sd
        t = np.arange(int(22050 * 0.09)) / 22050
        tone = lambda f: (0.25 * np.sin(2 * np.pi * f * t) * np.hanning(len(t))).astype(np.float32)
        sd.play(np.concatenate([tone(880), tone(1320)]), 22050)
    except Exception:
        pass


def stray(text):
    from ishti.voice import LAST
    words = re.findall(r"\w+", text)
    if len(words) <= 2 and not SHORT_OK.match(text):
        return True
    return len(words) <= 4 and LAST.get("confidence", 0) < -0.8  # barely-heard short phrase


class Listener:
    def __init__(self, on_command, on_event=None, live=False):
        from openwakeword.model import Model
        from openwakeword.vad import VAD
        cfg = CONFIG["voice"]
        self.live = live                      # Live mode: no wake word, back to listening after every answer
        self.oww = Model(wakeword_models=[cfg["wake_model"]], inference_framework="onnx")
        self.burst, self.burst_quiet = [], 0  # speech burst being collected for wake-phrase spotting
        self.spot_vad = VAD()
        self._spotting = False
        self._woken = None                    # set by the spotter thread: ("listen", None) or ("command", audio)
        self.vad = VAD()          # end-of-command detection
        self.barge_vad = VAD()    # separate state: watches for interruptions while ISHTI thinks/speaks
        self.threshold = cfg["wake_threshold"]
        self.on_command = on_command
        self.on_event = on_event or (lambda kind, data: None)
        self.recent = collections.deque(maxlen=PRE_ROLL)
        self.buf = None
        self.busy = False                     # a command is being handled
        self.cancel = threading.Event()
        self.interrupted = None               # {"request", "rest"} of the reply the owner cut off
        self._pre = collections.deque(maxlen=int(BARGE["min_ms"] / 40) + 2)
        self._run_len, self._check, self._decision, self._quiet_until = 0, None, None, 0.0
        self._partial_busy = False

    # ---------------------------------------------------------------- wake word + command capture
    def feed(self, frame: np.ndarray):
        """One 80 ms frame in. Returns the finished utterance (int16 audio, possibly empty) or None."""
        if self.buf is None:
            self.recent.append(frame)
            if self.live:  # Live mode: speech itself starts the turn
                if self.vad.predict(frame, frame_size=640) >= 0.5:
                    self.buf, self.heard, self.silent = list(self.recent), True, 0
                    self.state("listening")
                return None
            if self._woken:  # the phrase spotter heard a wake phrase
                kind, audio = self._woken
                self._woken = None
                if kind == "command":  # "Hey ISHTI, what's the weather" in one breath: that burst is the command
                    return audio
                self._wake_ack()
                self.buf, self.heard, self.silent = [], False, 0
                return None
            if max(self.oww.predict(frame).values()) >= self.threshold:  # built-in model ("hey jarvis"), instant
                self._wake_ack()
                self.buf, self.heard, self.silent = list(self.recent), False, 0
                return None
            self._spot(frame)
            return None
        self.buf.append(frame)
        if self.vad.predict(frame, frame_size=640) >= 0.5:
            self.heard, self.silent = True, 0
        else:
            self.silent += 1
        n = len(self.buf) - PRE_ROLL
        if n > 0 and n % PARTIAL_EVERY == 0 and self.heard:
            self._partial(np.concatenate(self.buf))
        if (self.heard and self.silent >= END_SILENCE) or (not self.heard and n >= NO_SPEECH) or n >= MAX_LEN:
            audio = np.concatenate(self.buf) if self.heard else np.zeros(0, np.int16)
            self.buf = None
            self.oww.reset()
            self.recent.clear()
            return audio
        return None

    def _spot(self, frame):
        """Collect a burst of speech (VAD) and check it for any configured wake phrase with a tiny CPU model.
        Runs only when someone is talking; the full recogniser stays off until a phrase is heard."""
        speech = self.spot_vad.predict(frame, frame_size=640) >= 0.5
        if speech or self.burst:
            self.burst.append(frame)
            self.burst_quiet = 0 if speech else self.burst_quiet + 1
        if not self.burst or (self.burst_quiet < 5 and len(self.burst) < 50):  # 0.4 s pause or 4 s cap ends a burst
            return
        audio, self.burst, self.burst_quiet = np.concatenate(self.burst), [], 0
        if len(audio) < RATE * 0.4 or self._spotting:
            return
        self._spotting = True

        def check():
            try:
                heard = spotter().transcribe(audio.astype(np.float32) / 32768, beam_size=1, language="en",
                                             initial_prompt="Hey ISHTI. Hey Jarvis. Hey my assistant.")[0]
                text = " ".join(s.text for s in heard)
                hit = phrase_in(text)
                if hit:
                    rest = text.lower().split(hit.split()[-1], 1)[-1]
                    self._woken = ("command", audio) if len(re.findall(r"\w+", rest)) >= 2 else ("listen", None)
            finally:
                self._spotting = False
        threading.Thread(target=check, daemon=True).start()

    def _wake_ack(self):
        """A short chime and the listening state - not a sentence."""
        self.oww.reset()
        self.state("listening")
        self.on_event("wake", {})
        threading.Thread(target=chime, daemon=True).start()

    def command_text(self, audio: np.ndarray) -> tuple[str, str | None]:
        """Returns (clean_command, wrong_name_or_None)."""
        from ishti.voice import transcribe
        raw = transcribe(audio.astype(np.float32) / 32768) if len(audio) else ""
        wrong = _WRONG_NAME.search(raw)
        wrong_name = wrong.group(0) if wrong else None
        return WAKE_TEXT.sub("", raw).strip(), wrong_name

    @staticmethod
    def _identity_correction(wrong_name: str) -> str:
        """Playful one-liner when Whisper hears 'Jarvis' (or another wrong name)."""
        return f"Haha, hey! I'm Ishti, not {wrong_name.capitalize()}! But no worries, I heard you. Let me help. "

    def state(self, name):
        self.on_event("voice_state", {"state": name})

    def _partial(self, audio):
        """Live 'what I'm hearing' for the dashboard; skipped if the previous one is still running."""
        if self._partial_busy:
            return
        self._partial_busy = True

        def run():
            try:
                from ishti.voice import transcribe
                text = WAKE_TEXT.sub("", transcribe(audio.astype(np.float32) / 32768)).strip()
                if text:
                    self.on_event("stt_partial", {"text": text})
            finally:
                self._partial_busy = False
        threading.Thread(target=run, daemon=True).start()

    # ---------------------------------------------------------------- barge-in
    def watch(self, block, talker):
        """While ISHTI thinks or speaks: returns audio frames to start recording a new command from, or None."""
        if self._decision is not None:            # the echo check finished
            verdict, self._decision = self._decision, None
            frames, self._check = self._check, None
            if verdict == "echo":
                talker.resume()
                self._quiet_until = time.time() + BARGE["cooldown_s"]
                return None
            self.interrupted = {"request": getattr(self, "request", ""), "rest": talker.stop()}
            self.cancel.set()
            self.on_event("barge_in", {"heard": verdict})
            return frames + [block]
        if self._check is not None:               # still deciding: keep the audio, it may be a new command
            self._check.append(block)
            return None
        p = self.barge_vad.predict(block, frame_size=BLOCK)
        self._pre.append(block)
        if time.time() < self._quiet_until or p < BARGE["vad_threshold"]:
            self._run_len = 0
            return None
        self._run_len += 1
        if self._run_len * 40 < BARGE["min_ms"]:
            return None
        speaking = talker.speaking
        talker.pause()                            # stop the voice within one audio block
        self._check, self._run_len = list(self._pre), 0
        check_frames = int(BARGE["check_ms"] / 40)

        def decide():
            from ishti.talk import is_echo
            from ishti.voice import transcribe
            while self._check is not None and len(self._check) < check_frames:
                time.sleep(0.02)
            frames = list(self._check or [])
            heard = transcribe(np.concatenate(frames).astype(np.float32) / 32768) if frames else ""
            words = re.findall(r"[a-z0-9']+", heard.lower())
            if speaking:
                self._decision = "echo" if is_echo(heard, talker, BARGE["echo_overlap"]) else heard
            else:  # thinking, nothing playing: no echo possible, but a stray word must not abort the task
                self._decision = heard if len(words) >= 2 else "echo"
        threading.Thread(target=decide, daemon=True).start()
        return None

    # ---------------------------------------------------------------- command handling
    def _handle(self, audio, talker):
        from ishti.llm import Cancelled
        from ishti.talk import Sentences
        self.busy = True
        self.cancel = cancel = threading.Event()
        try:
            self.state("thinking")
            text, wrong_name = self.command_text(audio)
            if not text or stray(text):
                self.on_event("stt_final", {"text": text, "ignored": True})
                return
            self.on_event("stt_final", {"text": text})
            print(f"[wake] heard: {text!r}" + (f" (misheard as '{wrong_name}')" if wrong_name else ""))
            if CONTINUE.match(text) and self.interrupted and self.interrupted.get("rest"):
                self.state("speaking")
                Sentences(talker).flush(self.interrupted["rest"])
                self.interrupted = None
                talker.wait()
                return
            self.request = text
            speech = Sentences(talker)
            if wrong_name:
                talker.say(self._identity_correction(wrong_name))

            def on_token(tok):
                if cancel.is_set():
                    raise Cancelled
                if not speech.spoke:
                    self.state("speaking")
                speech.feed(tok)
            try:
                reply = self.on_command(text, on_token)
            except Cancelled:
                return
            if cancel.is_set():  # interrupted while a non-streaming skill worked: its answer is dropped
                return
            self.state("speaking")
            speech.flush(None if speech.spoke else reply)
            while talker.speaking and not cancel.is_set():
                time.sleep(0.05)
        finally:
            self.busy = False
            if not cancel.is_set():
                self.state("listening" if self.live else "idle")

    def loop(self, read_block, stop: threading.Event):
        """The voice state machine over any audio source: read_block() -> 40 ms int16 block, or None when it ended."""
        from ishti.talk import talker
        tk = talker()
        pair = []
        self.state("listening" if self.live else "idle")
        while not stop.is_set():
            block = read_block()
            if block is None:
                return
            if (self.busy or tk.speaking) and self.buf is None:
                start = self.watch(block, tk) if BARGE["enabled"] else None
                if start is None:
                    continue
                # the interruption is the start of the next command: record the rest of it
                joined = np.concatenate(start)
                self.buf = [joined[i:i + FRAME] for i in range(0, len(joined) - FRAME + 1, FRAME)]
                self.heard, self.silent = True, 0
                self.state("listening")
                continue
            pair.append(block)
            if len(pair) < 2:
                continue
            audio, pair = self.feed(np.concatenate(pair)), []
            if audio is not None:
                if len(audio):
                    threading.Thread(target=self._handle, args=(audio, tk), daemon=True).start()
                else:
                    self.state("listening" if self.live else "idle")

    def run(self, stop: threading.Event):
        """Hands-free on this PC's microphone (wake word first)."""
        import sounddevice as sd
        warned = False
        while not stop.is_set():
            try:
                with sd.InputStream(samplerate=RATE, channels=1, dtype="int16", blocksize=BLOCK) as mic:
                    print(f"[wake] listening (wake phrases {wake_phrases()}, barge-in {'on' if BARGE['enabled'] else 'off'})")
                    warned = False
                    self.loop(lambda: mic.read(BLOCK)[0][:, 0].copy(), stop)
            except Exception as exc:
                if not warned:
                    print(f"[wake] no usable microphone ({exc}); retrying every 30 s")
                    warned = True
                stop.wait(30)


def start(on_command, on_event=None) -> threading.Event:
    """Background hands-free loop; returns the Event that stops it. on_command(text, on_token) -> reply."""
    stop = threading.Event()
    threading.Thread(target=Listener(on_command, on_event).run, args=(stop,), daemon=True, name="wake").start()
    return stop


if __name__ == "__main__":
    import io
    import wave
    from ishti.voice import synthesize

    def pcm16k(text):
        with wave.open(io.BytesIO(synthesize(text))) as wf:
            pcm, rate = np.frombuffer(wf.readframes(wf.getnframes()), np.int16), wf.getframerate()
        t = np.arange(0, len(pcm) / rate, 1 / RATE)
        return np.interp(t, np.arange(len(pcm)) / rate, pcm).astype(np.int16)  # resample 22.05 -> 16 kHz

    def run_through(listener, speech):
        audio = np.concatenate([np.zeros(RATE, np.int16), speech, np.zeros(2 * RATE, np.int16)])
        audio = audio[: len(audio) // FRAME * FRAME]
        for i in range(0, len(audio), FRAME):
            if (out := listener.feed(audio[i:i + FRAME])) is not None:
                return out
        return None

    events = []
    lis = Listener(on_command=None, on_event=lambda k, d: events.append((k, d)))
    assert run_through(lis, pcm16k("What is the weather tomorrow?")) is None, "no wake word -> nothing"
    got = run_through(lis, pcm16k("Hey Jarvis, what is the weather tomorrow?"))
    assert got is not None and len(got), "wake word not detected"
    text, wrong = lis.command_text(got)
    print(f"command: {text!r}, wrong_name: {wrong!r}")
    assert "weather" in text.lower() and "jarvis" not in text.lower(), text
    assert ("voice_state", {"state": "listening"}) in events

    # barge-in: ISHTI's own sentence coming back through the mic is echo; different words interrupt
    class FakeTalker:
        speaking, paused, stopped = True, False, None
        said = ["Tomorrow in Peshawar it will be sunny and thirty four degrees."]
        def pause(self): self.paused = True
        def resume(self): self.paused = False
        def stop(self): self.stopped = "rest"; return "rest"
        def recent_words(self): return set(re.findall(r"[a-z0-9']+", " ".join(self.said).lower()))

    def barge(listener, talker, speech):
        audio = np.concatenate([speech, np.zeros(RATE // 2, np.int16)])
        audio = audio[: len(audio) // BLOCK * BLOCK]
        for i in range(0, len(audio), BLOCK):
            if (out := listener.watch(audio[i:i + BLOCK], talker)) is not None:
                return out
            time.sleep(0.005)
        deadline = time.time() + 10
        while listener._check is not None and time.time() < deadline:  # let the echo check finish
            if (out := listener.watch(np.zeros(BLOCK, np.int16), talker)) is not None:
                return out
            time.sleep(0.02)
        return None

    t1 = FakeTalker()
    assert barge(lis, t1, pcm16k("Tomorrow in Peshawar it will be sunny.")) is None and t1.stopped is None, "echo must not interrupt"
    assert not t1.paused, "echo: the voice resumes"
    lis._quiet_until = 0
    t2 = FakeTalker()
    frames = barge(lis, t2, pcm16k("Stop. Open YouTube instead."))
    assert frames is not None and t2.stopped == "rest" and lis.cancel.is_set(), "a real interruption stops and cancels"
    assert lis.interrupted["rest"] == "rest"
    print("[self-check] wake OK - wake word, command capture, echo resumes, real speech barges in and keeps context")
