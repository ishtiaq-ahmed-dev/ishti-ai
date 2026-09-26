"""Hands-free voice: openWakeWord ("hey jarvis", CPU) -> record until Silero VAD hears silence ->
faster-whisper -> dispatcher -> Piper. Voice is just another input channel into handle().
A custom "hey ishti" wake model needs training (NEEDS.md); until then the pretrained one is used."""
import collections
import re
import threading

import numpy as np

from ishti import CONFIG

RATE, FRAME = 16000, 1280                 # openWakeWord works on 80 ms frames of 16 kHz int16
PRE_ROLL = 6                              # keep 0.5 s before the trigger: the command often follows in one breath
END_SILENCE, NO_SPEECH, MAX_LEN = 10, 50, 150   # frames: 0.8 s quiet ends it, 4 s of nothing cancels, 12 s cap
WAKE_TEXT = re.compile(r"^\W*(?:hey|hi|ok|okay)?\W*(?:jarvis|ishti)\b\W*", re.I)


class Listener:
    def __init__(self, on_command):
        from openwakeword.model import Model
        from openwakeword.vad import VAD
        cfg = CONFIG["voice"]
        self.oww = Model(wakeword_models=[cfg["wake_model"]], inference_framework="onnx")
        self.vad = VAD()
        self.threshold = cfg["wake_threshold"]
        self.on_command = on_command
        self.recent = collections.deque(maxlen=PRE_ROLL)
        self.buf = None

    def feed(self, frame: np.ndarray):
        """One 80 ms frame in. Returns the finished utterance (int16 audio, possibly empty) or None."""
        if self.buf is None:
            self.recent.append(frame)
            if max(self.oww.predict(frame).values()) < self.threshold:
                return None
            self.buf, self.heard, self.silent = list(self.recent), False, 0
            return None
        self.buf.append(frame)
        if self.vad.predict(frame, frame_size=640) >= 0.5:
            self.heard, self.silent = True, 0
        else:
            self.silent += 1
        n = len(self.buf) - PRE_ROLL
        if (self.heard and self.silent >= END_SILENCE) or (not self.heard and n >= NO_SPEECH) or n >= MAX_LEN:
            audio = np.concatenate(self.buf) if self.heard else np.zeros(0, np.int16)
            self.buf = None
            self.oww.reset()
            self.recent.clear()
            return audio
        return None

    def command_text(self, audio: np.ndarray) -> str:
        from ishti.voice import transcribe
        text = transcribe(audio.astype(np.float32) / 32768) if len(audio) else ""
        return WAKE_TEXT.sub("", text).strip()

    def run(self, stop: threading.Event):
        import sounddevice as sd
        from ishti.voice import speak
        warned = False
        while not stop.is_set():
            try:
                # Reopened after every command so audio buffered while ISHTI was talking is dropped.
                with sd.InputStream(samplerate=RATE, channels=1, dtype="int16", blocksize=FRAME) as mic:
                    print(f"[wake] listening for '{CONFIG['voice']['wake_model']}'")
                    warned = False
                    while not stop.is_set():
                        audio = self.feed(mic.read(FRAME)[0][:, 0])
                        if audio is not None:
                            break
                text = self.command_text(audio)
                if text:
                    print(f"[wake] heard: {text!r}")
                    speak(self.on_command(text), wait=True)
            except Exception as exc:
                if not warned:
                    print(f"[wake] no usable microphone ({exc}); retrying every 30 s")
                    warned = True
                stop.wait(30)


def start(on_command) -> threading.Event:
    """Background hands-free loop; returns the Event that stops it."""
    stop = threading.Event()
    threading.Thread(target=Listener(on_command).run, args=(stop,), daemon=True, name="wake").start()
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

    lis = Listener(on_command=None)
    assert run_through(lis, pcm16k("What is the weather tomorrow?")) is None, "no wake word -> nothing"
    got = run_through(lis, pcm16k("Hey Jarvis, what is the weather tomorrow?"))
    assert got is not None and len(got), "wake word not detected"
    text = lis.command_text(got)
    print(f"command: {text!r}")
    assert "weather" in text.lower() and "jarvis" not in text.lower(), text
    print("[self-check] wake OK - ignores speech without the wake word, captures the command after it")
