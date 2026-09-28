"""Creates the ISHTI desktop shortcuts (Start and Stop) for the current user's desktop."""
import os
import sys
import win32com.client

ROOT = r"D:\ISHTI"
PYTHONW = os.path.join(ROOT, "venv", "Scripts", "pythonw.exe")
LAUNCHER = os.path.join(ROOT, "desktop", "launcher.py")
SHUTDOWN = os.path.join(ROOT, "desktop", "shutdown.py")
ICON = os.path.join(ROOT, "assets", "ishti.ico")


def create_desktop_shortcut():
    """Start: the singleton launcher (a second click focuses the running instance, never duplicates it)."""
    ws = win32com.client.Dispatch("WScript.Shell")
    desktop = ws.SpecialFolders("Desktop")
    shortcut_path = os.path.join(desktop, "ISHTI.lnk")

    shortcut = ws.CreateShortcut(shortcut_path)
    shortcut.TargetPath = PYTHONW
    shortcut.Arguments = f'"{LAUNCHER}"'
    shortcut.WorkingDirectory = ROOT
    shortcut.Description = "ISHTI - Intelligent Personal Assistant"
    if os.path.exists(ICON):
        shortcut.IconLocation = f"{ICON},0"
    shortcut.Save()
    print(f"[SUCCESS] Created desktop shortcut at: {shortcut_path}")
    return shortcut_path


def create_stop_shortcut():
    """Stop: the full clean shutdown (process tree, GPU VRAM, browser sessions) with a visible result window -
    a console-less pythonw would hide whether it actually stopped anything."""
    ws = win32com.client.Dispatch("WScript.Shell")
    desktop = ws.SpecialFolders("Desktop")
    shortcut_path = os.path.join(desktop, "Stop ISHTI.lnk")

    stop_bat = os.path.join(ROOT, "stop.bat")
    shortcut = ws.CreateShortcut(shortcut_path)
    shortcut.TargetPath = stop_bat if os.path.exists(stop_bat) else os.path.join(ROOT, "venv", "Scripts", "python.exe")
    shortcut.Arguments = "" if os.path.exists(stop_bat) else f'"{SHUTDOWN}"'
    shortcut.WorkingDirectory = ROOT
    shortcut.Description = "Stop ISHTI completely and release its resources"
    if os.path.exists(ICON):
        shortcut.IconLocation = f"{ICON},0"
    shortcut.Save()
    print(f"[SUCCESS] Created stop shortcut at: {shortcut_path}")
    return shortcut_path


if __name__ == "__main__":
    create_desktop_shortcut()
    create_stop_shortcut()
