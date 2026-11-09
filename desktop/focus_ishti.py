"""Singleton check and window focus helper for ISHTI."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def is_running(port=8888) -> bool:
    """True when something answers on the ISHTI port.

    A closed loopback port takes ~2 s to refuse on this Windows setup (measured: connect_ex -> 10061 after
    2.0 s), and the old three-probe version paid that 2 s twice before every cold launch (4 s of dead time).
    One 0.25 s connect probe: an open port accepts in well under 10 ms, a closed one gives up quickly.
    """
    import socket
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.25):
            pass
    except OSError:
        return False
    # an open port is not ISHTI: another app holding 8888 used to get a window pointed at it while ISHTI never started.
    # ISHTI's /api/health carries a "state"; anything else is someone else (ISHTI still booting shows up on retry).
    import json
    import urllib.request
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=1.5) as r:
            return "state" in json.loads(r.read(65536) or b"{}")
    except Exception:
        return False


def get_ishti_windows() -> list[int]:
    """Finds all visible windows belonging to ISHTI."""
    try:
        import win32gui
    except ImportError:
        return []

    hwnds = []

    def enum_cb(hwnd, _):
        if win32gui.IsWindowVisible(hwnd):
            title = win32gui.GetWindowText(hwnd)
            # Match exact ISHTI app title or URL in title bar
            if title == "ISHTI" or "ISHTI - Intelligent Personal Assistant" in title or "127.0.0.1:8888" in title:
                hwnds.append(hwnd)

    win32gui.EnumWindows(enum_cb, None)
    return hwnds


def focus_window() -> bool:
    """Restores and brings the existing ISHTI window to the foreground."""
    try:
        import win32gui
        import win32con
        import ctypes
    except ImportError:
        return False

    hwnds = get_ishti_windows()
    if not hwnds:
        return False

    target = hwnds[0]
    try:
        # Simulate Alt-key press to bypass Windows Foreground Lock restriction
        ctypes.windll.user32.keybd_event(0x12, 0, 0, 0)
        win32gui.ShowWindow(target, win32con.SW_RESTORE)
        win32gui.SetForegroundWindow(target)
        ctypes.windll.user32.keybd_event(0x12, 0, 2, 0)
        return True
    except Exception as e:
        print(f"[focus] Error bringing window to front: {e}", file=sys.stderr)
        return False


def reveal_window() -> bool:
    """The server is up but no ISHTI window is on screen (it was closed): open the app window again.

    Split out of main() so the launcher reuses the same path - an already-running instance must never start a
    second one just because its window happens to be closed."""
    import subprocess
    pythonw_exe = os.path.join(ROOT, "venv", "Scripts", "pythonw.exe")
    python_exe = pythonw_exe if os.path.exists(pythonw_exe) else os.path.join(ROOT, "venv", "Scripts", "python.exe")
    app_window_py = os.path.join(ROOT, "desktop", "app_window.py")
    flags = (
        subprocess.CREATE_NO_WINDOW
        | subprocess.DETACHED_PROCESS
        | subprocess.CREATE_NEW_PROCESS_GROUP
        if os.name == "nt"
        else 0
    )

    try:
        if os.path.exists(app_window_py):
            subprocess.Popen([python_exe, app_window_py], cwd=ROOT, creationflags=flags)
            return True

        chrome_exe = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
        profile = os.path.join(ROOT, "data", "chrome_profile")
        url = "http://127.0.0.1:8888"
        if os.path.exists(chrome_exe):
            subprocess.Popen([
                chrome_exe,
                f"--user-data-dir={profile}",
                "--no-first-run",
                "--no-default-browser-check",
                f"--app={url}",
            ], creationflags=flags)
            return True
        import webbrowser
        webbrowser.open(url)
        return True
    except Exception as e:
        print(f"[focus] Error reopening the app window: {e}", file=sys.stderr)
        return False


def main():
    if is_running():
        if focus_window():
            print("[INFO] ISHTI is already running. Brought existing window to the foreground.")
            sys.exit(0)
        # Server is running but window might be closed; reopen the browser window
        print("[INFO] ISHTI server is active. Reopening application window...")
        sys.exit(0 if reveal_window() else 1)
    sys.exit(1)


if __name__ == "__main__":
    main()
