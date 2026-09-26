"""More desktop control: virtual desktops, clipboard history, displays, printing, macros.
Native first: Win+Ctrl shortcuts, DisplaySwitch.exe, the Windows print verb and PowerShell's print cmdlets;
pynput only for macros. Printing and macro playback act on the real world, so they wait for a yes."""
import json
import os
import re
import sqlite3
import subprocess
import threading
import time

from ishti import ROOT
from ishti.skills import ask_first

I = re.I
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
MACROS = os.path.join(ROOT, "data", "macros")
CLIP_DB = os.path.join(ROOT, "data", "clipboard.db")
CLIP_KEEP = 100

OPS = [
    ("screen_text", re.compile(r"\b(?:(?:read|copy|get|grab|extract)\s+(?:all\s+)?(?:the\s+)?text\s+(?:on|from|off)\s+(?:my\s+|the\s+)?screen|ocr\s+(?:my\s+|the\s+)?screen)\b", I)),
    ("vdesk", re.compile(r"\b(?:(?P<act>new|create|add|close|remove)\s+(?:a\s+|this\s+|the\s+)?(?:virtual\s+)?desktop|"
                         r"(?:switch|go|move)\s+to\s+(?:the\s+)?(?:(?P<dir>next|previous|left|right)\s+desktop|desktop\s+(?P<n>\d))|"
                         r"(?P<view>task\s+view|show\s+(?:all\s+)?desktops))\b", I)),
    ("clip_history", re.compile(r"\bclipboard\s+history\b|\b(?:what\s+did\s+i|things\s+i)\s+cop(?:y|ied)\b|"
                                r"\b(?:copy|restore|paste)\s+(?:clipboard\s+)?(?:item|entry|number)\s+(?P<n>\d+)\b", I)),
    ("displays", re.compile(r"\b(?:list|show|which|how\s+many)\s+(?:my\s+)?(?:monitors|displays|screens)\b|"
                            r"\b(?P<mode>extend|duplicate|mirror|clone)\s+(?:my\s+|the\s+)?(?:displays?|screens?|monitors?)\b|"
                            r"\b(?P<only>(?:pc|laptop|first|main|second|external)\s+screen\s+only|only\s+(?:the\s+)?(?:second|external|main|laptop)\s+(?:screen|display|monitor))\b", I)),
    ("print_queue", re.compile(r"\bprint(?:er)?\s+queue\b|\bwhat(?:'s| is)\s+printing\b|\b(?P<cancel>cancel|clear)\s+(?:all\s+)?(?:the\s+)?print(?:ing)?\s+jobs?\b|"
                               r"\b(?:list|show|which)\s+(?:my\s+)?printers\b", I)),
    ("print_file", re.compile(r"^\s*(?:please\s+)?print\s+(?:out\s+)?(?:the\s+file\s+)?[\"']?(?P<path>[a-z]:[\\/].+?\.\w{2,5})[\"']?"
                              r"(?:\s+(?P<copies>\d+)\s+(?:copies|times))?\s*$", I)),
    ("macro", re.compile(r"\b(?P<act>record|start\s+recording|stop\s+recording|play|run|replay|list|delete)\s+(?:a\s+|the\s+|my\s+)?"
                         r"macros?\b(?:\s+(?:called|named)?\s*[\"']?(?P<name>[\w -]+?)[\"']?)?\s*$", I)),
]


def match(text):
    return any(rx.search(text) for _, rx in OPS)


# ---------------------------------------------------------------- screen text (OCR)
def op_screen_text(m, text, session):
    from ishti.ocr import ocr_screen
    found, path = ocr_screen()
    if not found.strip():
        return f"I found no text on the screen. (Screenshot: {path})"
    if re.search(r"\bcopy\b", text, I):
        import pyperclip
        pyperclip.copy(found)
        return f"Copied {len(found.split())} words from the screen to the clipboard."
    return f"Text on your screen:\n```\n{found[:3000]}\n```"


# ---------------------------------------------------------------- virtual desktops
def _keys(*combo, times=1):
    import pyautogui
    for _ in range(times):
        pyautogui.hotkey(*combo)
        time.sleep(0.25)


def op_vdesk(m, text, session):
    act, direction, n = (m["act"] or "").lower(), (m["dir"] or "").lower(), m["n"]
    if m["view"]:
        _keys("win", "tab")
        return "Task View is open - all your desktops are shown."
    if act in ("new", "create", "add"):
        _keys("win", "ctrl", "d")
        return "Made a new virtual desktop and switched to it."
    if act in ("close", "remove"):
        _keys("win", "ctrl", "f4")
        return "Closed this virtual desktop; its windows moved to the next one."
    if n:  # ponytail: no count API without pyvda - go to the first desktop, then step right (n-1) times
        _keys("win", "ctrl", "left", times=10)
        _keys("win", "ctrl", "right", times=int(n) - 1)
        return f"Switched to desktop {n} (if it exists - otherwise you're on the last one)."
    _keys("win", "ctrl", "right" if direction in ("next", "right") else "left")
    return f"Switched to the {direction} desktop."


