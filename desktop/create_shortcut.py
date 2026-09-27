"""Creates the ISHTI desktop shortcut on the current user's desktop."""
import os
import sys
import win32com.client

ROOT = r"D:\ISHTI"
PYTHONW = os.path.join(ROOT, "venv", "Scripts", "pythonw.exe")
LAUNCHER = os.path.join(ROOT, "desktop", "launcher.py")
ICON = os.path.join(ROOT, "assets", "ishti.ico")

def create_desktop_shortcut():
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

if __name__ == "__main__":
    create_desktop_shortcut()
