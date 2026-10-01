"""desktop_control skill (WP-B2) — INERT until the cua-driver exists and the owner enables it.

Safety architecture (CUA_FEASIBILITY.md + R3):
  - manifest status "disabled" -> the router never selects it until the owner enables it (T2 act)
  - _blocker(): if the cua-driver is not installed, every request answers honestly and does nothing
  - tier split: read-only inspection = T1 (manifest tier); ANY side-effect action = T2 via ask_first,
    with the exact recap of the target app, the action, and the value
  - BLOCKED_APPS are never driven (password managers, banking, security tools, system settings)
  - remote channels refuse this skill by name (gate FORBIDDEN classifier + policy desktop_control_remote)
  - a kill-switch hook aborts any in-flight driver sequence (killswitch.on_kill)
"""
from __future__ import annotations

import json
import os
import re
import shutil

from ishti import CONFIG
from ishti.skills import ask_first, trace

CONF_PATH = os.path.join(os.path.dirname(CONFIG["memory"]["db_path"]), "desktop_control.json")

BLOCKED_APPS = {
    "keepass", "1password", "bitwarden", "lastpass", "enpass",            # password managers
    "banking", "bank", "paypal", "broker", "metatrader", "trading",       # money
    "defender", "antivirus", "firewall", "malwarebytes", "regedit",       # security
    "settings", "control panel", "services.msc", "task scheduler",        # system config
    "credential manager", "secpol", "gpedit",
}
BLOCKED_RE = re.compile("|".join(re.escape(a) for a in BLOCKED_APPS), re.I)

INSPECT = re.compile(r"\b(?:what(?:'s| is| does)|read|check|inspect|list|summarize|show)\b[^.?!]{0,40}"
                     r"\b(?:window|app|application|dialog|notepad|calculator|explorer|chrome|edge)\b", re.I)
ACT = re.compile(r"\b(?:click|type|press|enter|write|fill|select|open|close|scroll|copy|paste)\b", re.I)
TARGET = re.compile(r"\b(?:in|into|on)\s+(?:the\s+)?([A-Za-z][\w ]{0,24})"
                    r"|\b(notepad|calculator|explorer|paint|wordpad|mspaint|cmd|powershell|terminal|chrome|edge|firefox)\b", re.I)


def _driver_available() -> bool:
    """The cua-driver CLI on PATH (checked, not assumed)."""
    return bool(shutil.which("cua") or shutil.which("cua-driver") or os.environ.get("CUA_DRIVER_TOKEN"))


def _manifest_status() -> str:
    """The live manifest status (the loader's enable switch)."""
    try:
        with open(os.path.join(os.path.dirname(__file__), "manifest.json"), encoding="utf-8") as f:
            return json.load(f).get("status", "disabled")
    except Exception:
        return "disabled"


def _config() -> dict:
    """Local-only allowlist the owner edits via the desktop (never via a remote message)."""
    try:
        with open(CONF_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"allowed_apps": [], "max_steps": 15, "max_seconds": 120}


def _app_allowed(app: str, cfg: dict) -> bool:
    if not app:
        return False
    if BLOCKED_RE.search(app):
        return False
    allowed = cfg.get("allowed_apps") or []
    return any(app.strip().lower() == a.lower() for a in allowed)


def _blocker() -> str | None:
    if not _driver_available():
        return ("The cua-driver is not installed on this PC. See CUA_FEASIBILITY.md - installing it "
                "needs your explicit approval first.")
    return None


# ------------------------------------------------------------------ kill-switch wiring
def _install_kill_hook():
    if getattr(_install_kill_hook, "done", False):
        return
    from ishti import killswitch

    @killswitch.on_kill
    def _abort_driver():
        seq = _state.get("running")
        if seq:
            _state["running"] = None
            return "desktop_control sequence aborted"
        return None

    _install_kill_hook.done = True


_state = {"running": None}


