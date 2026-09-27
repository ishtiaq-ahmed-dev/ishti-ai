"""ISHTI core: dispatcher, local LLM, memory, routing, skills. Runs 100% locally."""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# pdfium (pypdfium2) is not thread-safe: every PDF parse in ISHTI takes this lock (parallel reads crashed without it)
PDFIUM_LOCK = __import__("threading").RLock()
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)  # legacy packages (orchestrator/, tools/, voice/) import from the repo root

with open(os.path.join(ROOT, "ishti", "config.json"), encoding="utf-8") as _f:
    CONFIG = json.load(_f)
