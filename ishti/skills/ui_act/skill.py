"""Click a named control or type into a named field of an open window, through Windows accessibility.

Two steps, both exact: the request must name one window and one control; the owner then says yes to that exact
recap, and only then is the control acted on (re-found at that moment, so a window that changed is not used)."""
import re

from ishti import ui_access
from ishti.skills import ask_first

_CLICK = re.compile(r"^\s*(?:please\s+)?(?:click|press|tap|select)\s+(?:on\s+|the\s+)?(?P<ctrl>.+?)(?:\s+button|\s+link|\s+tab)?\s+(?:in|on|inside)\s+(?:the\s+|that\s+|my\s+)?(?P<win>.+?)(?:\s+(?:window|app))?\s*[.!]?\s*$", re.I)
_TYPE = re.compile(r"^\s*(?:please\s+)?type\s+(?P<val>.+?)\s+(?:into|in)\s+(?:the\s+|that\s+)?(?P<ctrl>.+?)(?:\s+field|\s+box)?\s+(?:in|on|inside)\s+(?:the\s+|that\s+|my\s+)?(?P<win>.+?)(?:\s+(?:window|app))?\s*[.!]?\s*$", re.I)


def match(text):
    return bool(_CLICK.match(text or "") or _TYPE.match(text or ""))


def _parse(text):
    if m := _TYPE.match(text or ""):
        return {"verb": "type", "value": m.group("val").strip(), "ctrl": m.group("ctrl").strip(), "win": m.group("win").strip()}
    if m := _CLICK.match(text or ""):
        return {"verb": "click", "value": None, "ctrl": m.group("ctrl").strip(), "win": m.group("win").strip()}
    return None


def act_done(message):
    """'clicked X' -> 'Clicked X' (only the first letter changes; the control name keeps its case)."""
    return message[:1].upper() + message[1:]


def _do(job):
    try:
        window = ui_access.find_window(job["win"])
        control = ui_access.find_control(window, job["ctrl"])
        return {"reply": act_done(ui_access.act(control, job["value"])) + f" in '{window.Name}'.", "sources": [], "verified": False}
    except (LookupError, PermissionError, NotImplementedError) as exc:
        return {"reply": f"I did not act: {exc}.", "sources": [], "verified": False}


def run(text, session):
    job = _parse(text)
    if not job:
        return {"reply": "Say which control and window, for example: click Save in Notepad.", "sources": []}
    try:
        window = ui_access.find_window(job["win"])
        control = ui_access.find_control(window, job["ctrl"])
    except LookupError as exc:
        return {"reply": f"I did not act: {exc}.", "sources": [], "verified": False}
    if ui_access.SENSITIVE.search(control.Name or ""):
        return {"reply": f"I will not touch '{control.Name}': it looks like a secret field.", "sources": [], "verified": False}
    if job["verb"] == "type":
        question = f"Type '{job['value'][:80]}' into '{control.Name}' in '{window.Name}'"
    else:
        question = f"Click '{control.Name}' in '{window.Name}'"
    return ask_first(session, "ui_act", question, lambda j=dict(job): _do(j))

if __name__ == "__main__":
    job = _parse("click the Save button in Notepad")
    assert job == {"verb": "click", "value": None, "ctrl": "Save", "win": "Notepad"}, job
    typed = _parse("type Ali into the Name field in Notepad")
    assert typed["verb"] == "type" and typed["value"] == "Ali" and typed["ctrl"] == "Name", typed
    assert _parse("what time is it") is None
    assert act_done("clicked 'Save'") == "Clicked 'Save'"
    print("[self-check] ui_act OK - click and type parsing, reply capitalisation")
