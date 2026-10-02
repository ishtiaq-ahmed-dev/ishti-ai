"""SC-10: the shipped Laya checkpoint's calibration warning.

The checkpoint ships a fitted temperature of 0.1006 for the "choice, 11+ options" bucket (ISHTI's skill choice has
~120 options). Laya clamps every temperature into [0.5, 5] - sharpening below 0.5 is outside what the library
treats as safe - and says so with a RuntimeWarning on every load.

Decision (kept, not worked around): ISHTI keeps Laya's conservative clamp. The router's min_confidence (0.2) and the
labelled routing set (ishti.check, 333 requests) were measured WITH the clamped values, so sharper confidences would
silently shift every threshold. The warning itself is pure noise for the owner, so ishti/router.py filters exactly
that message once at import (main thread; catch_warnings() is not thread-safe and routing runs in parallel plan
steps). This test pins both halves: the library still warns (so we notice if a new checkpoint fixes it and the
filter can go) and ISHTI's import silences it.
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CODE = """
import warnings
{pre}
from ishti import CONFIG
from laya import Router
Router(max_loaded=1, device="cpu").load(CONFIG["router"]["laya_model"])
print("LOADED")
"""


def _load(pre):
    p = subprocess.run([sys.executable, "-W", "always", "-c", CODE.format(pre=pre)], cwd=ROOT, capture_output=True,
                       text=True, timeout=180, env={**os.environ, "HF_HUB_OFFLINE": "1"})
    assert "LOADED" in p.stdout, p.stderr[-500:]
    return p.stderr


def test_library_still_warns_without_ishti_router():
    assert "invalid temperatures" in _load("")      # if this ever fails the checkpoint is fixed: drop the filter


def test_importing_ishti_router_silences_the_calibration_warning():
    assert "invalid temperatures" not in _load("import ishti.router")
