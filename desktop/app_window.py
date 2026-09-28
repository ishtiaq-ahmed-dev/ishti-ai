import os
import sys
import subprocess
import threading
import time
import ctypes

ROOT = r"D:\ISHTI"
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

PORT = 8888
URL = f"http://127.0.0.1:{PORT}"
ICON_PATH = os.path.join(ROOT, "assets", "ishti.ico")

_shutting_down = False


def _trigger_detached_shutdown():
    """Spawns desktop/shutdown.py as a detached process so GPU VRAM and servers are released even after window closes."""
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
        subprocess.Popen([sys.executable, shutdown_py], cwd=ROOT, creationflags=flags)
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


def run_app_window():
    import webview
    from desktop.focus_ishti import is_running

    # Wait up to 10 seconds for the web server to be ready
    for _ in range(50):
        if is_running(PORT):
            break
        time.sleep(0.2)

    window = webview.create_window(
        title="ISHTI - Intelligent Personal Assistant",
        url=URL,
        width=1280,
        height=820,
        min_size=(960, 600),
        confirm_close=False,
    )
    window.events.closing += on_closing

    # Launch native Edge Chromium WebView2 window
    webview.start(private_mode=False)


if __name__ == "__main__":
    run_app_window()

