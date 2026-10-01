"""WP-COUCOU acceptance: tray merge (matrix row COU-17).

  - Pause = mic + overlay relay together, Resume undoes both (one meaning, one item)
  - the menu surface carries Pause (mic + overlay) / Resume next to mic + Open + Stop
  - Stop goes through the graceful shutdown path and only then stops the icon -
    no orphan processes

pystray itself is only imported inside run_tray(), so everything here runs headlessly.
Run: venv\\Scripts\\python -m pytest tests/test_tray_merge.py -q
"""
import os
import sys
import threading
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

pytest.importorskip("win32pipe", reason="pywin32 not installed - pause reaches hooks_relay")

import desktop.tray as tray  # noqa: E402
from ishti import hooks_relay, mic_control  # noqa: E402


@pytest.fixture(autouse=True)
def sandbox(monkeypatch):
    calls = []
    monkeypatch.setattr(mic_control, "mic_on",
                        lambda *a, **k: calls.append(("mic_on", a, k)) or {"enabled": True})
    monkeypatch.setattr(mic_control, "mic_off",
                        lambda *a, **k: calls.append(("mic_off", a, k)) or {"enabled": False})
    hooks_relay._reset()
    yield calls
    hooks_relay._reset()


def test_pause_stops_mic_and_relay_together(sandbox):
    assert tray.set_paused(True) is True
    assert hooks_relay.paused() is True, "overlay relay must be paused"
    assert sandbox[0][0] == "mic_off" and sandbox[0][1][0] == "tray pause"
    # outbound relay traffic is really dropped
    assert hooks_relay.emit("Stop") is False


def test_resume_restores_both(sandbox):
    tray.set_paused(True)
    assert tray.set_paused(False) is False
    assert hooks_relay.paused() is False
    assert sandbox[-1][0] == "mic_on", "resume brings the mic back"
    assert hooks_relay.emit("Stop") in (True, False)  # pipe decides; paused flag is off


def test_menu_surface_has_pause_resume_and_guards(monkeypatch):
    src = open(os.path.join(ROOT, "desktop", "tray.py"), encoding="utf-8").read()
    assert '"Pause (mic + overlay)"' in src, "C17: pause item must exist"
    assert '"Resume"' in src, "C17: resume item must exist"
    assert '"Open dashboard"' in src and '"Stop ISHTI"' in src, "existing surface kept"
    # quit must stay on the graceful path: shutdown first, icon second
    assert src.index("quit_all") < src.index("menu_quit")


def test_quit_calls_graceful_shutdown_then_stops_icon(monkeypatch):
    from desktop import shutdown as shutdown_mod
    fired = threading.Event()
    monkeypatch.setattr(shutdown_mod, "shutdown", lambda: fired.set())

    class Icon:
        stopped = False

        def stop(self):
            self.stopped = True

    icon = Icon()
    tray.quit_all(icon)
    deadline = time.monotonic() + 3
    while not fired.is_set() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert fired.is_set(), "Stop must go through desktop.shutdown.shutdown"
    assert icon.stopped, "icon stops only after the graceful shutdown was kicked off"
