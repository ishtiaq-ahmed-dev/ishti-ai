"""Refreshing a browser tab must not stop the assistant service."""
from pathlib import Path
import re


def test_shutdown_requires_explicit_quit_or_native_close():
    source = (Path(__file__).resolve().parents[1] / "interface/app/src/App.tsx").read_text(encoding="utf-8")
    assert not re.search(r'addEventListener\(["\'](?:pagehide|beforeunload|unload)["\']', source)
    assert 'addEventListener("ishti-window-close-requested", handleCloseReq)' in source
    assert 'const handleCloseReq = () => handleCloseTrigger()' in source
    assert 'const handleQuitConfirm' in source and 'executeShutdown();' in source
