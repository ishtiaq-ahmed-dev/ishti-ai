"""Push-to-talk + dictation mode (WP-JEV, matrix JEV-02/03; hey-jev dictation.py / Recorder).

Local only: every chunk goes through `ishti.voice.transcribe` (faster-whisper + the
`stt_vocab` post-correction), never a cloud STT. Sessions are EXPLICIT owner actions:

  * PTT (JEV-02): bind_ptt() registers a Windows hotkey (default right alt) with pynput -
    press starts a session, release ends it. The session exists only while the key is held.
  * Dictation (JEV-03): start()/stop(), "start dictation"/"stop dictation" phrases, chunked
    transcription, paste at the cursor + clipboard, JSONL history under data/.
  * No session active -> push_chunk() refuses: nothing records, so the always-listening
    mic stays OFF (mic_control invariant). Like the Live Agent session, an explicit press
    is the owner's consent for that chunk only.
  * HONEST HOLES: a failed or empty chunk is never pasted; the audio path, the error and
    the index go to the history so nothing silently disappears, and state() counts holes.

Everything is injectable (transcribe/paste/clipboard/binder/log/now) so tests and the
self-check run headless - no microphone, no keystrokes, no audio files.
Self-check: python ishti/dictation.py
"""
from __future__ import annotations

import json
import os
import re
import sys
import time

# `python ishti/dictation.py` (the documented self-check) puts ishti/ first on sys.path; put the
# repo root back so `from ishti import CONFIG` resolves (same bootstrap as ishti/hooks_relay.py).
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from ishti import CONFIG  # noqa: E402  (after the path bootstrap)

try:  # best-effort failure log (never breaks the module if logging is unavailable)
    from ishti.core.logutil import swallowed
except Exception:  # pragma: no cover
    def swallowed(*_a, **_k):
        return None


DATA_DIR = os.path.dirname(CONFIG["memory"]["db_path"])
LOG_PATH = os.path.join(DATA_DIR, "dictation_history.jsonl")
MAX_CHUNKS = 500
MAX_TEXT = 2000
PTT_KEY = "right alt"


def _default_transcribe(path):
    from ishti.voice import transcribe
    return transcribe(path)


def _default_clipboard(text):
    import pyperclip
    pyperclip.copy(text)
    return True


def _default_paste(text):
    """Clipboard + Ctrl+V at the cursor. False (honestly) when the backend is unavailable."""
    try:
        import pyperclip
        pyperclip.copy(text)
        from pynput.keyboard import Controller, Key
        kb = Controller()
        kb.press(Key.ctrl)
        kb.press("v")
        kb.release("v")
        kb.release(Key.ctrl)
        return True
    except Exception:
        return False


def _default_binder(key, on_press, on_release):
    """Register a global hotkey with pynput. Returns an unbind callable."""
    from pynput import keyboard
    if not key:
        raise ValueError("no key")
    want = str(key).strip().lower().replace(" ", "_")
    target = getattr(keyboard.Key, want, None)
    if target is None:
        target = str(key)

    def _same(k):
        return str(getattr(k, "name", k)) == str(getattr(target, "name", target))

    listener = keyboard.Listener(
        on_press=lambda k: on_press() if _same(k) else None,
        on_release=lambda k: on_release() if _same(k) else None)
    listener.start()
    return listener.stop


