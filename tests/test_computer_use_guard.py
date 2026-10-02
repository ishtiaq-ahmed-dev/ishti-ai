"""NEEDS N21: under ISHTI_TEST_MODE=1 a direct desktop_control call never opens an app or moves a window.
(The T2 gate that blocks the side effect at the last step lives in the dispatcher; a direct run() bypassed it.)"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def test_launch_and_focus_are_blocked_in_test_mode(monkeypatch):
    monkeypatch.setenv("ISHTI_TEST_MODE", "1")
    import subprocess
    from desktop import computer_use
    opened = []
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: opened.append(a))
    monkeypatch.setattr(os, "startfile", lambda *a, **k: opened.append(a), raising=False)
    agent = computer_use.ComputerUseAgent() if hasattr(computer_use, "ComputerUseAgent") else computer_use.ComputerAgent()
    res = agent.launch_app("notepad")
    assert res["status"] == "blocked" and not opened, (res, opened)
    assert agent.focus_window("notepad") is False
