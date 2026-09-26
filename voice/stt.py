"""
ISHTI Speech-to-Text (STT) Engine
Local, GPU-accelerated speech transcription using faster-whisper with automatic CPU fallback
and audio recording support via sounddevice.
"""

import os
import io
import wave
import time
from typing import Optional
import numpy as np

try:
    import sounddevice as sd
except ImportError:
    sd = None

try:
    from faster_whisper import WhisperModel
except ImportError:
    WhisperModel = None


class STTEngine:
    def __init__(self, model_size: str = "tiny", device: str = "auto", compute_type: str = "default"):
        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type
        self.model = None
        self._load_model()

    def _load_model(self):
        if WhisperModel is None:
            print("[STT] faster-whisper not installed.")
            return

        # Attempt CUDA first if available
        chosen_device = "cuda" if self.device in ["auto", "cuda"] else "cpu"
        chosen_compute = "float16" if chosen_device == "cuda" else "int8"

        try:
            print(f"[STT] Initializing Whisper model '{self.model_size}' on {chosen_device} ({chosen_compute})...")
            self.model = WhisperModel(self.model_size, device=chosen_device, compute_type=chosen_compute)
            print("[STT] Whisper model loaded successfully.")
        except Exception as e:
            print(f"[STT] CUDA init failed ({e}), falling back to CPU (int8)...")
            try:
                self.model = WhisperModel(self.model_size, device="cpu", compute_type="int8")
                print("[STT] Whisper CPU fallback loaded successfully.")
            except Exception as e2:
                print(f"[STT] Whisper model load failed: {e2}")
                self.model = None

    def transcribe_audio_file(self, file_path: str) -> str:
        """Transcribes a WAV/MP3 audio file."""
        if not self.model:
            return ""
        try:
            segments, info = self.model.transcribe(file_path, beam_size=2)
            text = " ".join([seg.text.strip() for seg in segments]).strip()
            return text
        except Exception as e:
            print(f"[STT] Transcription error: {e}")
            return ""

    def record_and_transcribe(self, duration: float = 4.0, sample_rate: int = 16000) -> str:
        """Records from default microphone and returns transcription."""
        if sd is None:
            return "[Error: sounddevice not available]"
        print(f"[STT] Listening for {duration} seconds...")
        try:
            audio_data = sd.rec(int(duration * sample_rate), samplerate=sample_rate, channels=1, dtype="int16")
            sd.wait()
            # Save to temporary WAV
            temp_wav = os.path.join(r"D:\ISHTI\data", "temp_voice_in.wav")
            os.makedirs(r"D:\ISHTI\data", exist_ok=True)
            with wave.open(temp_wav, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(sample_rate)
                wf.writeframes(audio_data.tobytes())

            text = self.transcribe_audio_file(temp_wav)
            print(f"[STT] Transcribed: \"{text}\"")
            return text
        except Exception as e:
            print(f"[STT] Recording error: {e}")
            return ""


_stt = None


def get_stt() -> STTEngine:
    global _stt
    if _stt is None:
        _stt = STTEngine()
    return _stt


def transcribe(wav_path: str) -> str:
    return get_stt().transcribe_audio_file(wav_path)


if __name__ == "__main__":
    stt = get_stt()
    print("STT Engine initialized.")