# ---------------------------------------------------------------- clipboard history
SECRET = re.compile(r"^(?=.*\d)(?=.*[A-Za-z])\S{16,}$|^(?:sk|ghp|gho|xox[abp]|AKIA|eyJ)[\w-]{10,}|password|passwd", I)


def _clip_db():
    os.makedirs(os.path.dirname(CLIP_DB), exist_ok=True)
    db = sqlite3.connect(CLIP_DB)
    db.execute("CREATE TABLE IF NOT EXISTS clips(id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, text TEXT)")
    return db


def remember_clip(text, db=None):
    """Store a new clipboard text (not secrets, not repeats); keep the newest CLIP_KEEP."""
    text = (text or "").strip()
    if not text or len(text) > 20000 or SECRET.search(text):
        return False
    db = db or _clip_db()
    last = db.execute("SELECT text FROM clips ORDER BY id DESC LIMIT 1").fetchone()
    if last and last[0] == text:
        return False
    with db:
        db.execute("INSERT INTO clips(ts, text) VALUES(?, ?)", (time.time(), text))
        db.execute("DELETE FROM clips WHERE id NOT IN (SELECT id FROM clips ORDER BY id DESC LIMIT ?)", (CLIP_KEEP,))
    return True


def recent_clips(n=10, db=None):
    return (db or _clip_db()).execute("SELECT text FROM clips ORDER BY id DESC LIMIT ?", (n,)).fetchall()


_watching = False


def start_clipboard_history():
    """Watch the clipboard (text only, every 1.5 s, local SQLite ring of the last 100; passwords/tokens skipped)."""
    global _watching
    if _watching:
        return
    _watching = True

    def loop():
        import pyperclip
        db = _clip_db()
        while True:
            try:
                remember_clip(pyperclip.paste(), db)
            except Exception:
                pass
            time.sleep(1.5)
    threading.Thread(target=loop, daemon=True, name="clipboard-history").start()


def op_clip_history(m, text, session):
    items = [t for (t,) in recent_clips(20)]
    if m["n"]:
        i = int(m["n"])
        if not 0 < i <= len(items):
            return f"There's no item {i} - I have {len(items)} in the clipboard history."
        import pyperclip
        pyperclip.copy(items[i - 1])
        return f"Item {i} is back on the clipboard: {items[i - 1][:120]}"
    if not items:
        return "The clipboard history is empty (it fills while ISHTI runs; passwords and tokens are never kept)."
    lines = [f"{i}. {' '.join(t.split())[:100]}" for i, t in enumerate(items[:10], 1)]
    return "Recent copies (newest first) - say \"copy item 2\" to put one back:\n" + "\n".join(lines)


# ---------------------------------------------------------------- displays
def op_displays(m, text, session):
    mode = (m["mode"] or "").lower()
    only = (m["only"] or "").lower()
    if mode or only:
        flag = "/extend" if mode == "extend" else "/clone" if mode else \
               "/external" if re.search(r"second|external", only) else "/internal"
        subprocess.run(["DisplaySwitch.exe", flag], creationflags=NO_WINDOW, timeout=15)
        return {"/extend": "Extended the desktop across your screens.", "/clone": "Mirroring the same picture on both screens.",
                "/external": "Showing only the second (external) screen.", "/internal": "Showing only the main (PC) screen."}[flag]
    import win32api
    rows = []
    for i, (h, _, rect) in enumerate(win32api.EnumDisplayMonitors(), 1):
        info = win32api.GetMonitorInfo(h)
        l, t, r, b = rect
        rows.append(f"{i}. {r - l}x{b - t} at ({l}, {t})" + (" - main" if info.get("Flags") & 1 else ""))
    return f"{len(rows)} display(s):\n" + "\n".join(rows) + "\nSay \"extend my displays\", \"duplicate displays\" or \"second screen only\"."


# ---------------------------------------------------------------- printing
def _ps(cmd):
    p = subprocess.run(["powershell", "-NoProfile", "-Command", cmd], capture_output=True, text=True, timeout=30,
                       creationflags=NO_WINDOW)
    return (p.stdout or p.stderr).strip()


