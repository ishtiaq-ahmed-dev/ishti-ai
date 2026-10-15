"""Windows UI Automation for named controls: read a window's controls and act on one of them by name.

This replaces screenshot + OCR + coordinate clicking. Nothing is captured as an image and nothing is clicked at a
position: a control is found by its accessibility name, and acted on through its own pattern (Invoke, Toggle, Select,
SetValue). If an app does not expose a pattern, the action is refused and the owner is told, instead of guessing a
click position.
"""
from __future__ import annotations

import re

MAX_CONTROLS = 60
SENSITIVE = re.compile(r"password|passcode|passwd|secret|credential|token|\bpin\b|cvv|card\s*number|iban", re.I)
INTERACTIVE = ("Button", "Edit", "MenuItem", "Hyperlink", "CheckBox", "RadioButton", "ComboBox", "ListItem", "TabItem")
_READABLE = INTERACTIVE + ("Text",)

_backend = None  # tests inject an object with GetRootControl(); None means the real Windows UI Automation


def _root():
    if _backend is not None:
        return _backend.GetRootControl()
    import uiautomation as auto
    return auto.GetRootControl()


def _name(c) -> str:
    return (getattr(c, "Name", "") or "").strip()


def _type(c) -> str:
    """The control's type without UI Automation's 'Control' suffix (ButtonControl -> Button)."""
    return str(getattr(c, "ControlTypeName", "") or "").removesuffix("Control")


def _kids(c) -> list:
    try:
        return list(c.GetChildren() or [])
    except Exception:
        return []


def _ishti_window(title: str) -> bool:
    return "ishti" in title.lower()


def windows() -> list[str]:
    """Titles of the open top-level windows (ISHTI's own windows left out)."""
    return [t for t in (_name(w) for w in _kids(_root())) if t and not _ishti_window(t)]


def find_window(part: str):
    """The one open window whose title contains `part`. Raises LookupError with the reason, never guesses."""
    part = (part or "").strip().lower()
    if not part:
        raise LookupError("name the window, for example 'in Notepad'")
    hits = [w for w in _kids(_root()) if part in _name(w).lower() and not _ishti_window(_name(w))]
    if not hits:
        raise LookupError(f"no open window matches '{part}'")
    exact = [w for w in hits if _name(w).lower() == part]
    if len(exact) == 1:
        return exact[0]
    if len(hits) > 1:
        raise LookupError(f"'{part}' matches {len(hits)} windows ({', '.join(_name(w) for w in hits[:5])}); name one exactly")
    return hits[0]


def _walk(root, depth=0, out=None):
    out = [] if out is None else out
    if depth > 8 or len(out) >= 2000:
        return out
    for child in _kids(root):
        out.append(child)
        _walk(child, depth + 1, out)
    return out


def controls(window, kinds=_READABLE) -> list:
    """Named controls of a window, interactive ones first, capped at MAX_CONTROLS."""
    named = [c for c in _walk(window) if _name(c) and _type(c) in kinds]
    named.sort(key=lambda c: 0 if _type(c) in INTERACTIVE else 1)
    return named[:MAX_CONTROLS]


def find_control(window, name: str):
    """The one control whose accessibility name is `name` (exact first, then contains). Raises LookupError otherwise."""
    want = (name or "").strip().lower()
    if not want:
        raise LookupError("name the control, for example 'Save'")
    named = [c for c in _walk(window) if _name(c) and _type(c) in INTERACTIVE]
    exact = [c for c in named if _name(c).lower() == want]
    hits = exact or [c for c in named if want in _name(c).lower()]
    if not hits:
        raise LookupError(f"no control named '{name}' in that window")
    if len(hits) > 1:
        raise LookupError(f"'{name}' matches {len(hits)} controls ({', '.join(_name(c) for c in hits[:5])}); name one exactly")
    return hits[0]


def _pattern(control, getter: str):
    fn = getattr(control, getter, None)
    try:
        return fn() if callable(fn) else None
    except Exception:
        return None


def act(control, value: str | None = None) -> str:
    """Invoke the control (or set its text) through its own pattern. Returns what happened, or why it could not."""
    name, kind = _name(control), _type(control)
    if value is None:
        if SENSITIVE.search(name):
            raise PermissionError(f"'{name}' looks like a secret field, so I will not touch it")
        for getter, verb in (("GetInvokePattern", "clicked"), ("GetTogglePattern", "toggled"), ("GetSelectionItemPattern", "selected")):
            pat = _pattern(control, getter)
            if pat is None:
                continue
            (pat.Invoke if getter == "GetInvokePattern" else pat.Toggle if getter == "GetTogglePattern" else pat.Select)()
            return f"{verb} '{name}'"
        raise NotImplementedError(f"the {kind} '{name}' does not expose a click pattern, so I did not act on it")
    if SENSITIVE.search(name):
        raise PermissionError(f"'{name}' looks like a secret field, so I will not type into it")
    pat = _pattern(control, "GetValuePattern")
    if pat is None:
        raise NotImplementedError(f"the {kind} '{name}' does not accept typed text through accessibility, so I did not type")
    pat.SetValue(value)
    return f"set the text of '{name}'"


if __name__ == "__main__":
    class _Node:
        def __init__(self, name, kind, kids=(), pattern=None):
            self.Name, self.ControlTypeName, self._kids, self._pattern = name, kind, list(kids), pattern

        def GetChildren(self):
            return self._kids

        def GetInvokePattern(self):
            return self._pattern

    class _Invoke:
        def __init__(self):
            self.called = 0

        def Invoke(self):
            self.called += 1

    class _Root:
        def __init__(self, kids):
            self.kids = kids

        def GetRootControl(self):
            return _Node("", "Desktop", self.kids)

    inv = _Invoke()
    save = _Node("Save", "Button", pattern=inv)
    notepad = _Node("Untitled - Notepad", "Window", [save, _Node("Password", "Edit")])
    globals()["_backend"] = _Root([notepad])  # this module's own backend, as functions above read it
    assert windows() == ["Untitled - Notepad"]
    assert find_window("notepad") is notepad
    assert find_control(notepad, "save") is save
    assert act(save) == "clicked 'Save'" and inv.called == 1
    refused = False
    try:
        act(_Node("Password", "Edit"), value="x")
    except PermissionError:
        refused = True
    assert refused, "secret field was touched"
    print("[self-check] ui_access OK - windows, named controls, invoke, secret fields refused")
