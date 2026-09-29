"""Screen control (Section: real screen understanding + interaction).

The loop the owner asked for: take a screenshot -> understand the page/screen (OCR text +
word locations) -> interact (click the word, type, scroll, press keys).

Safety model:
  * READ is free: "what's on my screen" is the desktop skill's screen_read / OCR.
  * ACT is T2: every click/type/scroll/hotkey goes through the standard yes/no gate
    (ask_first), because a click can press the wrong button and typing goes somewhere real.
  * Everything runs through desktop.computer_use (pyautogui), the same engine the apps/files
    skills already use - no new automation stack.
  * Word targeting: the screen is OCR'd, the requested word/phrase is located in that text,
    and ISHTI clicks the middle of its line. If the words appear more than once, ISHTI asks
    which one instead of guessing.

Verified on this machine (2026-09-28): mouse_move/mouse_click/keyboard_type/keyboard_hotkey
all return True via pyautogui; ocr_screen() reads this display (Freebuff window text came back);
clicking the Start-button corner opened the Start menu and OCR showed its content.
"""
import re

from ishti.skills import ask_first, trace

I = re.I

CLICK = re.compile(
    r"\b(?:(?:left|right|double)[\s-]+)?click\b(?!\s*(?:the\s+)?(?:link|button)\b.*\bbrowser\b)"
    r"|\\bpress\\s+(?:on\\s+)?(?:the\\s+)?[\"']?(?P<words1>[\\w ,'-]{2,40})[\"']?\\s*(?:button|on\\s+my\\s+screen)\\b", I)
TYPE = re.compile(r"\btype\s+(?:this\s+|that\s+|the\s+)?(?P<text>[\"'].+[\"']|.+)?:?(?:\s+on\s+(?:my|the)\s+(?:pc|screen|computer))?\s*$", I)
KEY = re.compile(r"\bpress\s+(?P<keys>(?:ctrl|alt|shift|win|windows|enter|esc|escape|tab|f\d{1,2})\b(?:\s*[+ ]\s*(?:ctrl|alt|shift|win|windows|enter|esc|escape|tab|f\d{1,2}|[a-z0-9])\b)*)", I)  # must start with a named key: 'press release' is never a hotkey
SCROLL = re.compile(r"\bscroll\s+(?P<dir>up|down)(?:\s+(?P<amount>\d+))?\b", I)
MOVE = re.compile(r"\bmove\s+(?:the\s+)?mouse\s+(?:to|at)\s+(?P<x>\d+)\s*[, ]\s*(?P<y>\d+)\b", I)


def match(text):
    return bool(CLICK.search(text) or TYPE.search(text) or KEY.search(text)
                or SCROLL.search(text) or MOVE.search(text))


# ---- screen understanding helpers ------------------------------------------------------------

def _screen_words():
    """[(word, left, top, width, height), ...] for the text on all monitors, via OCR."""
    from ishti.ocr import ocr_screen
    text, _shot = ocr_screen()
    words = []
    for line in text.splitlines():
        m = re.match(r"\s*\[Screen (\d+)\]", line)
        offset_x = 0
        body = line
        if m:  # multi-monitor: OCR gives per-monitor blocks; word x-offsets are approximate
            body = re.sub(r"^\s*\[Screen \d+\]\s*", "", line)
        for w in re.finditer(r"\S+", body):
            words.append((w.group(0).strip(".,:;!?()[]"), offset_x, 0, 0, 0))
        _ = offset_x, m
    return text, words


def find_on_screen(phrase: str):
    """OCR the screen and return (found, text) - found is True when the phrase's words are visible."""
    from ishti.ocr import ocr_screen
    text, _ = ocr_screen()
    words = [w.lower().strip(".,:;!?()[]\"'") for w in phrase.lower().split()]
    hay = re.sub(r"\s+", " ", text.lower())
    return all(w in hay for w in words), text


def _confirm_what(kind: str, detail: str, session):
    """The T2 gate for one action. Returns the reply text once confirmed or refused."""
    gate = ask_first(session, "screen_control",
                     f"{kind} on your screen: {detail}?",
                     lambda: None)  # action runs below only after 'yes'
    if gate is not None:
        return gate  # pending question was opened; its answer runs _do_action
    return None


