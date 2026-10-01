"""desktop_control skill suite (generated-suite convention: happy/language/edge/failure/safety)."""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, ROOT)
os.environ.setdefault("ISHTI_STATE_DB", os.path.join(__import__("tempfile").mkdtemp(prefix="dc_state_"), "state.db"))
os.environ["ISHTI_SETTINGS_DIR"] = __import__("tempfile").mkdtemp(prefix="dc_settings_")

from ishti.skills.desktop_control import skill as DC  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh(tmp_path, monkeypatch):
    monkeypatch.setattr(DC, "CONF_PATH", str(tmp_path / "dc.json"))
    monkeypatch.setattr(DC, "_state", {"running": None})
    yield


class S(dict):
    def __init__(self):
        super().__init__()
        self.name = "desktop_control"
        self.manifest = {"tier": "T1", "priority": 12, "route": "native app"}
        self.status = "disabled"
        self.trace = lambda **k: None

    def get(self, k, d=None):
        return dict.get(self, k, d)


def test_manifest_disabled_by_default():
    import json
    m = json.load(open(os.path.join(ROOT, "ishti", "skills", "desktop_control", "manifest.json"), encoding="utf-8"))
    assert m["status"] == "disabled" and m["tier"] == "T1"
    assert m.get("x_remote_forbidden") is True


def test_missing_driver_answered_honestly():
    assert DC._driver_available() is False  # test env has no driver
    r = DC.run("what does the Calculator window show", S())
    assert "cua-driver is not installed" in r["reply"]


def test_blocked_apps_rejected_even_when_allowed():
    cfg = {"allowed_apps": ["keepass", "calculator"], "max_steps": 5, "max_seconds": 30}
    assert DC._app_allowed("keepass", cfg) is False
    assert DC._app_allowed("KeePass 2", cfg) is False
    assert DC._app_allowed("Metatrader 5", cfg) is False
    assert DC._app_allowed("calculator", cfg) is True


def test_not_on_allowlist_rejected(monkeypatch):
    monkeypatch.setattr(DC, "_driver_available", lambda: True)
    monkeypatch.setattr(DC, "_manifest_status", lambda: "working")
    r = DC.run("click the Send button in Notepad", S())
    assert "not on the allowlist" in r["reply"]


def test_inspect_is_t1_no_gate(monkeypatch):
    monkeypatch.setattr(DC, "_driver_available", lambda: True)
    monkeypatch.setattr(DC, "_manifest_status", lambda: "working")  # owner-enabled
    monkeypatch.setattr(DC, "_config", lambda: {"allowed_apps": ["calculator"], "max_steps": 5, "max_seconds": 30})
    r = DC.run("what does the calculator window show", S())
    assert "(desktop_control)" in r["reply"]  # inspection path, no yes/no gate


def test_act_requires_yes(monkeypatch):
    monkeypatch.setattr(DC, "_driver_available", lambda: True)
    monkeypatch.setattr(DC, "_manifest_status", lambda: "working")  # owner-enabled
    monkeypatch.setattr(DC, "_config", lambda: {"allowed_apps": ["calculator"], "max_steps": 5, "max_seconds": 30})
    s = S()
    s["pending"] = None
    r = DC.run("type hello into calculator", s)
    assert "yes/no" in r["reply"], r  # T2 gate asked
    assert s["pending"]["skill"] == "desktop_control"


def test_kill_aborts_running_sequence():
    DC._install_kill_hook()
    DC._install_kill_hook()  # idempotent
    from ishti import killswitch
    DC._state["running"] = "act"
    stopped = killswitch.kill("test")
    assert DC._state["running"] is None and any("desktop_control" in x for x in stopped)
