"""Singleton check and window focus helper for ISHTI."""
import os
import sys
import urllib.request
import json

ROOT = r"D:\ISHTI"
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def is_running(port=8888) -> bool:
    """Checks if the ISHTI web server is responding."""
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/health", headers={"User-Agent": "ISHTI-Singleton"})
        with urllib.request.urlopen(req, timeout=1.5) as resp:
            return resp.status == 200
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


def main():
    if is_running():
        focused = focus_window()
        if focused:
            print("[INFO] ISHTI is already running. Brought existing window to the foreground.")
            sys.exit(0)
        else:
            # Server is running but window might be closed; reopen browser window
            print("[INFO] ISHTI server is active. Reopening application window...")
            import subprocess
            chrome_exe = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
            profile = r"D:\ISHTI\data\chrome_profile"
            if os.path.exists(chrome_exe):
                flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
                subprocess.Popen([
                    chrome_exe,
                    f"--user-data-dir={profile}",
                    "--no-first-run",
                    "--no-default-browser-check",
                    "--app=http://127.0.0.1:8888",
                ], creationflags=flags)
                sys.exit(0)
            else:
                import webbrowser
                webbrowser.open("http://127.0.0.1:8888")
                sys.exit(0)
    sys.exit(1)


if __name__ == "__main__":
    main()
