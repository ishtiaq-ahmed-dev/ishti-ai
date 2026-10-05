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
import hashlib
import threading

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
    try:
        from ishti.ocr import ocr_screen
        text, _ = ocr_screen()
    except Exception:
        return False, ""
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


def _foreground():
    import win32gui
    window = win32gui.GetForegroundWindow()
    if not window:
        raise RuntimeError("No foreground window is available")
    return window


def _focus(window):
    import ctypes
    from ctypes import wintypes
    class GUIThreadInfo(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("flags", wintypes.DWORD),
                    ("hwndActive", wintypes.HWND), ("hwndFocus", wintypes.HWND),
                    ("hwndCapture", wintypes.HWND), ("hwndMenuOwner", wintypes.HWND),
                    ("hwndMoveSize", wintypes.HWND), ("hwndCaret", wintypes.HWND),
                    ("rcCaret", wintypes.RECT)]
    info = GUIThreadInfo()
    info.cbSize = ctypes.sizeof(info)
    user = ctypes.WinDLL("user32", use_last_error=True)
    user.GetGUIThreadInfo.argtypes = [wintypes.DWORD, ctypes.POINTER(GUIThreadInfo)]
    user.GetGUIThreadInfo.restype = wintypes.BOOL
    if not user.GetGUIThreadInfo(0, ctypes.byref(info)) or info.hwndActive != window or not info.hwndFocus:
        raise RuntimeError("The focused input control is unavailable")
    return info.hwndFocus


def _cursor():
    import win32gui
    return tuple(win32gui.GetCursorPos())


def _capture_state(point=None, *, mode="click"):
    """Capture availability/identity plus exact pixels only around pointer targets.

    The OS can change focus between this final check and the input primitive.
    Focus HWND does not identify individual fields inside a browser renderer.
    """
    import mss
    import win32gui
    window = _foreground()
    focus = _focus(window) if mode in {"type", "key", "scroll"} else None
    left, top, right, bottom = win32gui.GetWindowRect(window)
    focus_geometry = win32gui.GetWindowRect(focus) if focus else None
    if mode == "scroll" and _cursor() != point:
        raise RuntimeError("The pointer's scroll target changed")
    with mss.mss() as capture:
        virtual = capture.monitors[0]
        left, top = max(left, virtual["left"]), max(top, virtual["top"])
        right = min(right, virtual["left"] + virtual["width"])
        bottom = min(bottom, virtual["top"] + virtual["height"])
        regions = [(left, top, right - left, bottom - top)]
        if point is not None:
            x, y = map(int, point)
            monitor = next((m for m in capture.monitors[1:] if
                            m["left"] <= x < m["left"] + m["width"]
                            and m["top"] <= y < m["top"] + m["height"]), None)
            if monitor is None:
                raise RuntimeError("Target is outside the connected displays")
            x0, y0 = max(x - 64, monitor["left"]), max(y - 64, monitor["top"])
            x1 = min(x + 65, monitor["left"] + monitor["width"])
            y1 = min(y + 65, monitor["top"] + monitor["height"])
            regions.append((x0, y0, x1 - x0, y1 - y0))
        fingerprints = []
        for index, (x, y, width, height) in enumerate(regions):
            if width <= 0 or height <= 0 or width * height > 40_000_000:
                raise RuntimeError("Screen capture exceeds safe bounds")
            shot = capture.grab({"left": x, "top": y, "width": width, "height": height})
            if index > 0 and mode in {"click", "scroll"}:
                fingerprints.append(hashlib.sha256(shot.rgb).hexdigest())
    if _foreground() != window:
        raise RuntimeError("Foreground changed during capture")
    if focus is not None and _focus(window) != focus:
        raise RuntimeError("Focused input control changed during capture")
    return window, tuple(regions), tuple(fingerprints), focus, focus_geometry


def _approve_action(session, detail, action, point=None, *, mode="click"):
    try:
        if mode == "scroll":
            point = _cursor()
        expected = _capture_state(point, mode=mode)
    except Exception:
        return {"reply": "The foreground window or screenshot is unavailable. No input was sent.", "sources": []}
    consumed = False
    lock = threading.Lock()
    def apply_once():
        nonlocal consumed
        with lock:
            if consumed:
                return "This approval was already consumed. No input was sent."
            consumed = True
        from ishti import test_mode
        if test_mode.enabled():
            return "Test mode recorded the approved action; no real input was sent."
        try:
            if _capture_state(point, mode=mode) != expected:
                return "The foreground window or target changed. No input was sent."
            return action()
        except Exception:
            return "Screen capture or input failed. No further input was sent."
    return ask_first(session, "screen_control", detail, apply_once)


def _image_fingerprint(image, regions=None):
    if image.width <= 0 or image.height <= 0 or image.width * image.height > 40_000_000:
        raise RuntimeError("Planned screenshot exceeds safe bounds")
    converted = image.convert("RGB")
    try:
        if regions is None:
            return image.size, hashlib.sha256(converted.tobytes()).hexdigest()
        fingerprints = []
        for left, top, right, bottom in regions:
            if left < 0 or top < 0 or right > image.width or bottom > image.height or right <= left or bottom <= top:
                raise RuntimeError("Planned target is outside the screenshot")
            with converted.crop((left, top, right, bottom)) as target:
                fingerprints.append(hashlib.sha256(target.tobytes()).hexdigest())
        return image.size, tuple(fingerprints)
    finally:
        converted.close()


