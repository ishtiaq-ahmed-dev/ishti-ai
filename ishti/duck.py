"""Audio ducking (B5): while ISHTI speaks, other apps' sound (music, a video) drops to DUCK_LEVEL and comes back
afterwards; a wake word or barge-in gives a short duck so the hand-over is clean. Per-app volumes through pycaw
(Windows audio sessions); ISHTI's own process is never ducked. Reference-counted: overlapping ducks restore once.
No audio device (or no pycaw) -> nothing happens, nothing breaks."""
import os
import threading
import time

try:  # best-effort failure log (never breaks the module if logging is unavailable)
    from ishti.core.logutil import swallowed
except Exception:  # pragma: no cover
    def swallowed(*_a, **_k):
        return None


DUCK_LEVEL = 0.25
_lock = threading.Lock()
_holders = 0
_saved = {}  # session pid -> original volume


def _sessions():
    from pycaw.pycaw import AudioUtilities
    me = os.getpid()
    for s in AudioUtilities.GetAllSessions():
        if s.Process and s.Process.pid != me and s.Process.name().lower() not in ("python.exe", "pythonw.exe"):
            yield s


def start():
    """Duck other apps (nested calls just count)."""
    global _holders
    with _lock:
        _holders += 1
        if _holders > 1:
            return
        try:
            for s in _sessions():
                vol = s.SimpleAudioVolume
                level = vol.GetMasterVolume()
                if level > DUCK_LEVEL:
                    _saved[s.Process.pid] = level
                    vol.SetMasterVolume(DUCK_LEVEL, None)
        except Exception as _exc:
            swallowed('ishti/duck.py:start', _exc)


def stop():
    """Undo one start(); the last one restores every app's own volume."""
    global _holders
    with _lock:
        _holders = max(0, _holders - 1)
        if _holders:
            return
        try:
            for s in _sessions():
                if s.Process.pid in _saved:
                    s.SimpleAudioVolume.SetMasterVolume(_saved[s.Process.pid], None)
        except Exception as _exc:
            swallowed('ishti/duck.py:stop', _exc)
        _saved.clear()


def brief(seconds=1.5):
    """A short duck for a wake word / barge-in."""
    start()
    threading.Timer(seconds, stop).start()


if __name__ == "__main__":
    calls = []

    class Vol:
        def __init__(self, v):
            self.v = v

        def GetMasterVolume(self):
            return self.v

        def SetMasterVolume(self, v, _):
            calls.append(v)
            self.v = v

    class Proc:
        def __init__(self, pid, name):
            self.pid, self._n = pid, name

        def name(self):
            return self._n

    class Sess:
        def __init__(self, pid, name, v):
            self.Process, self.SimpleAudioVolume = Proc(pid, name), Vol(v)

    fake = [Sess(101, "spotify.exe", 0.8), Sess(102, "chrome.exe", 0.1)]
    _sessions = lambda: iter(fake)  # noqa: E731 - test double for Windows audio sessions
    start()
    start()  # nested (a wake duck during speech)
    assert fake[0].SimpleAudioVolume.v == DUCK_LEVEL and fake[1].SimpleAudioVolume.v == 0.1  # quiet apps untouched
    stop()
    assert fake[0].SimpleAudioVolume.v == DUCK_LEVEL  # still held once
    stop()
    assert fake[0].SimpleAudioVolume.v == 0.8 and not _saved
    print("[self-check] duck OK - other apps ducked to 25%, nested ducks restore once, quiet apps untouched")
