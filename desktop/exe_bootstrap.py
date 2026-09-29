"""ISHTI.exe entry point - a tiny, self-contained bootstrap (ISSUE: "click ISHTI.exe -> error").

Earlier builds froze the whole desktop stack. The half-frozen exe bundled only ~18 files and then
imported raw source modules from the repo, which need stdlib/3rd-party modules the exe did not carry
-> "ModuleNotFoundError: No module named 'json'" in a Windows error dialog and a zombie process with
no window. Freezing the full stack (webview, pythonnet, pywin32, requests, ...) is the fragile
alternative: every new venv dependency silently breaks the exe again.

So the frozen exe is an honest bootstrap and nothing more: it starts desktop/launcher.py with the
project venv's pythonw.exe and exits. ONE canonical launcher (no duplicated lifecycle logic), and the
exe cannot go stale when the venv gains a dependency.

Rule that keeps this file safe to freeze: import ONLY os/sys/subprocess/ctypes here (all stdlib, all
verified present in _internal). tests/test_desktop_bootstrap.py enforces this by AST so the rule
cannot rot silently. desktop/launcher.py holds the real launcher and is never frozen.
"""
import os
import sys
import subprocess


def _project_root():
    """D:\\ISHTI whether we are the frozen exe or run from source.

    --onedir layout: dist\\ISHTI\\ISHTI.exe + dist\\ISHTI\\_internal -> three dirnames up.
    --onefile layout: dist\\ISHTI.exe -> two dirnames up. The candidate that really contains
    desktop\\launcher.py wins, so a future layout change fails loudly, not silently.
    """
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(sys.executable)                 # ...\dist\ISHTI  or  ...\dist
        candidates = [
            os.path.dirname(os.path.dirname(exe_dir)),            # onedir: dist\\ISHTI -> dist -> root
            os.path.dirname(exe_dir),                             # onefile: dist -> root
        ]
        for cand in candidates:
            if os.path.isfile(os.path.join(cand, "desktop", "launcher.py")):
                return cand
        return candidates[0]
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _error_dialog(title, body):
    try:
        import ctypes
        # 0x10 = MB_ICONERROR. ctypes is stdlib and bundled (_ctypes.pyd in _internal).
        ctypes.windll.user32.MessageBoxW(0, body, title, 0x10)
    except Exception:
        pass


def handoff():
    """Start the real launcher under the venv interpreter and exit. Returns a process exit code."""
    root = _project_root()
    launcher_py = os.path.join(root, "desktop", "launcher.py")
    venv_scripts = os.path.join(root, "venv", "Scripts")
    pyw = os.path.join(venv_scripts, "pythonw.exe")
    py = os.path.join(venv_scripts, "python.exe")

    if not os.path.isfile(pyw) and os.path.isfile(py):
        pyw = py                              # python.exe also works: CREATE_NO_WINDOW hides its console
    if not os.path.isfile(pyw):
        _error_dialog(
            "ISHTI - cannot start",
            "ISHTI could not find its Python environment.\n\n"
            f"Expected: {pyw}\n\n"
            "Fix: run D:\\ISHTI\\start.bat once (it restores the venv), then launch ISHTI again.",
        )
        return 1
    if not os.path.isfile(launcher_py):
        _error_dialog(
            "ISHTI - cannot start",
            "ISHTI could not find its launcher module.\n\n"
            f"Expected: {launcher_py}\n\n"
            "Fix: restore the ISHTI project folder (git checkout) and launch again.",
        )
        return 1

    flags = 0
    if os.name == "nt":
        flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    try:
        subprocess.Popen([pyw, launcher_py], cwd=root, creationflags=flags)
        return 0
    except Exception as exc:
        _error_dialog(
            "ISHTI - cannot start",
            f"ISHTI failed to hand off to its launcher.\n\nDetails: {type(exc).__name__}: {exc}\n\n"
            "Run start.bat for the console version with full output.",
        )
        return 1


if __name__ == "__main__":
    sys.exit(handoff())
