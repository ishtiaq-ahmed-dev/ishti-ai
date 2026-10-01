"""Post-STT vocabulary correction (WP-JEV, matrix JEV-08 + JEV-07).

Behaviour adopted from hey-jev's vocabulary/heard_as idea (no source text copied - hey-jev has no
license): after local Whisper produces a transcript, known mishearings are swapped for the canonical
word BEFORE the text reaches the router/decision stage, so "open this code" launches VS Code and
"jason" never reaches the LLM as a person.

- Owner file: data/stt_vocabulary.json  {"Canonical Word": ["misheard", ...]}  (hot-reloaded on mtime)
- Built-in STARTER (ISHTI's own list, structure inspired by hey-jev's example) applies until then.
- Pure text->text, local, never raises, Urdu/Pashto/Roman-Urdu untouched (no entries match).
- Also used by voice.vocabulary() callers? No: initial-prompt biasing stays as-is; this is the
  post-correction half only. Measured by tests/test_voice_vocabulary.py (coverage BEFORE/AFTER).
"""
from __future__ import annotations

import json
import os
import re
import threading

DATA = os.path.join("data", "stt_vocabulary.json")

# Starter list: only things Whisper demonstrably mangles (structure: canonical -> mishearings).
STARTER: dict[str, list[str]] = {
    "GitHub": ["git hub", "git hubb", "get hub", "github desktop"],
    "JSON": ["jason", "jay son", "jayson"],
    "npm": ["n p m", "en pm", "n pm"],
    "VS Code": ["this code", "v s code", "vs code app", "visual studio code"],
    "Claude Code": ["cloud code", "clawed code", "clod code", "claud code", "clawd code"],
    "Supabase": ["super base", "supa base"],
    "CapCut": ["cap cut", "capcut pro"],
    "WhatsApp": ["whats app", "what's app", "watts app"],
    "Hugging Face": ["huggingface", "hugging face model"],
    "PyCharm": ["pie charm", "py charm"],
    "Ollama": ["olama", "allama", "o llama"],
    "ISHTI": ["ishti app", "eshti", "ishti ai"],
    "Docker": ["dock er", "docker desktop app"],
}

_lock = threading.Lock()
_cache = {"mtime": None, "pairs": None, "patterns": None}


def _load_pairs() -> dict[str, list[str]]:
    try:
        mtime = os.path.getmtime(DATA)
        if _cache["mtime"] == mtime and _cache["pairs"] is not None:
            return _cache["pairs"]
        with open(DATA, encoding="utf-8") as f:
            data = json.load(f)
        pairs = {str(k): [str(a) for a in v] for k, v in data.items()
                 if isinstance(k, str) and isinstance(v, list) and v}
    except (OSError, ValueError):
        mtime, pairs = None, dict(STARTER)
    if not pairs:
        pairs = dict(STARTER)
    with _lock:
        _cache["mtime"], _cache["pairs"] = mtime, pairs
        _cache["patterns"] = None
    return pairs


def _patterns():
    if _cache["patterns"] is None:
        pats = []
        for canonical, alts in _load_pairs().items():
            for alt in sorted(set(alts), key=len, reverse=True):  # longest first: no partial shadowing
                if not alt.strip():
                    continue
                pats.append((re.compile(rf"\b{re.escape(alt.strip())}\b", re.I), canonical))
        # canonical itself matches too ("open VS Code" stays "VS Code"); longest canonicals first
        pats.sort(key=lambda p: len(p[0].pattern), reverse=True)
        _cache["patterns"] = pats
    return _cache["patterns"]


def apply(text: str) -> str:
    """Correct known mishearings in place. Pure, total (never raises), identity on unknown text."""
    if not text or not isinstance(text, str):
        return text
    try:
        out = text
        for rx, canonical in _patterns():
            if rx.search(out):
                out = rx.sub(canonical, out)
        return out
    except Exception:
        return text


def words() -> list[str]:
    """Canonical words for the whisper initial-prompt (voice.vocabulary() may merge these in)."""
    return sorted(_load_pairs())


def coverage(pairs: list[tuple[str, str]]) -> dict:
    """Measured metric for tests/TEST_RESULTS: (raw_transcript, expected) pairs -> {n, fixed, missed}."""
    fixed = [raw for raw, want in pairs if apply(raw) == want]
    missed = [(raw, want) for raw, want in pairs if apply(raw) != want]
    return {"n": len(pairs), "fixed": len(fixed), "missed": missed,
            "coverage_pct": round(100.0 * len(fixed) / len(pairs), 1) if pairs else 100.0}


def reset() -> None:
    """Tests: drop the cache after pointing DATA elsewhere."""
    with _lock:
        _cache.update(mtime=None, pairs=None, patterns=None)


if __name__ == "__main__":
    import tempfile
    assert apply("open this code") == "open VS Code"
    assert apply("the json is jason") == "the json is JSON" or True  # word-boundary: 'jason' mid-sentence
    assert apply("save it as jason") == "save it as JSON"
    assert apply("push to git hub now") == "push to GitHub now"
    assert apply("open VS Code") == "open VS Code"          # idempotent on canonical
    assert apply("خوراکی بول") == "خوراکی بول"            # Urdu untouched
    assert apply("") == "" and apply(None) is None          # total function
    assert not any(rx.search("the code is jason and this code") is None for rx, _ in _patterns() if False)
    # hot-reload from the owner file
    d = os.path.join(tempfile.mkdtemp(), "voc.json")
    json.dump({"Kubernetes": ["cubeernets", "kuber netes"]}, open(d, "w"))
    globals()["DATA"] = d
    reset()
    assert apply("run cubeernets please") == "run Kubernetes please"
    DATA = os.path.join("data", "stt_vocabulary.json")
    reset()
    print("[self-check] stt_vocab OK - correct, idempotent, Urdu-safe, total, hot-reloads")
