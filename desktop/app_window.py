import os
import sys
import subprocess
import threading
import time
import ctypes

# 1. Inform Windows Shell of explicit Application User Model ID before any window or webview creation
# This prevents Windows Taskbar from grouping under Python and ensures the official ISHTI logo is displayed
APP_ID = "ISHTI.PersonalAI.Assistant.Desktop.1.0"
try:
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
except Exception:
    pass

ROOT = r"D:\ISHTI"
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

PORT = 8888
URL = f"http://127.0.0.1:{PORT}"
ICON_PATH = os.path.join(ROOT, "assets", "ishti.ico")

_shutting_down = False


def _get_pythonw():
    pw = os.path.join(ROOT, "venv", "Scripts", "pythonw.exe")
    return pw if os.path.exists(pw) else sys.executable


def apply_ishti_icons_daemon():
    """Background daemon ensuring all ISHTI windows (splash + dashboard) have the official icon attached via Win32."""
    try:
        import win32gui
        import win32con
        if not os.path.exists(ICON_PATH):
            return

        # Load big (32x32 for taskbar & Alt-Tab) and small (16x16 for titlebar & tray)
        hicon_big = win32gui.LoadImage(
            0, ICON_PATH, win32con.IMAGE_ICON, 32, 32, win32con.LR_LOADFROMFILE
        )
        hicon_small = win32gui.LoadImage(
            0, ICON_PATH, win32con.IMAGE_ICON, 16, 16, win32con.LR_LOADFROMFILE
        )
        if not hicon_big and not hicon_small:
            return

        def apply_to_matching():
            def enum_cb(hwnd, _):
                if win32gui.IsWindowVisible(hwnd):
                    title = win32gui.GetWindowText(hwnd)
                    if title == "ISHTI" or "ISHTI - Intelligent Personal Assistant" in title or "127.0.0.1:8888" in title:
                        if hicon_big:
                            win32gui.SendMessage(hwnd, win32con.WM_SETICON, win32con.ICON_BIG, hicon_big)
                        if hicon_small:
                            win32gui.SendMessage(hwnd, win32con.WM_SETICON, win32con.ICON_SMALL, hicon_small)
            win32gui.EnumWindows(enum_cb, None)

        # Run periodically across the startup & transition window
        for _ in range(40):
            apply_to_matching()
            time.sleep(0.25)
    except Exception:
        pass


def _trigger_detached_shutdown():
    """Spawns desktop/shutdown.py as a detached windowless process so GPU VRAM and servers are released even after window closes."""
    global _shutting_down
    if _shutting_down:
        return
    _shutting_down = True
    try:
        flags = (
            subprocess.CREATE_NO_WINDOW
            | subprocess.DETACHED_PROCESS
            | subprocess.CREATE_NEW_PROCESS_GROUP
            if os.name == "nt"
            else 0
        )
        shutdown_py = os.path.join(ROOT, "desktop", "shutdown.py")
        subprocess.Popen([_get_pythonw(), shutdown_py], cwd=ROOT, creationflags=flags)
    except Exception as exc:
        print(f"[app_window] detached shutdown failed ({exc}), falling back to inline", file=sys.stderr)
        try:
            from desktop.shutdown import shutdown
            threading.Thread(target=shutdown, daemon=True).start()
        except Exception:
            pass


def on_closing(*args, **kwargs):
    """Intercepts the native window Cross [X] button.
    Prompts the user: "Do you want to exit ISHTI?" with Yes/No buttons.
    Reassures the user that all data is automatically saved.
    Returns False to cancel closing if user clicks 'No', or True to exit if user clicks 'Yes'.
    """
    global _shutting_down
    if _shutting_down:
        return True

    # 4 (MB_YESNO) | 32 (MB_ICONQUESTION) | 256 (MB_DEFBUTTON2: default to 'No')
    MB_YESNO = 4
    MB_ICONQUESTION = 32
    MB_DEFBUTTON2 = 256
    IDYES = 6

    message = (
        "Do you want to exit ISHTI?\n\n"
        "All your conversations, settings, and data are automatically saved."
    )
    title = "Exit ISHTI"

    try:
        res = ctypes.windll.user32.MessageBoxW(
            0,
            message,
            title,
            MB_YESNO | MB_ICONQUESTION | MB_DEFBUTTON2,
        )
        if res == IDYES:
            _trigger_detached_shutdown()
            time.sleep(0.2)
            return True
        # User clicked "No" or closed dialog -> cancel window close
        return False
    except Exception as exc:
        print(f"[app_window] MessageBox error: {exc}", file=sys.stderr)
        _trigger_detached_shutdown()
        return True


class MainApi:
    """JS bridge exposed to the main app window (interface/app)."""
    def __init__(self, win=None):
        self.win = win

    def set_window(self, win):
        self.win = win

    def minimize_to_tray(self):
        if self.win:
            self.win.minimize()

    def force_close(self):
        _trigger_detached_shutdown()
        if self.win:
            self.win.destroy()

    def show_window(self):
        if self.win:
            self.win.restore()
            self.win.show()


class SplashApi:
    """JS bridge exposed to splash.html."""
    def __init__(self, splash_win, main_api):
        self.splash_win = splash_win
        self.main_api = main_api

    def on_ready(self):
        import webview
        main_win = webview.create_window(
            title="ISHTI - Intelligent Personal Assistant",
            url=URL,
            width=1280,
            height=820,
            min_size=(960, 600),
            confirm_close=False,
            js_api=self.main_api,
        )
        self.main_api.set_window(main_win)
        main_win.events.closing += on_closing
        threading.Thread(target=apply_ishti_icons_daemon, daemon=True).start()
        if self.splash_win:
            self.splash_win.destroy()

    def view_log(self):
        log_path = os.path.join(os.environ.get("LOCALAPPDATA", ""), "ISHTI", "logs", "ishti.log")
        if os.path.exists(log_path):
            os.startfile(log_path)

    def retry_boot(self):
        try:
            import requests
            requests.post("http://127.0.0.1:8888/api/ready/retry", timeout=2)
        except Exception:
            pass


def run_app_window():
    import webview
    from desktop.focus_ishti import is_running

    main_api = MainApi()
    threading.Thread(target=apply_ishti_icons_daemon, daemon=True).start()

    # Check if backend is already fully ready
    already_ready = False
    if is_running(PORT):
        try:
            import requests
            r = requests.get("http://127.0.0.1:8888/api/ready", timeout=1.0)
            if r.status_code == 200 and r.json().get("ready"):
                already_ready = True
        except Exception:
            pass

    if already_ready:
        window = webview.create_window(
            title="ISHTI - Intelligent Personal Assistant",
            url=URL,
            width=1280,
            height=820,
            min_size=(960, 600),
            confirm_close=False,
            js_api=main_api,
        )
        main_api.set_window(window)
        window.events.closing += on_closing
        webview.start(private_mode=False, icon=ICON_PATH)
    else:
        splash_path = os.path.join(ROOT, "desktop", "splash.html")
        splash_url = f"file:///{splash_path.replace(os.sep, '/')}"
        splash_api = SplashApi(None, main_api)
        splash_win = webview.create_window(
            title="ISHTI",
            url=splash_url,
            width=520,
            height=340,
            frameless=True,
            easy_drag=True,
            on_top=True,
            js_api=splash_api,
        )
        splash_api.splash_win = splash_win
        webview.start(private_mode=False, icon=ICON_PATH)


if __name__ == "__main__":
    run_app_window()