class Dictation:
    """One dictation engine; all side effects injectable."""

    def __init__(self, *, transcribe=None, paste=None, clipboard=None, binder=None,
                 log_path=None, max_chunks: int = MAX_CHUNKS, now=None):
        self._transcribe = transcribe or _default_transcribe
        self._paste = paste if paste is not None else _default_paste
        self._clipboard = clipboard if clipboard is not None else _default_clipboard
        self._binder = binder
        self.log_path = log_path or LOG_PATH
        self.max_chunks = max(1, int(max_chunks))
        self._now = now or time.time
        self._session = None
        self._ptt_key = None
        self._unbind = None
        self._appends = 0

    # ------------------------------------------------------------------ sessions
    def start(self, kind: str = "dictation") -> bool:
        if self._session is not None:
            return False
        self._session = {"kind": str(kind or "dictation")[:20], "since": self._now(),
                         "chunks": 0, "holes": 0}
        return True

    def start_ptt(self) -> bool:
        return self.start("ptt")

    def release(self) -> bool:
        """PTT key released: end a PTT session (a dictation session is not affected)."""
        if self._session and self._session["kind"] == "ptt":
            return self.stop()
        return False

    def stop(self) -> bool:
        had = self._session is not None
        self._session = None
        return had

    def active(self) -> bool:
        return self._session is not None

    def state(self) -> dict:
        s = self._session or {}
        return {"active": self._session is not None, "kind": s.get("kind", ""),
                "chunks": s.get("chunks", 0), "holes": s.get("holes", 0),
                "since": s.get("since"), "ptt_bound": self._unbind is not None,
                "ptt_key": self._ptt_key, "log_path": self.log_path}

    def apply_phrase(self, text):
        """"start dictation" / "stop dictation" (and a few spellings) -> action name or None."""
        t = re.sub(r"[.!?]+$", "", str(text or "").strip().lower())
        if re.search(r"\b(?:start|begin|open)\s+(?:dictation|dictating|typing)\b|\bdictation\s+on\b", t):
            self.start("dictation")
            return "start"
        if re.search(r"\b(?:stop|end|finish|close)\s+(?:dictation|dictating|typing)\b|\bdictation\s+off\b", t):
            self.stop()
            return "stop"
        return None

    # ------------------------------------------------------------------ chunks
    def push_chunk(self, audio, *, label: str = "") -> dict:
        """Transcribe one recorded chunk. Refuses with no session (nothing records); a failed
        chunk is logged with its audio path and never pasted. Never raises."""
        try:
            if self._session is None:
                return {"ok": False, "pasted": False,
                        "error": "no active session - nothing records outside an explicit session"}
            idx = self._session["chunks"] + self._session["holes"] + 1
            err = ""
            text = None
            try:
                text = self._transcribe(audio)
            except Exception as exc:
                err = type(exc).__name__
            if not isinstance(text, str) or not text.strip():
                self._session["holes"] += 1
                why = err or "empty transcript"
                self._log({"i": idx, "ok": False, "audio": str(audio)[:200], "error": why,
                           "label": str(label)[:60], "ts": self._now()})
                return {"ok": False, "index": idx, "pasted": False,
                        "error": f"chunk {idx} failed ({why}) - not pasted"}
            text = text.strip()[:MAX_TEXT]
            self._session["chunks"] += 1
            try:
                self._clipboard(text)
            except Exception as _exc:
                swallowed('ishti/dictation.py:Dictation.push_chunk', _exc)
            try:
                pasted = bool(self._paste(text))
            except Exception:
                pasted = False
            self._log({"i": idx, "ok": True, "chars": len(text), "text": text, "pasted": pasted,
                       "label": str(label)[:60], "ts": self._now()})
            return {"ok": True, "index": idx, "text": text, "pasted": pasted}
        except Exception as exc:                      # a chunk must never take the session down
            return {"ok": False, "pasted": False, "error": f"unusable chunk ({type(exc).__name__})"}

    # ------------------------------------------------------------------ history
    def _log(self, row: dict) -> None:
        try:
            os.makedirs(os.path.dirname(self.log_path), exist_ok=True)
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
            self._appends += 1
            if self._appends % 25 == 0 or self._appends <= 2:
                self._trim()
        except Exception as _exc:
            swallowed('ishti/dictation.py:Dictation._log', _exc)

    def _trim(self) -> None:
        """Keep the JSONL history bounded (last max_chunks lines)."""
        try:
            with open(self.log_path, encoding="utf-8") as f:
                lines = f.readlines()
            if len(lines) > self.max_chunks:
                with open(self.log_path, "w", encoding="utf-8") as f:
                    f.writelines(lines[-self.max_chunks:])
        except Exception as _exc:
            swallowed('ishti/dictation.py:Dictation._trim', _exc)

    def history(self, limit: int = 50) -> list:
        try:
            with open(self.log_path, encoding="utf-8") as f:
                rows = [json.loads(line) for line in f if line.strip()]
            return rows[-max(1, int(limit)):]
        except Exception:
            return []

    # ------------------------------------------------------------------ ptt binding
    def bind_ptt(self, key: str = PTT_KEY) -> bool:
        """Register the hold-to-talk key. False (honestly) when the hotkey backend is absent."""
        try:
            self.unbind_ptt()
            binder = self._binder or _default_binder
            self._unbind = binder(key, self.start_ptt, self.release)
            self._ptt_key = key
            return self._unbind is not None
        except Exception:
            self._unbind = None
            self._ptt_key = None
            return False

    def unbind_ptt(self) -> bool:
        try:
            if self._unbind:
                self._unbind()
            had = self._unbind is not None
            self._unbind = None
            self._ptt_key = None
            if self._session and self._session["kind"] == "ptt":
                self.stop()
            return had
        except Exception:
            self._unbind = None
            return False


