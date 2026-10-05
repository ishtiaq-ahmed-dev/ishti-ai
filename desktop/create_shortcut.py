"""Creates the ISHTI desktop shortcut (Start) for the current user's desktop and Start Menu.

Sets the explicit Windows Application User Model ID (PKEY_AppUserModel_ID) on the shortcuts
so Windows Taskbar pinning cleanly associates the pinned taskbar button with ISHTI's official logo
rather than falling back to the generic Python interpreter icon.
"""
import os
import sys
import win32com.client

APP_ID = "ISHTI.PersonalAI.Assistant.Desktop.1.0"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
from ishti.core.logutil import swallowed
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
    shortcut = None

    # Set explicit AppUserModelID on the shortcut via Windows Property Store
    try:
        from win32com.propsys import propsys, pscon
        store = propsys.SHGetPropertyStoreFromParsingName(
            shortcut_path, None, 2, propsys.IID_IPropertyStore
        )
        prop = propsys.PROPVARIANTType(APP_ID)
        store.SetValue(pscon.PKEY_AppUserModel_ID, prop)
        store.Commit()
        store = None
    except Exception as exc:
        swallowed('desktop/create_shortcut.py:_setup_shortcut:40')

    print(f"[SUCCESS] Configured shortcut with AppID: {shortcut_path}")


def create_desktop_shortcuts():
    """Configures the official ISHTI shortcuts for Desktop, Start Menu Programs, and Taskbar."""
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

    # 1. Desktop shortcut
    _setup_shortcut(ws, os.path.join(desktop, "ISHTI.lnk"), target, args, desc, ICON)

    # 2. Start Menu Programs shortcut (CRITICAL for Windows Taskbar Pinning to find AppUserModelID)
    start_menu_programs = os.path.join(
        os.environ.get("APPDATA", ""), "Microsoft", "Windows", "Start Menu", "Programs"
    )
    if os.path.exists(start_menu_programs):
        _setup_shortcut(
            ws, os.path.join(start_menu_programs, "ISHTI.lnk"), target, args, desc, ICON
        )

    # 3. If User Pinned Taskbar already has a shortcut pointing to ISHTI or python, update its icon
    taskbar_dir = os.path.join(
        os.environ.get("APPDATA", ""),
        "Microsoft",
        "Internet Explorer",
        "Quick Launch",
        "User Pinned",
        "TaskBar",
    )
    if os.path.exists(taskbar_dir):
        for fname in os.listdir(taskbar_dir):
            if fname.lower().endswith(".lnk") and ("ishti" in fname.lower() or "python" in fname.lower()):
                try:
                    fpath = os.path.join(taskbar_dir, fname)
                    _setup_shortcut(ws, fpath, target, args, desc, ICON)
                except Exception:
                    swallowed('desktop/create_shortcut.py:create_desktop_shortcuts:88')

    # Clean up any stale Start ISHTI shortcut
    stale = os.path.join(desktop, "Start ISHTI.lnk")
    if os.path.exists(stale):
        try:
            from win32com.shell import shell, shellcon
            shell.SHFileOperation(
                (0, shellcon.FO_DELETE, stale, None, shellcon.FOF_ALLOWUNDO | shellcon.FOF_NOCONFIRMATION | shellcon.FOF_SILENT, None, None)
            )
        except Exception as exc:
            print(f"[WARN] could not remove {stale}: {exc}")


def remove_stop_shortcut():
    """Owner request: the desktop has no Stop shortcut; the app's own Shut down button / X
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
