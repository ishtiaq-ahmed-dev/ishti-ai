"""FIX-6 regression (QA prompt 3 chaos): orphaned llama-server.exe must be reapable.

Measured failure: killing ollama.exe hard (crash/chaos) left 3 llama-server.exe children
alive holding ~8.9 GB VRAM; the restarted daemon then could not fit any model (honest
GpuBusy on every turn, ~56 s each). self_heal.reap_orphaned_llama_servers() kills only
servers whose parent is NOT a live ollama* process, and trigger_heal runs it before the
connectivity check so the fresh daemon gets its memory back.

The tests are hermetic: fake processes, no real Ollama touched.
"""
import os
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.environ.setdefault("ISHTI_TEST_MODE", "1")

from ishti import self_heal  # noqa: E402


class _FakeProc:
    def __init__(self, pid, name, ppid):
        self.info = {"pid": pid, "name": name, "ppid": ppid}
        self.killed = False

    def kill(self):
        self.killed = True


def test_reaper_kills_only_orphans(monkeypatch):
    live_daemon = _FakeProc(100, "ollama.exe", 1)
    live_server = _FakeProc(200, "llama-server.exe", 100)     # parent alive -> untouched
    orphan_a = _FakeProc(300, "llama-server.exe", 999)        # parent dead -> reaped
    orphan_b = _FakeProc(301, "llama-server.exe", 888)        # parent dead -> reaped
    unrelated = _FakeProc(400, "chrome.exe", 100)             # not a server -> untouched

    monkeypatch.setattr(self_heal, "psutil", None, raising=False)
    import types
    fake_psutil = types.SimpleNamespace(
        process_iter=lambda attrs: [live_daemon, live_server, orphan_a, orphan_b, unrelated],
        NoSuchProcess=Exception, AccessDenied=Exception)
    monkeypatch.setattr("builtins.__import__",
                        lambda name, *a, **k: fake_psutil if name == "psutil"
                        else __builtins__.__import__(name, *a, **k))

    n = self_heal.reap_orphaned_llama_servers()
    assert n == 2, f"expected to reap exactly the 2 orphans, got {n}"
    assert orphan_a.killed and orphan_b.killed
    assert not live_server.killed, "reaper killed the LIVE daemon's server"
    assert not unrelated.killed


def test_trigger_heal_reaps_before_connectivity(monkeypatch):
    """Order matters: the reaper must run BEFORE the /api/tags check so the fresh daemon
    can actually fit models. Assert by call order on fakes."""
    calls = []

    monkeypatch.setattr(self_heal, "reap_orphaned_llama_servers",
                        lambda: calls.append("reap") or 3)

    class _R:
        ok = True

    fake_requests = types.SimpleNamespace(get=lambda *a, **k: calls.append("api") or _R())
    monkeypatch.setattr(self_heal, "requests", fake_requests, raising=False)

    # Stop the rest of trigger_heal from touching the world: make the search + db steps no-ops.
    monkeypatch.setattr("builtins.__import__",
                        lambda name, *a, **k: (_R if name == "requests" else real_import(name, *a, **k)))

    self_heal.trigger_heal()
    assert calls[:2] == ["reap", "api"], f"reaper must run before connectivity check: {calls}"


import builtins  # noqa: E402

real_import = builtins.__import__
