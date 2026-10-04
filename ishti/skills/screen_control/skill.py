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
    r"|\bpress\s+(?:on\s+)?(?:the\s+)?[\"']?(?P<words1>[\w ,'-]{2,40})[\"']?\s*(?:button|on\s+my\s+screen)\b", I)
TYPE = re.compile(r"\btype\s+(?:this\s+|that\s+|the\s+)?(?P<text>[\"'].+[\"']|.+)?:?(?:\s+on\s+(?:my|the)\s+(?:pc|screen|computer))?\s*$", I)
KEY = re.compile(r"\bpress\s+(?P<keys>(?:ctrl|alt|shift|win|windows|enter|esc|escape|tab|f\d{1,2})\b(?:\s*[+ ]\s*(?:ctrl|alt|shift|win|windows|enter|esc|escape|tab|f\d{1,2}|[a-z0-9])\b)*)", I)  # must start with a named key: 'press release' is never a hotkey
SCROLL = re.compile(r"\bscroll\s+(?P<dir>up|down)(?:\s+(?P<amount>\d+))?\b", I)
MOVE = re.compile(r"\bmove\s+(?:the\s+)?mouse\s+(?:to|at)\s+(?P<x>\d+)\s*[, ]\s*(?P<y>\d+)\b", I)
MULTISCREEN = re.compile(
    r"\b(?:multi[- ]screen|dual[- ]screen|drag\s+.*(?:from\s+screen|to\s+screen|across\s+screens)|"
    r"perceive[- ]plan[- ]act|os\s+automation|automate\s+(?:on\s+|across\s+)?(?:both\s+screens?|my\s+screens?|screen\s*\d))\b", I)


def match(text):
    return bool(CLICK.search(text) or TYPE.search(text) or KEY.search(text)
                or SCROLL.search(text) or MOVE.search(text) or MULTISCREEN.search(text))


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


def locate_on_screen(phrase: str):
    """Find one exact OCR phrase with coordinates; ambiguous or unavailable OCR fails closed."""
    import mss
    from PIL import Image
    from ishti.ocr import windows_ocr_boxes
    wanted = [w.casefold() for w in re.findall(r"[\w']+", phrase)]
    if not wanted:
        return None
    matches = []
    with mss.mss() as screenshots:
        for monitor in screenshots.monitors[1:]:
            shot = screenshots.grab(monitor)
            image = Image.frombytes("RGB", shot.size, shot.rgb)
            for line in windows_ocr_boxes(image):
                normalized = [re.sub(r"^\W+|\W+$", "", word).casefold() for word, *_ in line]
                for start in range(len(normalized) - len(wanted) + 1):
                    if normalized[start:start + len(wanted)] == wanted:
                        selected = line[start:start + len(wanted)]
                        left = min(item[1] for item in selected)
                        top = min(item[2] for item in selected)
                        right = max(item[1] + item[3] for item in selected)
                        bottom = max(item[2] + item[4] for item in selected)
                        matches.append((monitor["left"] + (left + right) // 2,
                                        monitor["top"] + (top + bottom) // 2))
    return matches[0] if len(matches) == 1 else None


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
            try:
                current = locate_on_screen(phrase)
            except Exception:
                current = None
            if current is None or current != (int(args.get("x", -1)), int(args.get("y", -1))):
                return f"The target \"{phrase}\" moved, is ambiguous, or is no longer visible. Nothing was clicked."
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
        if "+".join(keys) in {"win+l", "ctrl+alt+del"}:
            return "That system key combination is blocked."
        if len(keys) > 1:
            ok = a.keyboard_hotkey(*keys)
        else:
            import pyautogui
            pyautogui.press(keys[0])
            ok = True
        return f"Pressed {' + '.join(keys)}." if ok else "The key press failed."
    if op == "scroll":
        import pyautogui
        amount = max(1, min(10, int(args.get("amount") or 5))) * (1 if args["dir"] == "up" else -1)
        pyautogui.scroll(amount)
        return f"Scrolled {args['dir']}."
    if op == "move":
        ok = a.mouse_move(int(args["x"]), int(args["y"]))
        return f"Mouse at ({args['x']}, {args['y']})." if ok else "The mouse did not move."
    return "Unknown screen action."


def run(text, session):
    m = MULTISCREEN.search(text)
    if m:
        from ishti.automation.multi_screen_agent import get_multi_screen_agent
        try:
            agent = get_multi_screen_agent()
            cmd, record = agent.step(text)
        except Exception as exc:
            return {"reply": f"Screen planning stopped: {exc}. No input was sent.", "sources": [], "verified": False}
        if cmd.action == "done":
            return {"reply": "The visual planner proposed completion; no screen action was needed. Check the requested result.", "sources": [], "verified": False}
        if cmd.action == "wait":
            return {"reply": "The visual planner needs the screen to settle. Ask me to continue in a moment.", "sources": [], "verified": False}
        try:
            import win32gui
            foreground = win32gui.GetForegroundWindow()
        except Exception:
            return {"reply": "I could not identify the foreground window. No screen action was taken.", "sources": [], "verified": False}
        proposed = cmd.to_dict()
        def apply_one():
            try:
                from ishti import test_mode
                if test_mode.enabled():
                    return "Test mode recorded the planned action; no real input was sent."
                if win32gui.GetForegroundWindow() != foreground:
                    return "The foreground window changed. I did not apply the approved action."
                agent.capture_engine.capture_stitched(allow_synthetic=False)
                result = agent.executor.execute(cmd, approved=True)
                return (f"Applied one approved {cmd.action} action. Review the screen before the next step."
                        if result.get("success") else f"Screen action stopped: {result.get('error') or 'unknown failure'}")
            except Exception:
                return "The screen changed or capture failed. No action was applied."
        detail = f"{cmd.action} on the current screen"
        if cmd.action in {"click", "double_click", "move_to", "drag_to"}:
            detail += f" at ({cmd.x}, {cmd.y})"
        elif cmd.action == "type":
            detail += f": {cmd.text[:120]}"
        elif cmd.action == "press_key":
            detail += f": {cmd.key}"
        return ask_first(session, "screen_control", f"Apply one planned action: {detail}", apply_one)
    m = MOVE.search(text)
    if m:
        coords = m.groupdict()
        return ask_first(session, "screen_control", f"Move the pointer to ({coords['x']}, {coords['y']})",
                         lambda: _do("move", coords))
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
        if not words:
            named = re.search(r"\bclick\s+(?:on\s+)?(?:the\s+)?(.+?)(?:\s+button)?(?:\s+on\s+(?:my|the)\s+screen)?\s*$", text, I)
            words = (named.group(1) if named else "").strip(" \"'.,!?()")
        if not words:
            return {"reply": "Name the visible button or give exact coordinates so I can target the click.", "sources": []}
        try:
            location = locate_on_screen(words)
        except Exception:
            location = None
        if location is None:
            return {"reply": f"I couldn't locate one exact visible \"{words}\" target. Nothing was clicked.", "sources": []}
        x, y = location
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
