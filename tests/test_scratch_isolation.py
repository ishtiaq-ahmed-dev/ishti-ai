"""Scratch isolation contract (P9 finding: a live QA run still wrote 18 files into the real data/).

With ISHTI_TEST_MODE=1 + ISHTI_SCRATCH=1 every KNOWN writable store must resolve inside the scratch
dir - the real data/ dir must not appear anywhere in the resolved paths. The QA server, the seeder and
the pytest suite all share this switch; a new store that forgets it would write into the owner's real
data, so this test fails loudly instead.

The probe runs in a subprocess so module-level path binding happens under the env exactly as it does
for the server (import order is part of what was broken).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REAL_DATA = os.path.join(ROOT, "data")

PROBE = textwrap.dedent(
    r"""
    import importlib, json, os, sys
    sys.path.insert(0, sys.argv[1])
    import ishti
    from ishti import corrections, mic_control, models, monitor, privacy, self_heal, vram
    from ishti.core import state
    from ishti.skills import needs_path
    mods = {n: importlib.import_module("ishti.skills." + n + ".skill")
            for n in ("notes", "lifelog", "chat_digest", "calendar", "ledger")}
    more = importlib.import_module("ishti.skills.desktop.more")
    overlay = importlib.import_module("desktop.overlay.log")
    out = {
        "config.db_path": ishti.CONFIG["memory"]["db_path"],
        "config.chroma_dir": ishti.CONFIG["memory"]["chroma_dir"],
        "convo_state": state.default_db_path(),
        "models": models.DB,
        "monitor": monitor.CORRECTIONS_PATH,
        "vram": vram.DB,
        "self_heal": self_heal.STATE_PATH,
        "reflections": self_heal.REFLECTIONS_PATH,
        "mic": mic_control.STATE_PATH,
        "privacy_ledger": privacy.LEDGER_PATH,
        "corrections": corrections.EXPORT,
        "notes": mods["notes"].DB,
        "lifelog": mods["lifelog"].DB,
        "chat_digest_last": mods["chat_digest"].LAST_MD,
        "calendar": mods["calendar"].DB,
        "ledger": mods["ledger"].DB,
        "clipboard": more.CLIP_DB,
        "needs": needs_path(),
        "overlay": overlay.LOG_FILE,
    }
    print("PATHS:" + json.dumps(out))
    """
)


def _probe(scratch_dir: str) -> dict:
    env = dict(os.environ)
    # Other test modules set these at import time; they are per-test overrides of exactly the stores
    # this probe checks, so the probe must not inherit them.
    for leaked in ("ISHTI_NEEDS_LOG", "ISHTI_STATE_DB", "ISHTI_SETTINGS_DIR"):
        env.pop(leaked, None)
    env.update({"ISHTI_TEST_MODE": "1", "ISHTI_SCRATCH": "1", "ISHTI_SCRATCH_DIR": scratch_dir})
    proc = subprocess.run([sys.executable, "-c", PROBE, ROOT], env=env, capture_output=True,
                          text=True, timeout=240)
    assert proc.returncode == 0, proc.stderr[-2000:]
    line = next((ln for ln in proc.stdout.splitlines() if ln.startswith("PATHS:")), None)
    assert line, proc.stdout[-2000:]
    return json.loads(line[len("PATHS:"):])


def test_every_writable_store_resolves_under_the_scratch_dir(tmp_path):
    scratch = str(tmp_path / "scratch")
    paths = _probe(scratch)
    prefix = os.path.abspath(scratch).lower() + os.sep
    missing = {k: p for k, p in paths.items()
               if not os.path.abspath(str(p)).lower().startswith(prefix)}
    assert not missing, f"stores not redirected to the scratch dir: {missing}"
    real_prefix = os.path.abspath(REAL_DATA).lower() + os.sep
    leaked = {k: p for k, p in paths.items()
              if os.path.abspath(str(p)).lower().startswith(real_prefix)}
    assert not leaked, f"stores still resolve into the real data dir: {leaked}"


def test_scratch_dir_is_empty_when_the_switch_is_off():
    env = dict(os.environ)
    for key in ("ISHTI_TEST_MODE", "ISHTI_SCRATCH", "ISHTI_SCRATCH_DIR"):
        env.pop(key, None)
    code = ("import sys; sys.path.insert(0, sys.argv[1]); from ishti import test_mode; "
            "print('SD:' + test_mode.scratch_dir())")
    proc = subprocess.run([sys.executable, "-c", code, ROOT], env=env, capture_output=True,
                          text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert "SD:" in proc.stdout, proc.stdout[-2000:]
    assert proc.stdout.split("SD:", 1)[1].strip() == ""
