"""Real-time speech out: the reply is spoken sentence by sentence while the model is still writing it, and the
player can pause (<1 audio block), resume or stop instantly - what barge-in needs.

  Talker    one audio stream for everything ISHTI says; say(sentence) queues synthesised speech
  Sentences feeds streamed tokens and hands each finished sentence to the Talker (first audio after 1 sentence)
"""
import collections
import re
import threading
import time

import numpy as np

BLOCK = 512  # output frames per callback (~23 ms at 22.05 kHz): pause/stop take effect within one block
SENTENCE_END = re.compile(r"(?<=[.!?۔؟])\s+|\n+")


class Talker:
    def __init__(self):
        self.queue = collections.deque()      # (text, int16 pcm) waiting to play
        self.current, self.pos, self.text = None, 0, ""
        self.rate = None
        self.paused = False
        self.said = []                         # sentences fully played (for echo checks and "continue")
        self.pending = 0                       # sentences still being synthesised
        self.first_audio = 0.0                 # set to None to time the next audio start (perf_counter)
        self.duck = True                       # lower other apps' sound while speaking (B5)
        self.lock = threading.Lock()
        self.stream = None
        self.idle = threading.Event()
        self.idle.set()
        import queue
        self.generation_texts, self.generation, self.worker = queue.Queue(), 0, None

    # ---- producing ----
    def say(self, text):
        text = text.strip()
        if not text:
            return
        if self.idle.is_set() and self.duck:  # B5: other apps go quiet while ISHTI talks
            from ishti import duck
            duck.start()
        with self.lock:
            self.pending += 1
            self.generation_texts.put((self.generation, text))
            self.idle.clear()
            if self.worker is None or not self.worker.is_alive():
                self.worker = threading.Thread(target=self._synth_loop, daemon=True, name="talk-synth")
                self.worker.start()

    def _synth_loop(self):
        """One worker, in order: a short second sentence must not be spoken before the first."""
        import io
        import wave
        from ishti.voice import synthesize
        while True:
            try:
                gen, text = self.generation_texts.get()
                try:
                    with wave.open(io.BytesIO(synthesize(text))) as wf:
                        pcm, rate = np.frombuffer(wf.readframes(wf.getnframes()), np.int16), wf.getframerate()
                except Exception as exc:
                    print(f"[talk] synthesis failed: {exc}")
                    pcm, rate = np.zeros(0, np.int16), 22050
                with self.lock:
                    self.pending -= 1
                    if gen == self.generation:  # stop() since it was queued: drop it
                        self.queue.append((text, pcm))
                        if not self._ensure_stream(rate):  # no speaker: nothing will play it, don't wait for it
                            self.queue.clear()
                            self.said.append(text)
                self._check_idle()
            except Exception as loop_exc:
                print(f"[talk] worker loop error: {loop_exc}")
                self._check_idle()

    # ---- playing ----
    def _ensure_stream(self, rate):
        if self.stream is None or self.rate != rate:
            import sounddevice as sd
            if self.stream is not None:
                try:
                    self.stream.close()
                except Exception:
                    pass
            self.rate = rate
            try:
                self.stream = sd.OutputStream(samplerate=rate, channels=1, dtype="int16", blocksize=BLOCK, callback=self._fill)
                self.stream.start()
            except Exception as exc:
                print(f"[talk] audio stream initialization failed ({exc})")
                self.stream = None
        return self.stream is not None

    def _fill(self, out, frames, _time, _status):
        out[:] = 0
        with self.lock:
            if self.paused:
                return
            n = 0
            while n < frames:
                if self.current is None:
                    if not self.queue:
                        break
                    self.text, self.current = self.queue.popleft()
                    self.pos = 0
                    if self.first_audio is None:
                        self.first_audio = time.perf_counter()  # latency probe: the reply is audible from here
                take = min(frames - n, len(self.current) - self.pos)
                out[n:n + take, 0] = self.current[self.pos:self.pos + take]
                n, self.pos = n + take, self.pos + take
                if self.pos >= len(self.current):
                    self.said.append(self.text)
                    self.current, self.text = None, ""
        if n < frames:
            self._check_idle()

    def _went_idle(self):
        """Idle now; if it was talking, other apps get their volume back."""
        if not self.idle.is_set():
            self.idle.set()
            if self.duck:
                from ishti import duck
                duck.stop()

    def _check_idle(self):
        with self.lock:
            done = self.current is None and not self.queue and self.pending == 0
        if done:
            self._went_idle()

    # ---- control ----
    @property
    def speaking(self):
        return not self.idle.is_set()

    def pause(self):
        with self.lock:
            self.paused = True

    def resume(self):
        with self.lock:
            self.paused = False

    def stop(self):
        """Drop everything not yet heard; returns that text (kept so "continue" can pick it up)."""
        with self.lock:
            rest = ([self.text] if self.current is not None else []) + [t for t, _ in self.queue]
            self.queue.clear()
            self.current, self.text, self.paused = None, "", False
            self.generation += 1  # sentences still being synthesised are dropped when they finish
        self._check_idle() if self.pending else self._went_idle()
        return " ".join(rest)

    def recent_words(self):
        """Words ISHTI is saying or just said - what its own voice through the speakers would transcribe to."""
        with self.lock:
            texts = self.said[-2:] + ([self.text] if self.text else []) + [t for t, _ in list(self.queue)[:1]]
        return set(re.findall(r"[a-z0-9']+", " ".join(texts).lower()))

    def wait(self, timeout=None):
        return self.idle.wait(timeout)


