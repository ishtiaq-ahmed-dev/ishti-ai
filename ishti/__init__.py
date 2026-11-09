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


def _relocate(node, home=r"D:\ISHTI"):
    r"""config.json was written on the owner's PC with absolute D:\ISHTI\... paths. When the project lives in another folder
    (C:\ISHTI, a Desktop folder, another drive) those paths are rebased to where it really is, in memory only."""
    if isinstance(node, str):
        return ROOT + node[len(home):] if node.lower().startswith(home.lower()) else node
    if isinstance(node, list):
        return [_relocate(x, home) for x in node]
    if isinstance(node, dict):
        return {k: _relocate(v, home) for k, v in node.items()}
    return node


if os.path.normcase(ROOT) != os.path.normcase(r"D:\ISHTI"):
    CONFIG = _relocate(CONFIG)

# Scratch/test mode (ISHTI_TEST_MODE=1 + ISHTI_SCRATCH=1): move the writable stores out of the real
# data/ dir BEFORE any module binds a path from CONFIG (privacy, models, notes, vram, self_heal ...
# all read it at import time). One redirect here covers every dirname(CONFIG["memory"]["db_path"])
# consumer; modules with their own hard-coded data paths call test_mode.scratch_path() themselves.
from ishti import test_mode as _test_mode  # noqa: E402

if os.environ.get("ISHTI_SCRATCH") == "1":  # explicit opt-in; the test suite sets it without test mode (conftest)
    _scratch = _test_mode.scratch_dir()
    _mem = CONFIG.setdefault("memory", {})
    _db = _mem.get("db_path") or os.path.join(ROOT, "data", "ishti.db")
    _mem["db_path"] = _test_mode.scratch_path(_db)
    _mem["chroma_dir"] = os.path.join(_scratch, os.path.basename(str(_mem.get("chroma_dir") or "chroma")))
    del _scratch, _mem, _db

# Privacy: no telemetry, offline model hubs, no update pings - set before anything heavy is imported (docs/SAFETY.md).
from ishti import privacy as _privacy  # noqa: E402
_privacy.harden()
