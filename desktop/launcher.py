"""ISHTI Desktop Application Launcher & Process Supervisor.
Handles singleton window focus, OS shutdown signals (CTRL_CLOSE, CTRL_LOGOFF, CTRL_SHUTDOWN),
and launch of the isolated desktop app window.
"""
import os
import sys
import subprocess
import threading
import time
import ctypes

# 1. Inform Windows Shell of explicit Application User Model ID before any window creation
# This ensures Windows groups and taskbar icons use the official ISHTI logo instead of generic Python
APP_ID = "ISHTI.PersonalAI.Assistant.Desktop.1.0"
try:
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
except Exception:
    pass

ROOT = r"D:\ISHTI"
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from desktop.focus_ishti import is_running, focus_window, reveal_window
from desktop.shutdown import shutdown


def _get_pythonw():
    pw = os.path.join(ROOT, "venv", "Scripts", "pythonw.exe")
    return pw if os.path.exists(pw) else sys.executable


# Windows gives a console control handler only a few seconds before killing the process, and a full
# shutdown (process tree kill + VRAM release + verification) takes longer - so it runs in a detached child
# that outlives this one instead of inline.
def _shutdown_detached():
    try:
        flags = (
            subprocess.CREATE_NO_WINDOW
            | subprocess.DETACHED_PROCESS
            | subprocess.CREATE_NEW_PROCESS_GROUP
            if os.name == "nt"
            else 0
        )
        subprocess.Popen([_get_pythonw(), os.path.join(ROOT, "desktop", "shutdown.py")], cwd=ROOT,
                         creationflags=flags)
        return True
    except Exception as exc:
        print(f"[launcher] detached shutdown failed ({exc}); shutting down inline")
        threading.Thread(target=shutdown, daemon=True).start()
        return True


try:
    import win32api
    import win32con

    def ctrl_handler(ctrl_type):
        # 2: CTRL_CLOSE_EVENT, 5: CTRL_LOGOFF_EVENT, 6: CTRL_SHUTDOWN_EVENT
        if ctrl_type in (2, 5, 6):
            print(f"[launcher] Received OS signal {ctrl_type}, initiating clean shutdown...")
            _shutdown_detached()
            return True
        return False

    win32api.SetConsoleCtrlHandler(ctrl_handler, True)
except Exception:
    pass


def main():
    # 1. Singleton: an instance is already up (the server answers). Focus its window, or reopen the app window
    #    when it was closed - either way exit: starting a second startup.py is what duplicated ISHTI.
    if is_running():
        if not focus_window():
            reveal_window()
        sys.exit(0)

    # 2. Start ISHTI backend headless (Job Object + rotating logs in %LOCALAPPDATA%\ISHTI\logs)
    # Always use pythonw.exe so no console window is allocated
    python_exe = _get_pythonw()

    flags = (
        subprocess.CREATE_NO_WINDOW
        | subprocess.DETACHED_PROCESS
        | subprocess.CREATE_NEW_PROCESS_GROUP
        if os.name == "nt"
        else 0
    )

    subprocess.Popen(
        [python_exe, "-m", "ishti", "8888"],
        cwd=ROOT,
        creationflags=flags,
    )

    # 2b. System tray icon: global mic ON/OFF + dashboard + Stop.
    try:
        tray_py = os.path.join(ROOT, "desktop", "tray.py")
        subprocess.Popen([python_exe, tray_py], cwd=ROOT, creationflags=flags)
    except Exception as exc:
        print(f"[launcher] tray icon unavailable ({exc}); dashboard/voice toggles still work")

    # 3. Launch the desktop GUI window directly (instant animated splash -> main window on ready)
    from desktop.app_window import run_app_window
    run_app_window()


if __name__ == "__main__":
    main()
