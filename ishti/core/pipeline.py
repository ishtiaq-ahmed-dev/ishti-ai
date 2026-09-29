"""Pipeline stage interfaces (Part B) — every stage swappable from Settings.

Each stage is a small Protocol (structural typing: adapters don't have to inherit) plus
an `emit` trace-event hook (B7). Adapters wrap what ISHTI already runs (voice.py, router,
llm.py, talk.py) so the existing engines stay the implementations; new engines slot in by
implementing the same Protocol and being selected in Settings.
"""
from __future__ import annotations

import re
import time
from typing import Callable, Protocol, runtime_checkable

TraceFn = Callable[..., None]

_SENT = re.compile(r"(?<=[.!?])\s+")
_SENT_END = re.compile(r"[.!?…]['\”’)\]]?\s*$")


# ------------------------------------------------------------------ trace events (B7)

class Tracer:
    """Stage trace events: {type:'stage', stage, status:start/done/error, input, output, error, ms}.
    Feeds the HUD Thinking panel now; full observability lands in Prompt 3 on top of this."""

    def __init__(self, fn: TraceFn | None = None, enabled: bool = True):
        self.fn = fn
        self.enabled = enabled

    def emit(self, stage: str, status: str, input_summary: str = "", output_summary: str = "",
             error: str = "", ms: float = 0.0, **extra):
        if not (self.enabled and self.fn):
            return
        try:
            self.fn(type="stage", stage=stage, status=status,
                    input=str(input_summary)[:120], output=str(output_summary)[:160],
                    error=error, ms=round(ms, 1), **extra)
        except Exception:
            pass  # observability must never break the turn

    class _Span:
        def __init__(self, tracer: "Tracer", stage: str, input_summary: str):
            self.t0 = time.perf_counter()
            self.tracer, self.stage, self.input = tracer, stage, input_summary

        def done(self, output_summary=""):
            self.tracer.emit(self.stage, "done", self.input, output_summary,
                             ms=(time.perf_counter() - self.t0) * 1000)

        def error(self, error: str):
            self.tracer.emit(self.stage, "error", self.input, error=error,
                             ms=(time.perf_counter() - self.t0) * 1000)

    def span(self, stage: str, input_summary: str = "") -> "_Span":
        return Tracer._Span(self, stage, input_summary)


def sentences(text: str) -> list[str]:
    """Split into speakable sentences (TTS streaming)."""
    out, buf = [], ""
    for part in _SENT.split((text or "").strip()):
        buf = f"{buf} {part}".strip()
        if buf and _SENT_END.search(buf):
            out.append(buf)
            buf = ""
    if buf:
        out.append(buf)
    return out


# ------------------------------------------------------------------ B1 STT

@runtime_checkable
class STTStage(Protocol):
    def transcribe(self, audio, **kw) -> dict: ...


class LocalWhisperSTT:
    """The existing faster-whisper engine (ishti.voice), adapted to the interface.
    `transcribe(audio) -> {text, language, confidence, timestamps}`."""

    def __init__(self, language: str | None = None):
        self.language = language

    def transcribe(self, audio, **kw) -> dict:
        from ishti import voice
        lang = kw.get("language") or self.language
        text = voice.transcribe(audio, language=lang)
        return {"text": (text or "").strip(), "language": lang or "auto", "confidence": None,
                "timestamps": []}


class TextFallbackSTT:
    """No microphone / testing: the 'audio' is already text (or a file path to read)."""

    def transcribe(self, audio, **kw) -> dict:
        text = audio
        if isinstance(audio, (bytes, bytearray)):
            text = bytes(audio).decode("utf-8", "replace")
        elif isinstance(audio, str) and audio.endswith(".txt"):
            try:
                with open(audio, encoding="utf-8") as f:
                    text = f.read()
            except OSError:
                text = ""
        return {"text": (text or "").strip(), "language": kw.get("language") or "en",
                "confidence": 1.0, "timestamps": []}