_engine = None


def engine() -> Dictation:
    """Process-wide engine (so the tray/UI share one session)."""
    global _engine
    if _engine is None:
        _engine = Dictation()
    return _engine


if __name__ == "__main__":
    import tempfile
    base = tempfile.mkdtemp()
    log = os.path.join(base, "history.jsonl")
    calls = {"paste": [], "clip": [], "text": {"hello world", "second chunk"}, "n": 0}

    def fake_transcribe(path):
        text = "hello world" if "1" in str(path) else ("second chunk" if "2" in str(path) else "")
        if "boom" in str(path):
            raise RuntimeError("stt down")
        return text

    d = Dictation(transcribe=fake_transcribe, log_path=log,
                  paste=lambda t: calls["paste"].append(t) or True,
                  clipboard=lambda t: calls["clip"].append(t) or True,
                  binder=lambda key, on_press, on_release: (calls.update(key=key, press=on_press, rel=on_release) or (lambda: None)),
                  max_chunks=3)
    # no session -> nothing records
    r = d.push_chunk("audio1.wav")
    assert r["ok"] is False and "no active session" in r["error"] and calls["paste"] == []
    assert d.history() == [] and not d.active()
    # PTT: press starts, release ends; mic only while held
    assert d.bind_ptt("right alt") and d.state()["ptt_bound"]
    calls["press"]()
    assert d.active() and d.state()["kind"] == "ptt"
    r = d.push_chunk("audio1.wav")
    assert r["ok"] and r["text"] == "hello world" and calls["paste"] == ["hello world"] and calls["clip"] == ["hello world"]
    assert calls["rel"]() is True and not d.active()
    # dictation mode + phrases + honest holes
    assert d.apply_phrase("start dictation") == "start" and d.active()
    assert d.apply_phrase("start dictation") == "start", "phrase recognised; start() itself refuses"
    assert d.apply_phrase("please stop dictation.") == "stop" and not d.active()
    d.start()
    hole = d.push_chunk("boom.wav")
    assert hole["ok"] is False and "not pasted" in hole["error"] and calls["paste"] == ["hello world"]
    assert d.state()["holes"] == 1 and d.state()["chunks"] == 0
    ok = d.push_chunk("audio2.wav")
    assert ok["ok"] and ok["index"] == 2, ok
    assert d.state()["chunks"] == 1
    # empty transcript is a hole too; the failed audio path is preserved in the history
    assert d.push_chunk("audio3.wav")["ok"] is False
    rows = d.history(limit=99)
    assert any(r.get("ok") is False and r.get("audio") for r in rows), rows
    for i in range(4):
        d.push_chunk(f"audio1_{i}.wav")
    d._trim()
    assert len(d.history(limit=99)) <= 3, "history stays bounded"
    assert json.loads(open(log, encoding="utf-8").read().splitlines()[-1])["i"]
    # junk never raises; repeated start refused
    for junk in (None, [], {"a": "b"}, b""):
        assert d.push_chunk(junk)["ok"] is False, repr(junk)
    d.stop()
    assert d.start() and not d.start() and d.stop()
    assert d.unbind_ptt() and not d.state()["ptt_bound"]
    assert d.apply_phrase("hello there") is None
    print("[self-check] dictation OK - explicit sessions only, PTT press/release, dictation phrases, "
          "honest holes (never pasted), bounded JSONL history")