def op_print_queue(m, text, session):
    if m["cancel"]:
        return ask_first(session, "desktop", "Cancel every waiting print job on all printers",
                         lambda: _ps("Get-Printer | ForEach-Object { Get-PrintJob -PrinterName $_.Name } | Remove-PrintJob; 'done'")
                         and "Cancelled the waiting print jobs.")
    printers = _ps("Get-Printer | Select-Object -ExpandProperty Name")
    jobs = _ps("Get-Printer | ForEach-Object { Get-PrintJob -PrinterName $_.Name } | "
               "ForEach-Object { \"$($_.PrinterName): $($_.DocumentName) ($($_.JobStatus))\" }")
    default = _ps("(Get-CimInstance Win32_Printer | Where-Object Default).Name")
    return (f"Printers: {', '.join(printers.splitlines()) or 'none'} (default: {default or 'none'}).\n"
            + (f"In the queue:\n{jobs}" if jobs else "Nothing is waiting to print."))


def op_print_file(m, text, session):
    path, copies = m["path"].strip(), int(m["copies"] or 1)
    if not os.path.isfile(path):
        return f"I can't find {path}."
    default = _ps("(Get-CimInstance Win32_Printer | Where-Object Default).Name") or "the default printer"

    def go():
        for _ in range(copies):
            os.startfile(path, "print")  # the file's own app prints it to the default printer
        return f"Sent {os.path.basename(path)} to {default}" + (f" ({copies} copies)." if copies > 1 else ".")
    return ask_first(session, "desktop", f"Print {os.path.basename(path)} on {default}" + (f", {copies} copies" if copies > 1 else ""), go)


# ---------------------------------------------------------------- macros
_rec = {}


def _record(name):
    from pynput import keyboard, mouse
    events, t0 = [], time.time()
    add = lambda *e: events.append([round(time.time() - t0, 3), *e])
    ms = mouse.Listener(on_click=lambda x, y, b, down: add("click", x, y, b.name, down),
                        on_scroll=lambda x, y, dx, dy: add("scroll", x, y, dx, dy))
    key = lambda k: getattr(k, "char", None) or str(k)
    kb = keyboard.Listener(on_press=lambda k: add("press", key(k)), on_release=lambda k: add("release", key(k)))
    ms.start()
    kb.start()
    _rec.update(name=name, events=events, listeners=(ms, kb), t0=t0)


def _stop():
    if not _rec:
        return None
    for listener in _rec["listeners"]:
        listener.stop()
    name, events = _rec["name"], _rec["events"]
    _rec.clear()
    os.makedirs(MACROS, exist_ok=True)
    with open(os.path.join(MACROS, name + ".json"), "w", encoding="utf-8") as f:
        json.dump(events, f)
    return name, events


def play(events, speed=1.0):
    from pynput import keyboard, mouse
    ms, kb, start = mouse.Controller(), keyboard.Controller(), time.time()
    parse = lambda k: getattr(keyboard.Key, k.removeprefix("Key."), None) if k.startswith("Key.") else k
    for t, kind, *args in events:
        time.sleep(max(0.0, t / speed - (time.time() - start)))
        if kind == "click":
            x, y, button, down = args
            ms.position = (x, y)
            (ms.press if down else ms.release)(getattr(mouse.Button, button))
        elif kind == "scroll":
            ms.position = (args[0], args[1])
            ms.scroll(args[2], args[3])
        elif (k := parse(args[0])) is not None:
            (kb.press if kind == "press" else kb.release)(k)


def op_macro(m, text, session):
    act = m["act"].lower()
    name = re.sub(r"[^\w-]+", "_", (m["name"] or "").strip()).strip("_") or "macro"
    saved = sorted(f[:-5] for f in os.listdir(MACROS) if f.endswith(".json")) if os.path.isdir(MACROS) else []
    if act == "list":
        return "Saved macros: " + (", ".join(saved) if saved else "none yet - say \"record a macro called <name>\".")
    if act.startswith("stop"):
        done = _stop()
        return f"Saved macro '{done[0]}' ({len(done[1])} steps)." if done else "I'm not recording a macro."
    if act in ("record", "start recording"):
        if _rec:
            return f"Already recording '{_rec['name']}' - say \"stop recording macro\"."
        _record(name)
        threading.Timer(300, _stop).start()  # never records for more than 5 minutes
        return (f"Recording macro '{name}' - your clicks and keys (stored only on this PC, in data/macros). "
                "Say \"stop recording macro\" when done (5 minutes at most).")
    if name not in saved:
        return f"There's no macro '{name}'. Saved: {', '.join(saved) or 'none'}."
    if act == "delete":
        return ask_first(session, "desktop", f"Delete macro '{name}'",
                         lambda: os.remove(os.path.join(MACROS, name + ".json")) or f"Deleted macro '{name}'.")
    with open(os.path.join(MACROS, name + ".json"), encoding="utf-8") as f:
        events = json.load(f)
    return ask_first(session, "desktop", f"Play macro '{name}' ({len(events)} steps) - it will move the mouse and type. "
                                         "Don't touch the mouse while it runs",
                     lambda: play(events) or f"Played macro '{name}'.")