def run(text: str, session) -> dict:
    trace(session, "desktop_control", "manifest disabled / driver gate check", status="start")
    block = _blocker()
    if block:
        return {"reply": block, "sources": []}
    pol_status = _manifest_status()  # the manifest gate; the loader only routes enabled skills
    if pol_status != "working":
        return {"reply": "Desktop control is disabled. Enable it in Settings -> Skills (it needs "
                         "your explicit yes), then ask again.", "sources": []}

    m = TARGET.search(text)
    app = (next((g for g in (m.groups() if m else []) if g), "") if m else "").strip()
    cfg = _config()
    if not _app_allowed(app, cfg):
        blocked = bool(app and BLOCKED_RE.search(app))
        trace(session, "desktop_control", f"app not allowed: {app or '?'}", status="error")
        return {"reply": (f"'{app or 'that app'}' is {'never driven (blocked app)' if blocked else 'not on the allowlist'}. "
                          "Add it in Settings -> Skills -> desktop control, locally."), "sources": []}

    if INSPECT.search(text) and not ACT.search(text):
        return _inspect(text, session, app, cfg)
    if ACT.search(text):
        return _act(text, session, app, cfg)
    return {"reply": "Tell me what to inspect (T1, read-only) or what to do (I'll confirm the exact "
                     "action first).", "sources": []}


def _inspect(text, session, app, cfg) -> dict:
    """T1: read the UIA tree of the target window. Never types, never clicks."""
    _install_kill_hook()
    _state["running"] = "inspect"
    try:
        # The driver call goes here when installed; until then the honest answer is the blocker.
        trace(session, "desktop_control", "inspect (driver call pending install)", status="done")
        return {"reply": "(desktop_control) Driver present but the inspection runtime is not wired "
                         "yet - this skill is enabled only after the cua feasibility protocol passes "
                         "(CUA_FEASIBILITY.md).", "sources": []}
    finally:
        _state["running"] = None


def _act(text, session, app, cfg) -> dict:
    """T2: exact recap -> owner's yes -> bounded sequence; kill switch aborts mid-flight."""
    _install_kill_hook()

    def action():
        _state["running"] = "act"
        try:
            return {"reply": "(desktop_control) Approved action recorded; the execution runtime is "
                             "not wired yet - this skill stays disabled until the feasibility "
                             "protocol passes.", "sources": []}
        finally:
            _state["running"] = None

    return ask_first(session, "desktop_control",
                     f"In {app}: {text[:120]} (max {cfg.get('max_steps')} steps, "
                     f"{cfg.get('max_seconds')}s)", action)


if __name__ == "__main__":
    # self-checks (offline, no driver, no model): gates fire in the right order
    class FakeSession(dict):
        def get(self, k, d=None):
            return dict.get(self, k, d)

    s = FakeSession(name="desktop_control", manifest={"tier": "T1", "priority": 12, "route": "x"},
                    status="disabled", trace=lambda **k: None)
    r = run("what does the Calculator window show", s)
    assert "not installed" in r["reply"] or "disabled" in r["reply"], r
    # the manifest gate
    assert json.load(open(os.path.join(os.path.dirname(__file__), "manifest.json")))["status"] == "disabled"
    # blocked apps are rejected even when everything else is on
    cfg = {"allowed_apps": ["keepass", "calculator", "chrome"], "max_steps": 5, "max_seconds": 30}
    assert not _app_allowed("keepass", cfg) and not _app_allowed("Banking App", cfg)
    assert _app_allowed("calculator", cfg)
    assert _app_allowed("", cfg) is False
    # kill hook installs once and can abort
    _install_kill_hook()
    _install_kill_hook()
    _state["running"] = "act"
    from ishti import killswitch
    stopped = killswitch.kill("self-test")
    assert _state["running"] is None and any("desktop_control" in x for x in stopped), stopped
    print("[self-check] desktop_control OK - disabled by default, blocker honest, blocked apps rejected, kill aborts")
