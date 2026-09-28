"""ISHTI core: dispatcher, local LLM, memory, routing, skills. Runs 100% locally."""
import json
import os
import sys

# Windows consoles default to cp1252, which can't print the arrows, degree signs and Urdu/Pashto text
# ISHTI logs and replies with (UnicodeEncodeError mid-reply). Reconfigure once, at the central import:
# every entry path (python -m ishti, orchestrator/startup.py, skill self-checks, tests) imports this.
for _stream in (sys.stdout, sys.stderr):
    if _stream is not None and hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):  # a real file handle that doesn't want reconfiguring: leave it
            pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# pdfium (pypdfium2) is not thread-safe: every PDF parse in ISHTI takes this lock (parallel reads crashed without it)
PDFIUM_LOCK = __import__("threading").RLock()
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)  # legacy packages (orchestrator/, tools/, voice/) import from the repo root

with open(os.path.join(ROOT, "ishti", "config.json"), encoding="utf-8") as _f:
    CONFIG = json.load(_f)