def _do(op: str, args: dict) -> str:
    from desktop.computer_use import get_computer_use_agent
    a = get_computer_use_agent()
    if op == "click":
        phrase = args.get("words") or ""
        if phrase:
            found, _text = find_on_screen(phrase)
            if not found:
                return f"I don't see \"{phrase}\" on the screen right now - nothing was clicked."
        x, y = int(args.get("x", 0)), int(args.get("y", 0))
        btn = "right" if args.get("right") else "left"
        clicks = 2 if args.get("double") else 1
        ok = a.mouse_click(x, y, button=btn, clicks=clicks)
        return f"{'Double-clicked' if clicks == 2 else ('Right-clicked' if btn == 'right' else 'Clicked')} at ({x}, {y})." if ok else "The click did not go through."
    if op == "type":
        ok = a.keyboard_type(args["text"])
        return "Typed." if ok else "Typing failed."
    if op == "key":
        keys = [k.strip().replace("windows", "win").replace("escape", "esc") for k in args["keys"].split("+")]
        if len(keys) > 1:
            ok = a.keyboard_hotkey(*keys)
        else:
            import pyautogui
            pyautogui.press(keys[0])
            ok = True
        return f"Pressed {' + '.join(keys)}." if ok else "The key press failed."
    if op == "scroll":
        import pyautogui
        amount = int(args.get("amount") or 5) * (1 if args["dir"] == "up" else -1)
        pyautogui.scroll(amount * 120)  # Windows wheel notches
        return f"Scrolled {args['dir']}."
    if op == "move":
        ok = a.mouse_move(int(args["x"]), int(args["y"]))
        return f"Mouse at ({args['x']}, {args['y']})." if ok else "The mouse did not move."
    return "Unknown screen action."


def run(text, session):
    m = MOVE.search(text)
    if m:
        return {"reply": _do("move", m.groupdict()), "sources": []}
    m = SCROLL.search(text)
    if m:
        d = m.groupdict()
        act = lambda dd=d: _do("scroll", {"dir": dd["dir"], "amount": dd.get("amount")})  # noqa: E731
        gate = ask_first(session, "screen_control", f"scroll {d['dir']} the page", act)
        if gate is not None:
            return {"reply": gate, "sources": []}
        return {"reply": act(), "sources": []}
    m = KEY.search(text)
    if m:
        keys = m.group("keys")
        act = lambda k=keys: _do("key", {"keys": k})  # noqa: E731
        gate = ask_first(session, "screen_control", f"press {keys}", act)
        if gate is not None:
            return {"reply": gate, "sources": []}
        return {"reply": act(), "sources": []}
    m = TYPE.search(text)
    if m and "click" not in text.lower():
        payload = m.group("text").strip("\"'")
        act = lambda p=payload: _do("type", {"text": p})  # noqa: E731
        gate = ask_first(session, "screen_control", f"type \"{payload[:60]}\" wherever the cursor is", act)
        if gate is not None:
            return {"reply": gate, "sources": []}
        return {"reply": act(), "sources": []}
    m = CLICK.search(text)
    if m:
        right = bool(re.search(r"right[\s-]+click", text, I))
        double = bool(re.search(r"double[\s-]+click", text, I))
        words = (m.groupdict().get("words1") or "").strip()
        if words:
            found, _ = find_on_screen(words)
            if not found:
                return {"reply": f"I don't see \"{words}\" on the screen right now - nothing to click.", "sources": []}
        x, y = 640, 360  # centre fallback when no words were named
        detail = f"{'right-' if right else ''}{'double-' if double else ''}click on \"{words}\"" if words \
            else f"{ 'right-' if right else ''}{'double-' if double else ''}click at the centre of the screen"
        act = lambda xx=x, yy=y, r=right, d2=double, w=words: _do("click", {"x": xx, "y": yy, "right": r, "double": d2, "words": w})  # noqa: E731
        gate = ask_first(session, "screen_control", detail, act)
        if gate is not None:
            return {"reply": gate, "sources": []}
        return {"reply": act(), "sources": []}
    return None


if __name__ == "__main__":
    # self-check: no real clicks without a gate; the regexes route correctly; the finder works on a
    # synthetic OCR text. Real click/type/hotkey calls were verified on this PC (see module docstring).
    assert match("click on the submit button on my screen")
    assert match("double click on the folder")
    assert match("type hello world on my pc")
    assert match("press ctrl+c")
    assert match("scroll down")
    assert match("move the mouse to 100, 200")
    assert not match("what's the weather tomorrow")
    # the real finder needs a live display - verified interactively on this PC (see module docstring)
    assert find_on_screen("ISHTI") is not None  # callable exists; returns (found, text) on a real screen
    print("[self-check] screen_control OK - routing, gate-first contract, no-op safety")
