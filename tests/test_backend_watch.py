"""Regression for the orphaned desktop processes after an external stop (found 2026-09-29).

POST /api/shutdown stopped the backend and the watchdog, but desktop/launcher.py, desktop/tray.py
and the app window never learned about it: the launcher kept a window on a dead backend and tray
icons piled up across launches ("Stop is unreliable"). Fix: desktop/backend_watch.py - every
desktop process that outlives the backend exits once the backend has been up and then stays gone
past a grace period.
"""
import os
import re
import sys
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from desktop import backend_watch  # noqa: E402


def test_watcher_stays_silent_while_backend_never_came_up(monkeypatch, tmp_path):
    """A normal boot or a down backend must NOT fire on_lost (the splash owns the error case)."""
    monkeypatch.setattr(backend_watch, "shutdown_flag_path", lambda: str(tmp_path / "flag"))  # no real flag
    monkeypatch.setattr(backend_watch, "health_ok", lambda *a, **k: False)
    fired = []
    t = backend_watch.start(lambda: fired.append(True), grace_s=0.5, poll_s=0.1)
    time.sleep(1.2)
    assert not fired, "watcher fired although the backend never came up"


def test_watcher_fires_only_after_the_backend_stays_gone(monkeypatch, tmp_path):
    """Backend up -> goes down -> on_lost fires once, after the grace period, not during a blip."""
    monkeypatch.setattr(backend_watch, "shutdown_flag_path", lambda: str(tmp_path / "flag"))  # no real flag
    state = {"up": True}
    monkeypatch.setattr(backend_watch, "health_ok", lambda *a, **k: state["up"])
    fired = []
    t = backend_watch.start(lambda: fired.append(True), grace_s=0.6, poll_s=0.1)
    time.sleep(0.5)                     # up phase: must not fire
    assert not fired
    state["up"] = False
    time.sleep(0.3)                     # inside the grace period (a watchdog restart dips health)
    assert not fired, "fired during the grace period"
    time.sleep(0.8)                     # past the grace period
    assert fired == [True], fired
    t.join(timeout=1)


def test_watcher_resets_when_the_backend_comes_back(monkeypatch, tmp_path):
    """A short dip (watchdog restart) that recovers must not fire."""
    monkeypatch.setattr(backend_watch, "shutdown_flag_path", lambda: str(tmp_path / "flag"))  # no real flag
    state = {"up": True}
    monkeypatch.setattr(backend_watch, "health_ok", lambda *a, **k: state["up"])
    fired = []
    t = backend_watch.start(lambda: fired.append(True), grace_s=0.6, poll_s=0.1)
    time.sleep(0.4)
    state["up"] = False
    time.sleep(0.3)                     # longer than poll, shorter than grace
    state["up"] = True
    time.sleep(0.8)                     # if down_since had not reset, this would fire
    assert not fired, "a recovered blip must not be treated as a lost backend"
    t.join(timeout=0.1)


def test_shutdown_flag_closes_the_shell_quickly(monkeypatch, tmp_path):
    """Bug 2 (the owner's screenshot): the overlay hit 100% but the window stayed. The backend now
    writes shutting_down.flag; the watcher must close the shell on seeing it - fast, without waiting
    for the backend-gone grace period, and without any JS bridge involved."""
    monkeypatch.setattr(backend_watch, "shutdown_flag_path", lambda: str(tmp_path / "shutting_down.flag"))
    monkeypatch.setattr(backend_watch, "health_ok", lambda *a, **k: False)   # backend busy shutting down
    fired = []
    t = backend_watch.start(lambda: fired.append(True), grace_s=60)          # gone-grace would never fire
    time.sleep(0.2)
    assert not fired
    open(tmp_path / "shutting_down.flag", "w").write("x")
    time.sleep(backend_watch.FLAG_GRACE_S + backend_watch.POLL_S + 2.0)   # detection poll + grace + headroom
    assert fired == [True], "the shutdown flag did not close the shell"
    t.join(timeout=0.1)


