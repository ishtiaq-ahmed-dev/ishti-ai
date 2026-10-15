"""Read the named controls of an open window (accessibility tree). Read-only: never clicks or types."""
import re

from ishti import ui_access

_WINDOW = re.compile(r"\b(?:in|of|on|for|from|inside)\s+(?:the\s+|that\s+|my\s+)?(?P<win>[^?.!,]+?)(?:\s+(?:window|app))?\s*[?.!]*$", re.I)
_CUE = re.compile(r"\b(?:buttons?|fields?|controls?|menu\s+items?|what\s+(?:can\s+i\s+click|is\s+on\s+the\s+screen)|list\s+the)\b", re.I)


def match(text):
    return bool(_CUE.search(text or ""))


def _window_part(text):
    m = _WINDOW.search(text or "")
    return m.group("win").strip() if m else ""


def run(text, session):
    part = _window_part(text)
    if not part:
        titles = ui_access.windows()[:12]
        return {"reply": "Which window? Open windows: " + "; ".join(titles) if titles else "No windows are open.", "sources": []}
    try:
        window = ui_access.find_window(part)
    except LookupError as exc:
        return {"reply": f"I could not read that window: {exc}.", "sources": [], "verified": False}
    items = ui_access.controls(window)
    if not items:
        return {"reply": f"'{window.Name}' exposes no named controls to read.", "sources": []}
    lines = [f"- {ui_access._type(c)}: {c.Name}" for c in items]
    return {"reply": f"Controls in '{window.Name}' ({len(items)} shown):\n" + "\n".join(lines), "sources": []}

if __name__ == "__main__":
    assert match("what buttons are in the Notepad window") and not match("what is the capital of Kazakhstan")
    assert _window_part("list the controls in Notepad") == "Notepad"
    print("[self-check] ui_read OK - cue words and window name parsing")