# ------------------------------------------------------------------ B2 intent router

@runtime_checkable
class IntentRouterStage(Protocol):
    def route(self, request: str, state_summary: str = "", skills: dict | None = None) -> dict: ...


class EmbeddingShortlistRouter:
    """Existing skills + Laya scores (the arch2 shortlist), adapted. Returns
    {intent, skills: [names], confidence, followup, source}."""

    def __init__(self, top_k: int = 20, min_confidence: float = 0.2):
        self.top_k = top_k
        self.min_confidence = min_confidence

    def route(self, request: str, state_summary: str = "", skills: dict | None = None) -> dict:
        from ishti.arch2 import pipeline as apipe
        skills = skills or {}
        scores = apipe._laya_scores(request, skills) if skills else {}
        ranked = [n for n, _ in sorted(scores.items(), key=lambda kv: -kv[1]) if n in skills]
        top = ranked[:self.top_k]
        conf = float(scores.get(top[0], 0.0)) if top else 0.0
        intent = "conversation"
        if re.search(r"\b(?:search|research|find|look\s+up|price|compare)\b", request, re.I):
            intent = "information"
        return {"intent": intent, "skills": top, "confidence": conf,
                "followup": bool(state_summary), "source": "laya+shortlist"}


# ------------------------------------------------------------------ B3 LLM

@runtime_checkable
class LLMStage(Protocol):
    def chat(self, messages: list[dict], *, role: str = "main", system: str | None = None,
             tools: list | None = None, **kw): ...


class OllamaLLM:
    """The existing single LLM client (ishti.llm), adapted. Settings pick the backend; an
    OpenAI-compatible local endpoint (llama.cpp server, vLLM) only needs base_url+model."""

    def __init__(self, role_map: dict | None = None, temperature: float | None = None,
                 top_p: float | None = None, thinking: bool | None = None, max_tokens: int | None = None):
        self.role_map = role_map or {}
        self.temperature = temperature
        self.top_p = top_p
        self.thinking = thinking
        self.max_tokens = max_tokens

    def chat(self, messages, *, role="main", system=None, tools=None, on_token=None, **kw):
        from ishti import llm
        role = self.role_map.get(role, role)
        if self.temperature is not None and "temperature" not in kw:
            kw["temperature"] = self.temperature
        if self.top_p is not None and "top_p" not in kw:
            kw["top_p"] = self.top_p
        if self.max_tokens is not None and "max_tokens" not in kw:
            kw["max_tokens"] = self.max_tokens
        if self.thinking is False:
            system = f"{system or ''}\n\nDo not emit <think> blocks; answer directly." if system is not None else None
        return llm.chat(messages, role=role, system=system, tools=tools, on_token=on_token, **kw)

    def ask(self, prompt: str, *, role: str = "main", system: str | None = None, **kw) -> str:
        from ishti import llm
        return llm.ask(prompt, role=self.role_map.get(role, role), system=system, **kw)


# ------------------------------------------------------------------ B6 TTS

@runtime_checkable
class TTSStage(Protocol):
    def speak(self, text: str, **kw) -> None: ...
    def stop(self) -> None: ...


class LocalTalkerTTS:
    """The existing Kokoro/Piper roster through talk.Talker: sentence-streamed, with
    barge-in stop. `enabled=False` (Settings) makes speak() a no-op."""

    def __init__(self, enabled: bool = True, voice: str | None = None, speed: float | None = None):
        self.enabled = enabled
        self.voice = voice
        self.speed = speed

    def speak(self, text: str, **kw) -> None:
        if not self.enabled:
            return
        from ishti.talk import talker
        t = talker()
        if self.voice or self.speed:
            text = text  # roster picks the voice; explicit overrides go through Settings' voice profile
        t.say(text)

    def stop(self) -> None:
        try:
            from ishti.talk import talker
            talker().stop()
        except Exception:
            pass
