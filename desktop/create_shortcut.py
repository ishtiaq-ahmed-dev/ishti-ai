"""Creates the ISHTI desktop shortcuts (Start and Stop) for the current user's desktop."""
import os
import sys
import win32com.client

ROOT = r"D:\ISHTI"
PYTHONW = os.path.join(ROOT, "venv", "Scripts", "pythonw.exe")
LAUNCHER = os.path.join(ROOT, "desktop", "launcher.py")
SHUTDOWN = os.path.join(ROOT, "desktop", "shutdown.py")
ICON = os.path.join(ROOT, "assets", "ishti.ico")


def _setup_shortcut(ws, shortcut_path, target, args, desc, icon_path):
    shortcut = ws.CreateShortcut(shortcut_path)
    shortcut.TargetPath = target
    shortcut.Arguments = args
    shortcut.WorkingDirectory = ROOT
    shortcut.Description = desc
    if icon_path and os.path.exists(icon_path):
        shortcut.IconLocation = f"{icon_path},0"
    shortcut.Save()
    print(f"[SUCCESS] Configured shortcut: {shortcut_path}")


def create_desktop_shortcuts():
    """Start: the singleton launcher (a second click focuses the running instance, never duplicates it).
    Creates both ISHTI.lnk and Start ISHTI.lnk for maximum user convenience."""
    ws = win32com.client.Dispatch("WScript.Shell")
    desktop = ws.SpecialFolders("Desktop")

    exe_path = os.path.join(ROOT, "dist", "ISHTI", "ISHTI.exe")
    if os.path.exists(exe_path):
        target = exe_path
        args = ""
    else:
        target = PYTHONW
        args = f'"{LAUNCHER}"'

    desc = "ISHTI - Intelligent Personal Assistant"

    # Primary shortcuts
    _setup_shortcut(ws, os.path.join(desktop, "ISHTI.lnk"), target, args, desc, ICON)
    _setup_shortcut(ws, os.path.join(desktop, "Start ISHTI.lnk"), target, args, desc, ICON)


def create_stop_shortcut():
    """Stop: the full clean shutdown (process tree, GPU VRAM, browser sessions)."""
    ws = win32com.client.Dispatch("WScript.Shell")
    desktop = ws.SpecialFolders("Desktop")
    shortcut_path = os.path.join(desktop, "Stop ISHTI.lnk")

    stop_bat = os.path.join(ROOT, "stop.bat")
    target = stop_bat if os.path.exists(stop_bat) else os.path.join(ROOT, "venv", "Scripts", "python.exe")
    args = "" if os.path.exists(stop_bat) else f'"{SHUTDOWN}"'

    _setup_shortcut(ws, shortcut_path, target, args, "Stop ISHTI completely and release its resources", ICON)


if __name__ == "__main__":
    create_desktop_shortcuts()
    create_stop_shortcut()
