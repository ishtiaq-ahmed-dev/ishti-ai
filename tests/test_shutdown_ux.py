"""Regression for the owner's shutdown UX requests (2026-09-29).

1. The shutdown / boot screens speak plain language - no programmer words (VRAM, RTX, ports,
   orphaned processes, backend). Owner: "these are for the programmers, not for the real user".
2. The shutdown overlay uses the app's primary (accent) color, not amber/yellow.
3. There is no "Stop ISHTI" desktop shortcut - the app's own Shut down button / X closes ISHTI.
4. Closing is guaranteed: force_close ends the process even if the destroy call is swallowed
   (the bug where the overlay hit 100% but the window stayed).
"""
import os
import re
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

APP = os.path.join(ROOT, "interface", "app", "src", "App.tsx")
LIFECYCLE = os.path.join(ROOT, "ishti", "lifecycle.py")
MOCK = os.path.join(ROOT, "desktop", "mock_server.py")
SHORTCUTS = os.path.join(ROOT, "desktop", "create_shortcut.py")
APP_WINDOW = os.path.join(ROOT, "desktop", "app_window.py")
SPLASH = os.path.join(ROOT, "desktop", "splash.html")

JARGON = ("VRAM", "RTX", "orphaned", "GPU", "backend", "ports", "models", "process tree")


def test_shutdown_labels_are_plain_language():
    """Every shutdown step label the backend sends must be understandable by a normal person."""
    src = open(LIFECYCLE, encoding="utf-8").read()
    m = re.search(r"SHUTDOWN_STEPS\s*=\s*\[(.*?)\]", src, re.S)
    assert m, "SHUTDOWN_STEPS disappeared from lifecycle.py"
    labels = re.findall(r'"([^"]+)"\)', m.group(1)) or re.findall(r',\s*"([^"]+)"', m.group(1))
    assert len(labels) == 7, labels
    for label in labels:
        for word in JARGON:
            assert word.lower() not in label.lower(), f"jargon on the shutdown screen: {label!r} has {word!r}"
    assert labels[-1].startswith("All done"), labels[-1]
    # the mock server (used by tests/dev) mirrors the same labels
    mock = open(MOCK, encoding="utf-8").read()
    assert "All done. Goodbye!" in mock and "Releasing the graphics card" not in mock


def test_shutdown_overlay_uses_primary_color_and_plain_words():
    src = open(APP, encoding="utf-8").read()
    overlay = src[src.index("Unified Shutdown Progress Overlay"):]
    overlay = overlay[:overlay.index("Model Switch Notification")] if "Model Switch Notification" in overlay else overlay
    for forbidden in ("amber-", "RTX", "orphaned", "Releasing"):
        assert forbidden not in overlay, f"shutdown overlay still shows {forbidden!r}"
    assert "accent" in overlay, "the overlay must use the primary (accent) color"
    assert "see you soon" in overlay.lower() or "Everything is saved" in overlay
    # the default progress message must not be jargon either
    assert "Releasing RTX" not in src and "Zero orphaned processes" not in src


def test_exit_confirm_dialog_is_plain():
    src = open(APP, encoding="utf-8").read()
    for forbidden in ("Clean GPU release", "terminate background processes", "unload AI models"):
        assert forbidden not in src, forbidden


def test_no_stop_ishti_desktop_shortcut():
    src = open(SHORTCUTS, encoding="utf-8").read()
    assert "create_stop_shortcut" not in src, "the Stop shortcut creator is back"
    assert "remove_stop_shortcut" in src, "stale Stop shortcuts are no longer cleaned up"
    import win32com.client
    ws = win32com.client.Dispatch("WScript.Shell")
    lnk = os.path.join(ws.SpecialFolders("Desktop"), "Stop ISHTI.lnk")
    assert not os.path.exists(lnk), f"{lnk} exists - run desktop/create_shortcut.py to remove it"
    # stop.bat stays in the repo for developers; it is just not on the desktop as a shortcut


def test_window_close_is_guaranteed():
    src = open(APP_WINDOW, encoding="utf-8").read()
    assert "_schedule_hard_exit" in src, "the guaranteed-close hammer disappeared"
    # force_close must both try to destroy and schedule the hard exit
    fc = src[src.index("def force_close"):]
    fc = fc[:fc.index("\ndef ", 1)]
    assert "destroy()" in fc and "_schedule_hard_exit" in fc


def test_splash_error_text_is_plain():
    src = open(SPLASH, encoding="utf-8").read()
    assert "assistant backend" not in src.lower(), "splash error still says 'assistant backend'"
    assert "having trouble starting" in src


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
