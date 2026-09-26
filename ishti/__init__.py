"""ISHTI core: dispatcher, local LLM, memory, routing, skills. Runs 100% locally."""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)  # legacy packages (orchestrator/, tools/, voice/) import from the repo root

with open(os.path.join(ROOT, "ishti", "config.json"), encoding="utf-8") as _f:
    CONFIG = json.load(_f)
