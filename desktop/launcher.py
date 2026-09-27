"""ISHTI Desktop Application Launcher & Process Supervisor.
Handles singleton window focus, OS shutdown signals (CTRL_CLOSE, CTRL_LOGOFF, CTRL_SHUTDOWN),
and launch of the isolated desktop app window.
"""
import os
import sys
import subprocess
import time

ROOT = r"D:\ISHTI"
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from desktop.focus_ishti import is_running, focus_window
from desktop.shutdown import shutdown

try:
    import win32api
    import win32con

    def ctrl_handler(ctrl_type):
        # 2: CTRL_CLOSE_EVENT, 5: CTRL_LOGOFF_EVENT, 6: CTRL_SHUTDOWN_EVENT
        if ctrl_type in (2, 5, 6):
            print(f"[launcher] Received OS signal {ctrl_type}, initiating clean shutdown...")
            shutdown()
            return True
        return False

    win32api.SetConsoleCtrlHandler(ctrl_handler, True)
except Exception:
    pass


def main():
    # 1. Singleton Check: If already running, focus existing window and exit immediately
    if is_running():
        if focus_window():
            sys.exit(0)

    # 2. Start ISHTI via start.bat or startup.py
    python_exe = os.path.join(ROOT, "venv", "Scripts", "python.exe")
    startup_py = os.path.join(ROOT, "orchestrator", "startup.py")

    flags = (
        subprocess.CREATE_NO_WINDOW
        | subprocess.DETACHED_PROCESS
        | subprocess.CREATE_NEW_PROCESS_GROUP
        if os.name == "nt"
        else 0
    )

    proc = subprocess.Popen(
        [python_exe, startup_py],
        cwd=ROOT,
        creationflags=flags,
    )

    # Wait up to 10s for the window to appear and bring it to foreground
    for _ in range(20):
        time.sleep(0.5)
        if is_running():
            focus_window()
            break


if __name__ == "__main__":
    main()