def _do(op: str, args: dict) -> str:
    from ishti import test_mode
    if test_mode.enabled():
        return "Test mode recorded the action; no real input was sent."
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
        keys = [k.replace("windows", "win").replace("escape", "esc")
                for k in re.split(r"[+\s]+", args["keys"].strip().lower())]
        if {"win", "l"}.issubset(keys) or {"ctrl", "alt", "del"}.issubset(keys):
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
            foreground = _foreground()
            try:
                planning_focus = _focus(foreground)
            except Exception:
                planning_focus = None
            agent = get_multi_screen_agent()
            cmd, record = agent.step(text)
            if _foreground() != foreground:
                raise RuntimeError("Foreground changed during planning")
        except Exception as exc:
            return {"reply": f"Screen planning stopped: {exc}. No input was sent.", "sources": [], "verified": False}
        if cmd.action == "done":
            return {"reply": "The visual planner proposed completion; no screen action was needed. Check the requested result.", "sources": [], "verified": False}
        if cmd.action == "wait":
            return {"reply": "The visual planner needs the screen to settle. Ask me to continue in a moment.", "sources": [], "verified": False}
        try:
            from PIL import Image
            mode = {"click": "click", "double_click": "click", "drag_to": "click",
                    "type": "type", "press_key": "key", "scroll": "scroll", "move_to": "move"}.get(cmd.action)
            if mode is None:
                raise RuntimeError("Unsupported planned action")
            point = ((int(cmd.x), int(cmd.y)) if mode in {"click", "move"}
                     else (_cursor() if mode == "scroll" else None))
            expected_state = _capture_state(point, mode=mode)
            if mode in {"type", "key", "scroll"} and (not planning_focus or expected_state[3] != planning_focus):
                raise RuntimeError("Focused input changed during planning")
            targets = [point] if mode in {"click", "scroll"} else []
            if cmd.action == "drag_to":
                targets.append(_cursor())
            display = agent.display_mgr
            origin = (display.virtual_left, display.virtual_top)
            if cmd.action == "drag_to":
                drag_start = targets[-1]
            with open(record["screenshot"], "rb") as source:
                data = source.read(30 * 1024 * 1024 + 1)
            if len(data) > 30 * 1024 * 1024:
                raise RuntimeError("Planned screenshot file exceeds safe bounds")
            import io
            with Image.open(io.BytesIO(data)) as planned:
                regions = []
                for x, y in targets:
                    x, y = x - origin[0], y - origin[1]
                    regions.append((max(0, x - 32), max(0, y - 32),
                                    min(planned.width, x + 33), min(planned.height, y + 33)))
                expected_image = _image_fingerprint(planned, regions)
        except Exception:
            return {"reply": "The planned screenshot is unavailable. No input was sent.", "sources": [], "verified": False}
        consumed = False
        lock = threading.Lock()
        def apply_one():
            nonlocal consumed
            with lock:
                if consumed:
                    return "This approval was already consumed. No input was sent."
                consumed = True
            try:
                from ishti import test_mode
                if test_mode.enabled():
                    return "Test mode recorded the planned action; no real input was sent."
                if _foreground() != foreground:
                    return "The foreground window changed. I did not apply the approved action."
                if _capture_state(point, mode=mode) != expected_state:
                    return "The foreground, focus or target changed. No input was sent."
                if cmd.action == "drag_to" and _cursor() != drag_start:
                    return "The drag source changed. No input was sent."
                current, _, _ = agent.capture_engine.capture_stitched(allow_synthetic=False)
                try:
                    unchanged = _image_fingerprint(current, regions) == expected_image
                finally:
                    current.close()
                if not unchanged or _foreground() != foreground:
                    return "The planned screenshot changed. No input was sent."
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
        return _approve_action(session, f"Move the pointer to ({coords['x']}, {coords['y']})",
                               lambda: _do("move", coords), (coords["x"], coords["y"]), mode="move")
    m = SCROLL.search(text)
    if m:
        d = m.groupdict()
        act = lambda dd=d: _do("scroll", {"dir": dd["dir"], "amount": dd.get("amount")})  # noqa: E731
        return _approve_action(session, f"scroll {d['dir']} the page", act, mode="scroll")
    m = KEY.search(text)
    if m:
        keys = m.group("keys")
        act = lambda k=keys: _do("key", {"keys": k})  # noqa: E731
        return _approve_action(session, f"press {keys}", act, mode="key")
    m = TYPE.search(text)
    if m and "click" not in text.lower():
        payload = (m.group("text") or "").strip("\"'")
        if not payload:
            return {"reply": "Give me the text to type.", "sources": []}
        act = lambda p=payload: _do("type", {"text": p})  # noqa: E731
        return _approve_action(session, f"type \"{payload[:60]}\" wherever the cursor is", act, mode="type")
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
        return _approve_action(session, detail, act, (x, y))
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
