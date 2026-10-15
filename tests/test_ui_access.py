"""Accessibility-based desktop control (ishti/ui_access.py, skills ui_read and ui_act). Offline: a fake accessibility tree.
Nothing here captures a screen, clicks a position or types into a real window."""
import pytest

from ishti import ui_access
from ishti.skills.ui_act import skill as act_skill
from ishti.skills.ui_read import skill as read_skill


class Node:
    def __init__(self, name, kind, kids=(), invoke=None, value=None):
        self.Name, self.ControlTypeName = name, kind
        self._kids, self._invoke, self._value = list(kids), invoke, value

    def GetChildren(self):
        return self._kids

    def GetInvokePattern(self):
        return self._invoke

    def GetValuePattern(self):
        return self._value


class Root:
    def __init__(self, kids):
        self.kids = kids

    def GetRootControl(self):
        return Node("", "Desktop", self.kids)


class Recorder:
    def __init__(self):
        self.calls = []

    def Invoke(self):
        self.calls.append("invoke")

    def SetValue(self, value):
        self.calls.append(("set", value))


@pytest.fixture
def desk(monkeypatch):
    rec, field_rec = Recorder(), Recorder()
    save = Node("Save", "Button", invoke=rec)
    name_field = Node("Name", "Edit", value=field_rec)
    password = Node("Password", "Edit", value=field_rec)
    notepad = Node("Untitled - Notepad", "Window", [save, name_field, password, Node("Edit", "Text")])
    ishti = Node("ISHTI", "Window", [Node("OK", "Button", invoke=rec)])
    monkeypatch.setattr(ui_access, "_backend", Root([notepad, ishti]))
    return {"rec": rec, "field": field_rec, "window": notepad, "save": save, "name": name_field, "password": password}


def test_windows_lists_open_titles_and_leaves_ishti_out(desk):
    assert ui_access.windows() == ["Untitled - Notepad"]


def test_a_window_is_found_by_part_of_its_title(desk):
    assert ui_access.find_window("notepad") is desk["window"]


def test_an_unknown_window_is_refused_with_the_reason(desk):
    with pytest.raises(LookupError, match="no open window"):
        ui_access.find_window("photoshop")


def test_an_ambiguous_control_name_is_refused(desk, monkeypatch):
    extra = Node("Save as", "Button", invoke=desk["rec"])
    desk["window"]._kids.append(extra)
    with pytest.raises(LookupError, match="matches 2 controls"):
        ui_access.find_control(desk["window"], "sav")
    assert ui_access.find_control(desk["window"], "Save") is desk["save"]  # an exact name still wins


def test_real_uia_type_names_are_normalised(desk):
    """Windows UI Automation reports 'ButtonControl'; the rest of the code uses 'Button'."""
    real_print = Node("Print", "ButtonControl", invoke=desk["rec"])
    desk["window"]._kids.append(real_print)
    assert ui_access._type(real_print) == "Button"
    assert ui_access.find_control(desk["window"], "Print") is real_print


def test_clicking_a_button_invokes_its_pattern_only(desk):
    assert ui_access.act(desk["save"]) == "clicked 'Save'"
    assert desk["rec"].calls == ["invoke"]


def test_typing_sets_the_value_pattern(desk):
    ui_access.act(desk["name"], value="Ali")
    assert desk["field"].calls == [("set", "Ali")]


def test_secret_fields_are_never_touched(desk):
    with pytest.raises(PermissionError):
        ui_access.act(desk["password"], value="hunter2")
    assert desk["field"].calls == []


def test_a_control_without_a_pattern_is_refused_not_guessed(desk):
    plain = Node("Heading", "Text")
    with pytest.raises(NotImplementedError):
        ui_access.act(Node("Plain", "Button"))
    with pytest.raises(NotImplementedError):
        ui_access.act(plain, value="x")


def test_ui_act_asks_the_owner_with_the_exact_window_and_control_before_anything_runs(desk):
    session = {"id": "t-ui"}
    out = act_skill.run("click the Save button in Notepad", session)
    assert out["reply"].endswith("(yes/no)") and "'Save' in 'Untitled - Notepad'" in out["reply"]
    assert desk["rec"].calls == [], "nothing is clicked before the owner's yes"
    done = session["pending"]["action"]()
    assert "Clicked 'Save'" in done["reply"] and desk["rec"].calls == ["invoke"]


def test_ui_act_types_into_the_named_field_after_the_yes(desk):
    session = {"id": "t-ui2"}
    act_skill.run("type Ali into the Name field in Notepad", session)
    session["pending"]["action"]()
    assert desk["field"].calls == [("set", "Ali")]


def test_ui_act_refuses_a_secret_field_before_asking(desk):
    out = act_skill.run("type hunter2 into the Password field in Notepad", {"id": "t-ui3"})
    assert "secret field" in out["reply"] and "yes/no" not in out["reply"]


def test_ui_act_needs_a_window_and_a_control(desk):
    assert "Say which control and window" in act_skill.run("click the thing", {"id": "t-ui4"})["reply"]


def test_ui_read_lists_named_controls_and_never_acts(desk):
    out = read_skill.run("what buttons are in the Notepad window", {"id": "t-ui5"})
    assert "Controls in 'Untitled - Notepad'" in out["reply"] and "Button: Save" in out["reply"]
    assert desk["rec"].calls == [] and desk["field"].calls == []


def test_ui_read_says_which_window_when_none_is_named(desk):
    assert "Open windows: Untitled - Notepad" in read_skill.run("list the controls", {"id": "t-ui6"})["reply"]


def test_the_screenshot_free_skills_are_routable_and_the_screen_skills_are_off():
    from ishti.skills import load_skills
    skills = load_skills()
    assert skills["ui_read"].manifest["tier"] == "T0" and skills["ui_act"].manifest["tier"] == "T2"
    assert "screen_control" not in skills and "screen_understand" not in skills and "desktop_control" not in skills


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-q"]))
