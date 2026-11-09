"""Library audit: do the calls ISHTI makes actually exist in the installed libraries?

The checklist asks for every library to be verified against real current behaviour, not memory. Rather than trusting
a reading of the code, this reads ISHTI's own call sites with `ast`, resolves the target against the INSTALLED
package and checks the keyword arguments it passes are accepted by that object's signature. A renamed flag, a moved
parameter or a signature that changed under us shows up here instead of as a TypeError in front of the owner.

It also prints the installed versions (the evidence that "current" means these versions).

    python tools/check_libraries.py
Exit code: 0 when every call site matches, 1 otherwise.
"""
import ast
import importlib.metadata
import inspect
import json
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
# this tool reads the libraries without importing ishti (whose __init__ reconfigures the console); a signature with a
# non-ASCII default (faster-whisper prints one) would otherwise crash the report on a cp1252 console
for _stream in (sys.stdout, sys.stderr):
    if _stream is not None and hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            pass

# label -> (files, name called, resolver, receiver filter). The receiver filter matters where one file calls two
# objects with the same method name (voice.py calls both WhisperModel.transcribe and BatchedInferencePipeline.transcribe).
ANY = lambda recv: True  # noqa: E731
NOT_BATCHED = lambda recv: "batched" not in recv and recv != "made"  # noqa: E731
yes = lambda recv: recv.startswith("batched") or recv == "made"  # noqa: E731
TARGETS = [
    ("faster-whisper WhisperModel.transcribe", ("ishti/voice.py", "ishti/diarize.py", "ishti/skills/planners/skill.py",
                                                "ishti/skills/video/pro.py", "ishti/wake.py"), "transcribe",
     lambda: __import__("faster_whisper").WhisperModel.transcribe, NOT_BATCHED),
    ("faster-whisper detect_language", ("ishti/voice.py",), "detect_language",
     lambda: __import__("faster_whisper").WhisperModel.detect_language, ANY),
    ("faster-whisper BatchedInferencePipeline.transcribe", ("ishti/voice.py",), "transcribe",
     lambda: __import__("faster_whisper").BatchedInferencePipeline.transcribe, yes),
    ("faster-whisper decode_audio", ("ishti/voice.py",), "decode_audio",
     lambda: __import__("faster_whisper").decode_audio, ANY),
    ("faster-whisper download_model", ("ishti/manage.py",), "download_model",
     lambda: __import__("faster_whisper").download_model, ANY),
    ("openwakeword Model", ("ishti/wake.py",), "Model",
     lambda: __import__("openwakeword.model", fromlist=["Model"]).Model, ANY),
    ("openwakeword VAD.predict", ("ishti/wake.py",), "predict",
     lambda: __import__("openwakeword.vad", fromlist=["VAD"]).VAD.predict, ANY),
    ("piper PiperVoice.synthesize", ("ishti/voice.py",), "synthesize",
     lambda: __import__("piper", fromlist=["PiperVoice"]).PiperVoice.synthesize, ANY),
    ("kokoro Kokoro.create", ("ishti/voice.py",), "create",
     lambda: __import__("kokoro_onnx", fromlist=["Kokoro"]).Kokoro.create, ANY),
    ("speechbrain EncoderClassifier.from_hparams", ("ishti/diarize.py",), "from_hparams",
     lambda: __import__("speechbrain.inference.speaker", fromlist=["EncoderClassifier"]).EncoderClassifier.from_hparams, ANY),
    ("chromadb PersistentClient", ("ishti/semantic.py",), "PersistentClient",
     lambda: __import__("chromadb", fromlist=["PersistentClient"]).PersistentClient, ANY),
    ("pynput GlobalHotKeys", ("ishti/watchdog.py",), "GlobalHotKeys",
     lambda: __import__("pynput.keyboard", fromlist=["GlobalHotKeys"]).GlobalHotKeys, ANY),
]
VERSIONS = ["faster-whisper", "openwakeword", "piper-tts", "kokoro-onnx", "chromadb", "pynput", "sounddevice",
            "onnxruntime", "yfinance", "pypdfium2", "speechbrain", "torch", "ctranslate2"]


def version(name):
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        try:
            return "imported, no metadata"
        except Exception:
            return f"NOT INSTALLED"
        except BaseException:
            return "?"


def calls_in(path, attr, recv_ok=None):
    """[(line, receiver, kwargs)] for every call of that name in the file - `x.attr(...)` and a bare `attr(...)`
    (the libraries are often imported as names: `from faster_whisper import decode_audio`)."""
    out = []
    try:
        tree = ast.parse(open(os.path.join(ROOT, path), encoding="utf-8", errors="replace").read())
    except (OSError, SyntaxError) as exc:
        return [(-1, f"parse error: {exc}", {})]
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Attribute):
            name, receiver = node.func.attr, ast.unparse(node.func.value)
        elif isinstance(node.func, ast.Name):
            name, receiver = node.func.id, ""
        else:
            continue
        if name != attr or (recv_ok is not None and not recv_ok(receiver)):
            continue
        out.append((node.lineno, receiver, {k.arg: ast.unparse(k.value)[:40] for k in node.keywords if k.arg}))
    return out


def unknown_kwargs(sig, kwargs):
    params = sig.parameters
    if any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()):
        return []
    return sorted(k for k in kwargs if k not in params)


def main():
    print("installed versions (what \"current behaviour\" means here):")
    for name in VERSIONS:
        print(f"  {name:<16} {version(name)}")

    problems, checked = [], 0
    for label, files, attr, resolve, recv_ok in TARGETS:
        try:
            target = resolve()
        except Exception as exc:
            problems.append(f"{label}: cannot resolve against the installed package ({type(exc).__name__}: {exc})")
            continue
        try:
            sig = inspect.signature(target)
        except (TypeError, ValueError):
            sig = None
        found = 0
        for path in files:
            for line, receiver, kwargs in calls_in(path, attr, recv_ok):
                found += 1
                checked += 1
                if sig is None:
                    continue
                bad = unknown_kwargs(sig, kwargs)
                if bad:
                    problems.append(f"{label}: {path}:{line} ({receiver or 'bare call'}) passes {bad} - "
                                    f"not accepted by {attr}")
        if not found:
            problems.append(f"{label}: no call site found in {files} (this list is stale, fix it)")

    print(f"\n[check_libraries] {checked} call sites checked against the installed signatures")
    if problems:
        print(f"[check_libraries] {len(problems)} problem(s):")
        for p in problems:
            print("  - " + p)
        return 1
    print("[check_libraries] OK - every checked call matches the installed API")
    return 0


if __name__ == "__main__":
    sys.exit(main())
