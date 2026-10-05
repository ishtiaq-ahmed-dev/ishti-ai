from types import SimpleNamespace
import sys

import pytest

from ishti.skills.screen_control import skill


@pytest.fixture
def controls(monkeypatch):
    from ishti import test_mode
    state = {"value": (17, ((0, 0, 100, 100),), ("pixels",), 21, (0, 0, 100, 100))}
    calls = []
    monkeypatch.setattr(test_mode, "enabled", lambda: False)
    monkeypatch.setattr(skill, "_capture_state", lambda _point=None, **_kw: state["value"])
    monkeypatch.setattr(skill, "_cursor", lambda: (40, 50))
    monkeypatch.setattr(skill, "locate_on_screen", lambda _phrase: (40, 50))
    monkeypatch.setattr(skill, "_do", lambda op, args: calls.append((op, args)) or "Applied")
    return state, calls


@pytest.mark.parametrize("text,op", [
    ("click Save", "click"), ("type hello", "type"), ("press ctrl+c", "key"),
    ("scroll down", "scroll"), ("move mouse to 40,50", "move"),
])
def test_approval_is_required_and_consumed_once(controls, text, op):
    _, calls = controls
    session = {}
    result = skill.run(text, session)
    assert "yes/no" in result["reply"]
    assert calls == []
    action = session["pending"]["action"]
    assert action() == "Applied"
    assert [call[0] for call in calls] == [op]
    assert "already consumed" in action()
    assert len(calls) == 1


@pytest.mark.parametrize("change", ["window", "pixels", "geometry", "focus"])
def test_changed_target_consumes_approval_without_input(controls, change):
    state, calls = controls
    session = {}
    skill.run("click Save" if change == "pixels" else "type private text", session)
    action = session["pending"]["action"]
    original = state["value"]
    changes = {"window": (99, *original[1:]),
               "pixels": (17, original[1], ("changed",), *original[3:]),
               "geometry": (17, ((0, 0, 90, 100),), *original[2:]),
               "focus": (*original[:3], 22, original[4])}
    state["value"] = changes[change]
    assert "changed" in action()
    state["value"] = original
    assert "already consumed" in action()
    assert calls == []


def test_capture_unavailable_does_not_offer_executable_approval(controls, monkeypatch):
    _, calls = controls
    monkeypatch.setattr(skill, "_capture_state", lambda _point=None, **_kw: (_ for _ in ()).throw(RuntimeError()))
    session = {}
    assert "unavailable" in skill.run("press enter", session)["reply"]
    assert "pending" not in session
    assert calls == []


def test_capture_failure_after_approval_sends_no_input(controls, monkeypatch):
    _, calls = controls
    session = {}
    skill.run("scroll up", session)
    monkeypatch.setattr(skill, "_capture_state", lambda _point=None, **_kw: (_ for _ in ()).throw(RuntimeError()))
    assert "failed" in session["pending"]["action"]()
    assert calls == []


def test_test_mode_blocks_even_approved_action(controls, monkeypatch):
    from ishti import test_mode
    _, calls = controls
    session = {}
    skill.run("type hello", session)
    monkeypatch.setattr(test_mode, "enabled", lambda: True)
    assert "no real input" in session["pending"]["action"]()
    assert calls == []


def test_denial_never_runs_action(controls, monkeypatch):
    _, calls = controls
    def denied(_session, _skill, _question, _action):
        return {"reply": "Cancelled", "sources": []}
    monkeypatch.setattr(skill, "ask_first", denied)
    assert skill.run("type hello", {})["reply"] == "Cancelled"
    assert calls == []


@pytest.mark.parametrize("color,allowed", [("black", False), ("white", True), ("unrelated", True)])
def test_multiscreen_planned_pixels_and_replay_guard(tmp_path, monkeypatch, color, allowed):
    from PIL import Image
    from ishti import test_mode
    from ishti.automation import multi_screen_agent
    source = tmp_path / "planned.png"
    Image.new("RGB", (100, 100), "white").save(source)
    executed = []
    command = SimpleNamespace(action="click", x=40, y=50)
    command.to_dict = lambda: {"action": "click", "x": 40, "y": 50}
    def fresh_capture(**_kwargs):
        image = Image.new("RGB", (100, 100), "white" if color == "unrelated" else color)
        if color == "unrelated":
            image.putpixel((99, 0), (0, 0, 0))
        return image, (0, 0), ""
    agent = SimpleNamespace(
        display_mgr=SimpleNamespace(virtual_left=0, virtual_top=0),
        step=lambda _text: (command, {"screenshot": str(source)}),
        capture_engine=SimpleNamespace(capture_stitched=fresh_capture),
        executor=SimpleNamespace(execute=lambda *_a, **_kw:
                                 executed.append(True) or {"success": True}),
    )
    monkeypatch.setattr(test_mode, "enabled", lambda: False)
    monkeypatch.setattr(skill, "_foreground", lambda: 17)
    monkeypatch.setattr(skill, "_focus", lambda _window: 21)
    monkeypatch.setattr(skill, "_capture_state", lambda *_a, **_kw: (17, "target"))
    monkeypatch.setattr(multi_screen_agent, "get_multi_screen_agent", lambda: agent)
    session = {}
    skill.run("automate across both screens", session)
    action = session["pending"]["action"]
    result = action()
    assert ("Applied one approved" if allowed else "screenshot changed") in result
    assert "already consumed" in action()
    assert executed == ([True] if allowed else [])