class RemoteTalker(Talker):
    """A Talker whose speaker is a browser (Live mode): the same queue, pause, stop and echo bookkeeping, but the
    audio goes out over the Live WebSocket in 40 ms chunks paced in real time, so pause/stop still act within a
    chunk plus the browser's small jitter buffer. send(bytes | dict) must be thread-safe."""
    STEP = 0.04

    def __init__(self, send):
        super().__init__()
        self.send = send
        self.closed = False

    def _ensure_stream(self, rate):
        if self.stream is None or self.rate != rate:
            self.rate = rate
            self.send({"type": "audio_format", "data": {"rate": rate}})
            if self.stream is None:
                self.stream = threading.Thread(target=self._pump, daemon=True, name="talk-remote")
                self.stream.start()
        return not self.closed

    def _pump(self):
        nxt = time.perf_counter()
        while not self.closed:
            n = int(self.rate * self.STEP)
            if self.current is not None or self.queue:
                out = np.zeros((n, 1), np.int16)
                self._fill(out, n, None, None)
                if not self.paused:
                    self.send(out[:, 0].tobytes())
            nxt += self.STEP
            time.sleep(max(0.0, nxt - time.perf_counter()))
            nxt = max(nxt, time.perf_counter() - self.STEP)  # after a stall, don't burst to catch up

    def stop(self):
        rest = super().stop()
        self.send({"type": "audio_stop", "data": {}})  # drop what the browser has buffered, too
        return rest

    def close(self):
        self.closed = True
        super().stop()


class Sentences:
    """Streamed tokens in, whole sentences out to the Talker, so speech starts after the first sentence."""
    def __init__(self, talker, min_chars=12):
        self.talker, self.buf, self.min = talker, "", min_chars
        self.spoke = False

    def feed(self, token):
        self.buf += token
        while True:
            m = SENTENCE_END.search(self.buf)
            if not m or m.start() < self.min:
                break
            self.talker.say(self.buf[:m.start()])
            self.buf, self.spoke = self.buf[m.end():], True

    def flush(self, full_text=None):
        """End of the reply: speak what's left; if nothing streamed (a non-LLM skill), speak the whole reply
        sentence by sentence."""
        if not self.spoke and full_text:
            self.buf = full_text
        for part in SENTENCE_END.split(self.buf):
            self.talker.say(part)
        self.buf = ""


def is_echo(heard, talker, min_overlap=0.5):
    """Was what the mic heard ISHTI's own voice from the speakers? Word match, not energy: most of the heard
    words appear in what ISHTI is saying. Nothing intelligible counts as echo/noise too."""
    words = [w for w in re.findall(r"[a-z0-9']+", heard.lower()) if len(w) > 1]
    if not words:
        return True
    spoken = talker.recent_words()
    return sum(w in spoken for w in words) / len(words) >= min_overlap


_talker = None


def talker():
    global _talker
    if _talker is None:
        _talker = Talker()
    return _talker


if __name__ == "__main__":
    t = Talker.__new__(Talker)
    Talker.__init__(t)
    t.said = ["The weather tomorrow in Peshawar is sunny."]
    assert is_echo("weather tomorrow in peshawar", t) and is_echo("", t)
    assert not is_echo("stop, open youtube instead", t)
    got = []
    fake = type("T", (), {"say": lambda self, s: got.append(s)})()
    s = Sentences(fake)
    for tok in ["Tomorrow in Pesh", "awar: 34°C. It will", " be sunny! Anything", " else?"]:
        s.feed(tok)
    assert got == ["Tomorrow in Peshawar: 34°C.", "It will be sunny!"], got
    s.flush()
    assert got[-1] == "Anything else?", got
    s2 = Sentences(fake)
    s2.flush("One. Two sentences here.")
    assert got[-2:] == ["One.", "Two sentences here."], got
    t0 = time.time()
    tk = talker()
    tk.say("Testing sentence streaming.")
    tk.say("Second sentence plays right after.")
    time.sleep(0.8)
    assert tk.speaking
    tk.pause(); time.sleep(0.3); tk.resume()
    rest = tk.stop() if time.time() - t0 > 0 else ""
    assert not tk.speaking

    # Live mode: the browser is the speaker - audio streams in real time, stop tells it to drop its buffer
    sent = []
    rt = RemoteTalker(sent.append)
    rt.say("Hello there. This is a test.")
    assert rt.wait(15), "remote talker never finished"
    secs = sum(len(m) for m in sent if isinstance(m, bytes)) / 2 / rt.rate
    assert secs > 1.0 and {"type": "audio_format", "data": {"rate": rt.rate}} in sent, secs
    rt.say("A long sentence that will be cut off in the middle by a barge in from the owner.")
    time.sleep(1.2)
    assert "barge" in rt.stop() and sent[-1] == {"type": "audio_stop", "data": {}} and not rt.speaking
    rt.close()
    mute = Talker()  # no speaker at all must not leave ISHTI "speaking" forever
    mute._ensure_stream = lambda rate: False
    mute.say("Nobody can hear this.")
    assert mute.wait(15) and mute.said[-1] == "Nobody can hear this."
    from ishti import duck
    assert duck._holders == 0, duck._holders  # every duck from speaking was released (B5)
    print(f"[self-check] talk OK - sentence split, echo filter, play/pause/stop (stopped with left: {rest!r}), "
          f"browser voice streams and stops, no speaker never hangs, ducking released")
