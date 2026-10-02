"""Where the ISHTI window opens, and what it remembers.

First launch: 92 % x 94 % of the primary monitor's WORK AREA (the screen minus the taskbar), centred.
After that: the size/position/maximized flag from the last session (%LOCALAPPDATA%\\ISHTI\\window.json),
clamped to whatever monitor layout exists now (a saved window that is off-screen or bigger than the
screen would otherwise open unreachable).

All numbers here are LOGICAL pixels - what pywebview's create_window() takes. The OS reports the work
area in physical pixels, so it is divided by the display scale (125 % on the owner's 2560x1440 screen).
No GUI imports at module level: the geometry rules are testable on their own (tests/test_window_state.py).
"""
import ctypes
import json
import os

FRACTION = (0.92, 0.94)       # share of the work area a first-launch window takes
MIN_SIZE = (960, 600)         # same floor the window enforces
VISIBLE_MIN = 120             # at least this many logical px of the title bar must stay on a screen
STATE_FILE = os.environ.get("ISHTI_WINDOW_STATE") or os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "ISHTI", "window.json")


def work_area():
    """(x, y, w, h, scale) of the primary monitor's work area in logical pixels. Falls back to 1280x720 off Windows."""
    try:
        user32 = ctypes.windll.user32
        user32.SetProcessDPIAware()          # same awareness pywebview sets later: the numbers must agree with it
        from ctypes import wintypes
        r = wintypes.RECT()
        user32.SystemParametersInfoW(0x30, 0, ctypes.byref(r), 0)   # SPI_GETWORKAREA, primary monitor
        scale = (user32.GetDpiForSystem() or 96) / 96
        return (round(r.left / scale), round(r.top / scale), round((r.right - r.left) / scale),
                round((r.bottom - r.top) / scale), scale)
    except Exception:
        return 0, 0, 1280, 720, 1.0


def load(path=None):
    try:
        with open(path or STATE_FILE, encoding="utf-8") as f:
            s = json.load(f)
        return s if isinstance(s, dict) else {}
    except (OSError, ValueError):
        return {}


def save(state, path=None):
    path = path or STATE_FILE
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f)
        os.replace(tmp, path)
    except OSError:
        pass   # remembering the window is a convenience; never worth failing a close over


def geometry(area, saved=None):
    """-> {x, y, width, height, maximized} for create_window. `area` = work_area()[:4]; `saved` = what load() returned."""
    ax, ay, aw, ah = area
    w, h = round(aw * FRACTION[0]), round(ah * FRACTION[1])
    g = {"width": w, "height": h, "x": ax + (aw - w) // 2, "y": ay + (ah - h) // 2, "maximized": False}
    saved = saved or {}
    try:
        sw, sh, sx, sy = (int(saved[k]) for k in ("width", "height", "x", "y"))
    except (KeyError, TypeError, ValueError):
        return g
    if sw < MIN_SIZE[0] or sh < MIN_SIZE[1]:
        return g
    sw, sh = min(sw, aw), min(sh, ah)
    sx = min(max(sx, ax), ax + aw - min(sw, VISIBLE_MIN))      # title bar reachable on this screen
    sy = min(max(sy, ay), ay + ah - 60)
    return {"width": sw, "height": sh, "x": sx, "y": sy, "maximized": bool(saved.get("maximized"))}


if __name__ == "__main__":      # self-check (no GUI, no files)
    a = (0, 0, 2048, 1104)
    first = geometry(a)
    assert first["width"] >= 0.8 * a[2] and first["height"] >= 0.8 * a[3], first
    assert 0 <= first["x"] and first["x"] + first["width"] <= a[2], first
    ok = {"width": 1300, "height": 800, "x": 100, "y": 50, "maximized": True}
    assert geometry(a, ok) == ok
    off = geometry(a, {"width": 1300, "height": 800, "x": 9000, "y": 9000})       # monitor unplugged
    assert off["x"] < a[2] and off["y"] < a[3], off
    assert geometry(a, {"width": 3000, "height": 3000, "x": 0, "y": 0})["width"] == 2048     # bigger than the screen
    assert geometry(a, {"width": 100, "height": 100, "x": 0, "y": 0}) == first               # absurdly small = ignore
    assert geometry(a, {"junk": 1}) == first
    print("window_state OK")
