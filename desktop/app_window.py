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

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from desktop import timeline, window_state

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


def _on_backend_lost():
    """The backend stayed gone (external POST /api/shutdown, tray Stop, crash): close the window instead of
    showing a dead app. No confirm dialog here - this is not the user's X click (found 2026-09-29)."""
    global _shutting_down
    _shutting_down = True                      # on_closing must not pop its exit dialog
    _schedule_hard_exit(after_s=2.0)           # armed FIRST: destroy() may block on the GUI thread
    _destroy_all_windows()


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
            _schedule_hard_exit(after_s=1.5)   # guarantee the close even if the event is swallowed
            time.sleep(0.2)
            return True
        # User clicked "No" or closed dialog -> cancel window close
        return False
    except Exception as exc:
        print(f"[app_window] MessageBox error: {exc}", file=sys.stderr)
        _trigger_detached_shutdown()
        return True


def _schedule_hard_exit(after_s=1.5):
    """Last-resort close: end this process, the window dies with it.

    Found 2026-09-29: the shutdown overlay reached 100% but the window stayed on screen - the JS ->
    Python close call was not decisive. Cleanup is already running at that point (the detached
    shutdown.py + the backend's own graceful stop + backend_watch in this process), so a hard exit
    here only closes the shell; it never skips data work.
    """
    try:
        threading.Timer(after_s, lambda: os._exit(0)).start()
    except Exception:
        os._exit(0)


def _destroy_all_windows():
    try:
        import webview
        for w in list(webview.windows):
            try:
                w.destroy()
            except Exception:
                pass
    except Exception:
        pass
    # Also destroy the stored handle directly, in case webview.windows is stale or empty
    try:
        w = _remember.get("win")
        if w is not None:
            w.destroy()
    except Exception:
        pass
    # Last resort: use Win32 to force-close any remaining ISHTI windows
    try:
        import win32gui
        import win32con
        def _enum_and_destroy(hwnd, _):
            if win32gui.IsWindowVisible(hwnd):
                title = win32gui.GetWindowText(hwnd)
                if title == TITLE or "ISHTI - Intelligent Personal Assistant" in title or "127.0.0.1:8888" in title:
                    win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
        win32gui.EnumWindows(_enum_and_destroy, None)
    except Exception:
        pass


TITLE = "ISHTI - Intelligent Personal Assistant"

# pywebview walks every PUBLIC attribute of a js_api object (recursively) to build the JS bridge. Holding a window
# in a public attribute made it walk the native WinForms form from the wrong thread - the walk raised COM/UI-thread
# errors, wedged the UI and left the splash frozen at 14 % forever (reproduced 2026-10-02). Anything that is not
# a JS-callable method therefore lives in an underscore attribute, which pywebview skips.


class MainApi:
    """JS bridge exposed to the main app window (interface/app)."""
    def __init__(self, win=None):
        self._win = win

    def set_window(self, win):
        self._win = win

    def minimize_to_tray(self):
        if self._win:
            self._win.minimize()

    def force_close(self):
        _trigger_detached_shutdown()
        # The last-resort exit is armed FIRST but with room for a clean close (3 s): destroy() can block on a wedged GUI thread
        # (2026-09-29: overlay at 100%, window stayed), and if it does, nothing after it would ever run - so the guarantee
        # that the process (and with it the window) ends must not depend on destroy() returning.
        _schedule_hard_exit(after_s=3.0)
        _destroy_all_windows()          # pywebview destroy of every window, the stored handle, then a Win32 WM_CLOSE fallback
        try:
            if self._win:
                self._win.destroy()
        except Exception:
            pass

    def show_window(self):
        if self._win:
            self._win.restore()
            self._win.show()

    def toggle_fullscreen(self):
        if self._win:
            self._win.toggle_fullscreen()

    def toggle_maximize(self):
        if not self._win:
            return
        if _remember["maximized"]:
            self._win.restore()
        else:
            self._win.maximize()

    def window_info(self):
        """Size/state for the regression test and the UI: logical pixels."""
        w = self._win
        return {"width": w.width, "height": w.height, "x": w.x, "y": w.y, "maximized": _remember["maximized"]} if w else {}

    def on_ready(self):
        """splash.html reports a ready backend: go to the dashboard (normally already done by _open_dashboard)."""
        if self._win:
            self._win.load_url(URL)

    def view_log(self):
        log_path = os.path.join(os.environ.get("LOCALAPPDATA", ""), "ISHTI", "logs", "ishti.log")
        if os.path.exists(log_path):
            os.startfile(log_path)

    def retry_boot(self):
        try:
            import requests
            requests.post(f"{URL}/api/ready/retry", timeout=2)
        except Exception:
            pass


