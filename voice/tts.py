"""
ISHTI Text-to-Speech (TTS) Engine
Local, zero-fee speech synthesizer with low latency and clean audio playback.
Supports pyttsx3 (SAPI5 native) with COM multi-threading safety and fallback.
"""

import os
import re
import sys
import threading
import queue
from typing import Optional

try:
    import pythoncom
except ImportError:
    pythoncom = None

import pyttsx3


class TTSEngine:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(TTSEngine, cls).__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self, rate: int = 185, volume: float = 1.0):
        if self._initialized:
            return
        self.rate = rate
        self.volume = volume
        self.speech_queue = queue.Queue()
        self._is_speaking = False
        self._worker_thread = threading.Thread(target=self._speech_worker, daemon=True)
        self._worker_thread.start()
        self._initialized = True
        print("[TTS] ISHTI Voice synthesizer initialized successfully.")

    def _speech_worker(self):
        """Dedicated worker thread for pyttsx3 SAPI5 on Windows with COM initialization."""
        if pythoncom:
            try:
                pythoncom.CoInitialize()
            except Exception:
                pass

        while True:
            text = self.speech_queue.get()
            if text is None:
                break
            try:
                self._is_speaking = True
                engine = pyttsx3.init()
                engine.setProperty("rate", self.rate)
                engine.setProperty("volume", self.volume)
                voices = engine.getProperty("voices")
                for v in voices:
                    if any(name in v.name.lower() for name in ["david", "george", "zira"]):
                        engine.setProperty("voice", v.id)
                        break
                engine.say(text)
                engine.runAndWait()
                engine.stop()
            except Exception as e:
                print(f"[TTS] Speech output error: {e}")
            finally:
                self._is_speaking = False
                self.speech_queue.task_done()

    def speak(self, text: str, wait: bool = False) -> None:
        """Queues speech synthesis."""
        clean_text = text.replace("*", "").replace("#", "").replace("`", "").strip()
        if not clean_text:
            return
        print(f"[ISHTI Voice]: \"{clean_text[:90]}{'...' if len(clean_text) > 90 else ''}\"")
        self.speech_queue.put(clean_text)
        if wait:
            self.speech_queue.join()

    def is_speaking(self) -> bool:
        return self._is_speaking


def speakable(text: str, max_chars: int = 900) -> str:
    """
    Markdown reads terribly aloud. Strip the syntax, drop bare URLs and citation brackets,
    and cut on a sentence boundary so audio never ends mid-word.
    """
    t = re.sub(r"```.*?```", " ", text or "", flags=re.S)          # code blocks
    t = re.sub(r"https?://\S+", "", t)                              # URLs
    t = re.sub(r"\[\d+\]", "", t)                                   # [1] citations
    t = re.sub(r"[*#`_>|]", "", t)                                  # markdown syntax
    t = re.sub(r"^\s*[-+]\s*", "", t, flags=re.M)                   # list bullets
    t = re.sub(r"\s+", " ", t).strip()
    if len(t) <= max_chars:
        return t
    cut = t[:max_chars]
    stop = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    return cut[: stop + 1] if stop > 200 else cut


_tts = None


def get_tts() -> TTSEngine:
    global _tts
    if _tts is None:
        _tts = TTSEngine()
    return _tts


def speak(text: str, wait: bool = False):
    get_tts().speak(text, wait=wait)


if __name__ == "__main__":
    md = "## Result\n\n- **Peshawar**: 32C, sunny [1]\nSee https://open-meteo.com for details."
    spoken = speakable(md)
    assert "##" not in spoken and "**" not in spoken, spoken
    assert "http" not in spoken and "[1]" not in spoken, spoken
    assert "Peshawar" in spoken, spoken
    print("[self-check] speakable OK ->", spoken)

    print("Testing TTS speak...")
    engine = TTSEngine()
    engine.speak("ISHTI voice system operational.", wait=False)
    import time
    time.sleep(2)
    print("TTS test completed.")
