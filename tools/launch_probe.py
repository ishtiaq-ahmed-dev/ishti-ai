"""Measure what the owner sees when double-clicking the launcher: python tools/launch_probe.py [--shot out.png] [--keep]

Starts desktop/launcher.py exactly as start.bat does and records, in seconds since the spawn: the launcher's own
stage marks (desktop/timeline.py), the first/main window with its real pixel size as a % of the primary work area,
the backend's boot stages from /api/ready, and the time the dashboard and the full backend were ready.
Prints one JSON line; saves a screenshot of the window with --shot; stops what it started (POST /api/shutdown)
unless --keep. Refuses to run when ISHTI already listens on the port."""
import ctypes
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
ctypes.windll.user32.SetProcessDPIAware()      # real pixels, like the app's own process (else sizes are scaled down by 1.25)
PORT = 8888
TITLE = "ISHTI - Intelligent Personal Assistant"


def work_area():
    from ctypes import wintypes
    r = wintypes.RECT()
    ctypes.windll.user32.SystemParametersInfoW(0x30, 0, ctypes.byref(r), 0)   # SPI_GETWORKAREA
    return r.right - r.left, r.bottom - r.top


def main_window():
    import win32gui
    found = []

    def cb(h, _):
        if win32gui.IsWindowVisible(h) and win32gui.GetWindowText(h) == TITLE:
            l, t, r, b = win32gui.GetWindowRect(h)
            found.append({"hwnd": h, "w": r - l, "h": b - t, "x": l, "y": t, "maximized": win32gui.GetWindowPlacement(h)[1] == 3})
    win32gui.EnumWindows(cb, None)
    return found[0] if found else None


def listening():
    with socket.socket() as s:
        s.settimeout(0.25)       # a closed loopback port takes ~2 s to refuse on this machine: do not wait for it
        return s.connect_ex(("127.0.0.1", PORT)) == 0


def get_json(path, timeout=0.6):
    try:
        return json.load(urllib.request.urlopen(f"http://127.0.0.1:{PORT}{path}", timeout=timeout))
    except Exception:
        return None


def main(argv):
    from desktop import timeline
    if listening():
        sys.exit(f"port {PORT} already in use - stop that instance first")
    pw = os.path.join(ROOT, "venv", "Scripts", "pythonw.exe")
    t0 = time.time()
    subprocess.Popen([pw, os.path.join(ROOT, "desktop", "launcher.py")], cwd=ROOT)
    wa = work_area()
    out = {"work_area": wa, "stages": []}
    last_stage = None
    while time.time() - t0 < 120:
        t = round(time.time() - t0, 2)
        w = main_window()
        if w and "window_first" not in out:
            out["window_first"] = t
            out["window"] = {k: w[k] for k in ("w", "h", "x", "y", "maximized")}
            out["window"]["pct_of_work_area"] = round(100 * w["w"] * w["h"] / (wa[0] * wa[1]))
        r = get_json("/api/ready")
        if r:
            out.setdefault("backend_answering", t)
            if r.get("stage") != last_stage:
                last_stage = r.get("stage")
                out["stages"].append([t, r.get("stage"), r.get("progress")])
            if r.get("ready"):
                out.setdefault("fully_ready", t)
        tl = timeline.read()
        if "dashboard_loaded" in tl and "dashboard_loaded" not in out:
            out["dashboard_loaded"] = round(tl["dashboard_loaded"] - t0, 2)
        if "fully_ready" in out and "dashboard_loaded" in out:
            break
        time.sleep(0.1)
    out["launcher_marks"] = {k: round(v - t0, 2) for k, v in timeline.read().items()}
    time.sleep(1.0)
    w = main_window()  # timings only: no screenshot is taken
    print(json.dumps(out))
    if "--keep" not in argv:
        try:
            urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{PORT}/api/shutdown", method="POST", data=b""), timeout=3)
        except Exception:
            pass


if __name__ == "__main__":
    main(sys.argv[1:])