# ---- window geometry memory (desktop/window_state.py): saved on resize/move (debounced) and on close
_remember = {"maximized": False, "timer": None, "win": None}


def _save_geometry():
    w = _remember["win"]
    if not w:
        return
    try:
        state = window_state.load()
        state["maximized"] = _remember["maximized"]
        if not _remember["maximized"] and w.width and w.height:      # a maximized window's size is not the "normal" size
            state.update(width=int(w.width), height=int(w.height), x=int(w.x), y=int(w.y))
        window_state.save(state)
    except Exception:
        pass


def _remember_soon(*_):
    if _remember["timer"]:
        _remember["timer"].cancel()
    _remember["timer"] = threading.Timer(1.0, _save_geometry)
    _remember["timer"].daemon = True
    _remember["timer"].start()


def _set_maximized(flag):
    _remember["maximized"] = flag
    _remember_soon()


def _create_main_window(main_api, url):
    """The one window the owner sees: sized from the primary work area (or last session), maximize/fullscreen
    work, remembered on close. `url` is the splash until the backend answers, then the dashboard."""
    import webview
    ax, ay, aw, ah, _scale = window_state.work_area()
    g = window_state.geometry((ax, ay, aw, ah), window_state.load())
    _remember["maximized"] = g["maximized"]
    win = webview.create_window(title=TITLE, url=url, width=g["width"], height=g["height"], x=g["x"], y=g["y"],
                                min_size=window_state.MIN_SIZE, maximized=g["maximized"], confirm_close=False,
                                js_api=main_api)
    main_api.set_window(win)
    _remember["win"] = win
    win.events.closing += on_closing
    win.events.closing += lambda *a: _save_geometry()
    win.events.shown += lambda *a: timeline.mark("window_shown")
    win.events.loaded += lambda *a: timeline.mark("dashboard_loaded" if (win.get_current_url() or "").startswith(URL) else "splash_loaded")
    win.events.resized += _remember_soon
    win.events.moved += _remember_soon
    win.events.maximized += lambda *a: _set_maximized(True)
    win.events.restored += lambda *a: _set_maximized(False)
    return win


def _open_dashboard(win):
    """Runs once the GUI loop is up: the splash is already on screen. The moment the backend's HTTP server answers
    (it binds ~1 s after launch; the models keep loading in the background, the dashboard shows that), switch the
    window to the dashboard. Short connect timeouts: a closed loopback port takes ~2 s to refuse on Windows."""
    import requests
    deadline = time.time() + 120
    while time.time() < deadline:
        try:
            if requests.get(f"{URL}/api/health", timeout=(0.3, 2)).ok:
                timeline.mark("backend_answering")
                win.load_url(URL)
                return
        except Exception:
            pass
        time.sleep(0.1)
    # backend never answered: stay on the splash, whose error state offers retry / view log


def run_app_window():
    import webview
    if os.environ.get("ISHTI_DEBUG_PORT"):      # test hook (tests/test_launcher_window.py): inspect the page over CDP
        webview.settings["REMOTE_DEBUGGING_PORT"] = int(os.environ["ISHTI_DEBUG_PORT"])

    # Exit instead of haunting a dead backend (external stop / crash) - see desktop/backend_watch.py.
    from desktop import backend_watch
    backend_watch.start(_on_backend_lost)

    main_api = MainApi()
    threading.Thread(target=apply_ishti_icons_daemon, daemon=True).start()

    from desktop.focus_ishti import is_running
    splash = f"file:///{os.path.join(ROOT, 'desktop', 'splash.html').replace(os.sep, '/')}"
    # Straight to the dashboard when the backend is already up (window reopened after a close).
    up = is_running(PORT)
    win = _create_main_window(main_api, URL if up else splash)
    webview.start(None if up else _open_dashboard, None if up else win, private_mode=False, icon=ICON_PATH)


if __name__ == "__main__":
    run_app_window()
