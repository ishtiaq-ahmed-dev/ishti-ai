"""Creates the ISHTI desktop shortcut (Start) for the current user's desktop.

Owner request 2026-09-29: no "Stop ISHTI" desktop shortcut - stopping ISHTI belongs inside the app
(the Shut down button / the window's X), which shuts down cleanly. Any old Stop shortcut on the
desktop is removed here so stale ones disappear.
"""
import os
import sys
import win32com.client

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
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
    """The ONE desktop icon, "ISHTI": the singleton launcher (a second click focuses the running instance, never
    duplicates it). Owner request 2026-10-02: one icon only - a leftover "Start ISHTI.lnk" is moved to the Recycle Bin."""
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

    _setup_shortcut(ws, os.path.join(desktop, "ISHTI.lnk"), target, args, desc, ICON)
    stale = os.path.join(desktop, "Start ISHTI.lnk")
    if os.path.exists(stale):
        try:
            from win32com.shell import shell, shellcon  # Recycle Bin, not a hard delete
            shell.SHFileOperation((0, shellcon.FO_DELETE, stale, None, shellcon.FOF_ALLOWUNDO | shellcon.FOF_NOCONFIRMATION | shellcon.FOF_SILENT, None, None))
        except Exception as exc:
            print(f"[WARN] could not remove {stale}: {exc}")


def remove_stop_shortcut():
    """Owner request 2026-09-29: the desktop has no Stop shortcut; the app's own Shut down button / X
    closes ISHTI cleanly. Deletes any Stop ISHTI.lnk left by older versions."""
    ws = win32com.client.Dispatch("WScript.Shell")
    desktop = ws.SpecialFolders("Desktop")
    shortcut_path = os.path.join(desktop, "Stop ISHTI.lnk")
    if os.path.exists(shortcut_path):
        try:
            os.remove(shortcut_path)
            print(f"[REMOVED] stale shortcut: {shortcut_path}")
        except OSError as exc:
            print(f"[WARN] could not remove {shortcut_path}: {exc}")
    else:
        print("[OK] no Stop ISHTI shortcut on the desktop")


if __name__ == "__main__":
    create_desktop_shortcuts()
    remove_stop_shortcut()