def test_shutdown_flag_path_matches_lifecycle(tmp_path):
    """The desktop-side path must point at exactly the file the backend writes."""
    from ishti import lifecycle
    assert os.path.normcase(backend_watch.shutdown_flag_path()) == os.path.normcase(lifecycle.SHUTDOWN_FLAG)
    lifecycle._write_shutdown_flag()
    assert os.path.isfile(lifecycle.SHUTDOWN_FLAG)
    lifecycle.clear_shutdown_flag()
    assert not os.path.isfile(lifecycle.SHUTDOWN_FLAG)


def test_flag_is_cleared_on_a_fresh_boot():
    """A new ISHTI start must remove the previous run's flag, or the window would close instantly."""
    from ishti import lifecycle
    lifecycle._write_shutdown_flag()
    lifecycle.clear_shutdown_flag()
    src = open(os.path.join(ROOT, "ishti", "lifecycle.py"), encoding="utf-8").read()
    assert "clear_shutdown_flag()" in src.split("def start(", 1)[1][:400], "start() no longer clears the flag"


def test_backend_closes_the_shell_at_os_level():
    """The 2026-09-30 field proof: a wedged window ("ISHTI (Not Responding)") never runs its Python
    watcher threads, so in-process close paths cannot be trusted. The backend must close the recorded
    shell pids itself during the shutdown sequence."""
    from ishti import lifecycle
    assert hasattr(lifecycle, "record_shell_pids") and hasattr(lifecycle, "stop_shell"), \
        "backend-side shell close disappeared"
    src = open(os.path.join(ROOT, "ishti", "lifecycle.py"), encoding="utf-8").read()
    seq = src.split("def _shutdown_sequence", 1)[1]
    assert "stop_shell()" in seq, "the shutdown sequence no longer closes the desktop shell"
    # stop_shell must kill by recorded pid + creation time, and never kill the backend itself
    ss = src.split("def stop_shell", 1)[1].split("\ndef ", 1)[0]
    assert "shell_pids" in ss and "_alive(" in ss, "kill-by-name crept in; must stay pid+creation-time"
    assert "pid == me" in ss, "stop_shell must skip the backend process"
    launcher = open(os.path.join(ROOT, "desktop", "launcher.py"), encoding="utf-8").read()
    assert "record_shell_pids" in launcher, "the launcher no longer registers itself + tray with the backend"


def test_desktop_processes_wired_to_the_watcher():
    src_launcher = open(os.path.join(ROOT, "desktop", "launcher.py"), encoding="utf-8").read()
    src_tray = open(os.path.join(ROOT, "desktop", "tray.py"), encoding="utf-8").read()
    src_window = open(os.path.join(ROOT, "desktop", "app_window.py"), encoding="utf-8").read()
    assert "backend_watch.start" in src_launcher, "the launcher no longer watches the backend"
    assert "backend_watch.start" in src_tray, "the tray no longer watches the backend"
    assert "backend_watch.start" in src_window, "the app window no longer watches the backend"
    # Bug found in the field: the launcher's on_lost printed before os._exit - under pythonw sys.stdout
    # is None, the print raised AttributeError first, the watcher swallowed it, and the window never
    # closed. The callback must be a bare os._exit with no print in front.
    m = re.search(r"backend_watch\.start\((.*?)\)", src_launcher, re.S)
    assert m, "launcher watcher call disappeared"
    cb = m.group(1)
    assert "print" not in cb, f"launcher on_lost still prints (breaks under pythonw): {cb!r}"
    assert "os._exit" in cb
    # a fresh launch must clear the previous run's flag or the window closes itself at boot
    assert "clear_flag" in src_launcher, "the launcher does not clear the stale shutdown flag"
    # hard exit must be armed BEFORE any destroy call that could block
    lost = src_window[src_window.index("def _on_backend_lost"):]
    lost = lost[:lost.index("\ndef ", 1)]
    assert lost.index("_schedule_hard_exit") < lost.index("_destroy_all_windows"), \
        "hard exit must be armed before the possibly-blocking destroy"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
