"""
ISHTI Wake Word Listener & Audio Trigger Engine
Monitors the local microphone for wake words ('Ishti', 'Jarvis') using audio energy
detection and Faster-Whisper, then dispatches transcribed commands to the orchestrator.
"""

import os
import sys
import time
import re
import wave
import threading
from typing import Callable, Optional
import numpy as np

try:
    import sounddevice as sd
except ImportError:
    sd = None

ROOT_DIR = r"D:\ISHTI"
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

WAKE_WORDS = ["ishti", "jarvis", "hey ishti", "hey jarvis", "computer"]


class WakeWordListener:
    """Continuously monitors microphone stream and triggers callback upon wake word detection."""

    def __init__(
        self,
        on_wake_callback: Optional[Callable[[str], None]] = None,
        sample_rate: int = 16000,
        chunk_duration: float = 2.0,
        energy_threshold: float = 350.0,
    ):
        self.on_wake_callback = on_wake_callback
        self.sample_rate = sample_rate
        self.chunk_duration = chunk_duration
        self.energy_threshold = energy_threshold
        self.is_listening = False
        self._thread = None
        self._stt = None

    def _get_stt(self):
        if self._stt is None:
            try:
                from voice.stt import get_stt
                self._stt = get_stt()
            except Exception as e:
                print(f"[WakeWord] STT load warning: {e}")
        return self._stt

    def start(self):
        """Starts continuous wake word listener in daemon thread."""
        if self.is_listening:
            return
        if sd is None:
            print("[WakeWord] sounddevice module is unavailable. Wake word listener cannot start.")
            return

        self.is_listening = True
        self._thread = threading.Thread(target=self._listen_loop, daemon=True)
        self._thread.start()
        print("[WakeWord] ISHTI Wake Word Listener active. Listening for 'Ishti' / 'Jarvis'...")

    def stop(self):
        """Stops the audio listener loop."""
        self.is_listening = False

    def _listen_loop(self):
        """
        Background listener loop.
        Monitors microphone audio stream with RMS energy gate to minimize CPU/GPU load.
        Only transcribes when voice activity is detected above energy_threshold.
        """
        chunk_frames = int(self.sample_rate * self.chunk_duration)
        temp_dir = os.path.join(ROOT_DIR, "data")
        os.makedirs(temp_dir, exist_ok=True)
        temp_wav = os.path.join(temp_dir, "wake_chunk.wav")

        while self.is_listening:
            try:
                # Record chunk from default microphone
                audio_chunk = sd.rec(
                    chunk_frames,
                    samplerate=self.sample_rate,
                    channels=1,
                    dtype="int16",
                    blocking=True,
                )

                if not self.is_listening:
                    break

                # Compute RMS energy
                rms = np.sqrt(np.mean(np.square(audio_chunk.astype(np.float32))))

                # Only transcribe if audio volume indicates speech
                if rms > self.energy_threshold:
                    # Save to WAV for fast whisper transcription
                    with wave.open(temp_wav, "wb") as wf:
                        wf.setnchannels(1)
                        wf.setsampwidth(2)
                        wf.setframerate(self.sample_rate)
                        wf.writeframes(audio_chunk.tobytes())

                    stt = self._get_stt()
                    if stt:
                        text = stt.transcribe_audio_file(temp_wav).strip().lower()
                        if text:
                            # Check for wake words
                            for ww in WAKE_WORDS:
                                if ww in text:
                                    print(f"[WakeWord] Wake word '{ww}' detected! Raw utterance: '{text}'")
                                    # Extract following command if any
                                    command = re.sub(rf"^.*?{ww}\b", "", text, flags=re.IGNORECASE).strip()
                                    self.trigger_wake(command)
                                    # Sleep briefly to prevent re-triggering on same utterance
                                    time.sleep(1.0)
                                    break
                else:
                    # Sleep lightly between checks
                    time.sleep(0.1)

            except Exception as e:
                # Idle backoff on mic errors
                time.sleep(1.0)

    def trigger_wake(self, command_text: str = ""):
        """Dispatches a wake trigger event to the orchestrator callback."""
        print(f"[WakeWord] Wake event triggered! Command: '{command_text}'")
        if self.on_wake_callback:
            try:
                self.on_wake_callback(command_text)
            except Exception as e:
                print(f"[WakeWord] Callback error: {e}")


_listener = None


def get_wake_listener(callback=None) -> WakeWordListener:
    global _listener
    if _listener is None:
        _listener = WakeWordListener(on_wake_callback=callback)
    return _listener


if __name__ == "__main__":
    listener = get_wake_listener(lambda cmd: print(f"Heard command: {cmd}"))
    listener.start()
    time.sleep(0.5)
    listener.trigger_wake("Look up the system status")
    listener.stop()
