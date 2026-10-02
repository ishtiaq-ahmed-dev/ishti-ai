"""Lint is a gate now (CD-11 / N19): `ruff check` with ruff.toml's small bug-only rule set must report nothing.
Skipped (with the reason) when ruff is not installed; ruff is a developer tool, not a runtime dependency."""
import os
import shutil
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _ruff():
    exe = shutil.which("ruff") or os.path.join(os.path.dirname(sys.executable), "ruff.exe" if os.name == "nt" else "ruff")
    return exe if os.path.isfile(exe) else None


def test_ruff_reports_no_bugs():
    exe = _ruff()
    if not exe:
        pytest.skip("ruff is not installed (pip install ruff)")
    r = subprocess.run([exe, "check", "--output-format", "concise", "--no-cache", "."], cwd=ROOT, capture_output=True,
                       text=True, timeout=300)
    assert r.returncode == 0, "ruff found real bugs (undefined names, redefinitions, syntax):\n" + r.stdout[-3000:]