def test_capture_rejects_monitor_gap_and_detects_foreground_race(monkeypatch):
    monitors = [{"left": 0, "top": 0, "width": 300, "height": 100},
                {"left": 0, "top": 0, "width": 100, "height": 100},
                {"left": 200, "top": 0, "width": 100, "height": 100}]
    class Capture:
        def __enter__(self):
            self.monitors = monitors
            return self
        def __exit__(self, *_args):
            return False
        def grab(self, _region):
            return SimpleNamespace(rgb=b"synthetic pixels")
    monkeypatch.setitem(sys.modules, "mss", SimpleNamespace(mss=Capture))
    windows = SimpleNamespace(GetForegroundWindow=lambda: 17, GetWindowRect=lambda _w: (0, 0, 100, 100))
    monkeypatch.setitem(sys.modules, "win32gui", windows)
    first = skill._capture_state((40, 50))
    assert first == skill._capture_state((40, 50))
    with pytest.raises(RuntimeError, match="outside"):
        skill._capture_state((150, 50))
    foreground = iter([17, 18])
    windows.GetForegroundWindow = lambda: next(foreground)
    with pytest.raises(RuntimeError, match="changed during capture"):
        skill._capture_state()


@pytest.mark.parametrize("request_text,mode", [("type hello", "type"), ("press ctrl+c", "key"),
                                                ("move mouse to 40,50", "move")])
def test_caret_blink_or_unrelated_content_does_not_invalidate_identity(monkeypatch, request_text, mode):
    from ishti import test_mode
    pixels = {"value": b"initial pixels"}
    calls = []
    class Capture:
        monitors = [{"left": 0, "top": 0, "width": 100, "height": 100}] * 2
        def __enter__(self):
            return self
        def __exit__(self, *_args):
            return False
        def grab(self, _region):
            return SimpleNamespace(rgb=pixels["value"])
    monkeypatch.setitem(sys.modules, "mss", SimpleNamespace(mss=Capture))
    monkeypatch.setitem(sys.modules, "win32gui", SimpleNamespace(
        GetForegroundWindow=lambda: 17, GetWindowRect=lambda _w: (0, 0, 100, 100)))
    monkeypatch.setattr(skill, "_focus", lambda _window: 21)
    monkeypatch.setattr(test_mode, "enabled", lambda: False)
    monkeypatch.setattr(skill, "_do", lambda op, _args: calls.append(op) or "Applied")
    session = {}
    skill.run(request_text, session)
    pixels["value"] = b"caret blink and assistant reply updated"
    assert session["pending"]["action"]() == "Applied"
    assert calls == [mode]


def test_multiscreen_unavailable_plan_does_not_create_approval(monkeypatch):
    from ishti.automation import multi_screen_agent
    command = SimpleNamespace(action="click", x=40, y=50)
    agent = SimpleNamespace(step=lambda _text: (command, {"screenshot": "missing-synthetic-file.png"}))
    monkeypatch.setattr(skill, "_foreground", lambda: 17)
    monkeypatch.setattr(skill, "_focus", lambda _window: 21)
    monkeypatch.setattr(multi_screen_agent, "get_multi_screen_agent", lambda: agent)
    session = {}
    result = skill.run("automate across both screens", session)
    assert "unavailable" in result["reply"]
    assert "pending" not in session


@pytest.mark.parametrize("changed_focus", [False, True])
def test_multiscreen_typing_uses_focus_identity_not_caret_pixels(tmp_path, monkeypatch, changed_focus):
    from PIL import Image
    from ishti import test_mode
    from ishti.automation import multi_screen_agent
    source = tmp_path / "typing-plan.png"
    Image.new("RGB", (100, 100), "white").save(source)
    focus = {"value": 21}
    executed = []
    command = SimpleNamespace(action="type", text="Synthetic text")
    agent = SimpleNamespace(
        display_mgr=SimpleNamespace(virtual_left=0, virtual_top=0),
        step=lambda _text: (command, {"screenshot": str(source)}),
        capture_engine=SimpleNamespace(capture_stitched=lambda **_kw:
                                      (Image.new("RGB", (100, 100), "black"), (0, 0), "")),
        executor=SimpleNamespace(execute=lambda *_a, **_kw: executed.append(True) or {"success": True}))
    monkeypatch.setattr(test_mode, "enabled", lambda: False)
    monkeypatch.setattr(skill, "_foreground", lambda: 17)
    monkeypatch.setattr(skill, "_focus", lambda _window: focus["value"])
    monkeypatch.setattr(skill, "_capture_state", lambda *_a, **_kw:
                        (17, ((0, 0, 100, 100),), (), focus["value"], (0, 0, 100, 100)))
    monkeypatch.setattr(multi_screen_agent, "get_multi_screen_agent", lambda: agent)
    session = {}
    skill.run("automate across both screens", session)
    if changed_focus:
        focus["value"] = 22
    result = session["pending"]["action"]()
    assert ("focus or target changed" if changed_focus else "Applied one approved") in result
    assert executed == ([] if changed_focus else [True])
