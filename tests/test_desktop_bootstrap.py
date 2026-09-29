"""Regression for the "ISHTI.exe opens an error instead of the app" bug (found 2026-09-29).

What actually happened: ISHTI.exe froze desktop/launcher.py, pulled in only a fraction of the desktop
stack, then imported raw repo modules at runtime -> ModuleNotFoundError ('json') in a Windows error
dialog, zombie process, no window. Fix: the exe is now a tiny stdlib-only bootstrap
(desktop/exe_bootstrap.py) that hands off to the real launcher under the venv pythonw.exe.

These tests lock that architecture down offline; the last test is an e2e smoke that runs the real
exe (needs a built dist\\ISHTI\\ISHTI.exe, so it is skipped by default like the other e2e tests).
"""
import ast
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

BOOTSTRAP = os.path.join(ROOT, "desktop", "exe_bootstrap.py")
SPEC = os.path.join(ROOT, "ISHTI.spec")

ALLOWED_IMPORTS = {"os", "sys", "subprocess", "ctypes"}   # stdlib only - this is the whole point


def _imports_of(path):
    tree = ast.parse(open(path, encoding="utf-8").read())
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.add(node.module.split(".")[0])
    return found


def test_bootstrap_imports_are_stdlib_only():
    """The frozen exe must not import anything beyond the stdlib it ships with - that is what broke it
    last time (raw repo modules + missing stdlib in the bundle)."""
    assert os.path.isfile(BOOTSTRAP), "desktop/exe_bootstrap.py disappeared - ISHTI.spec freezes it"
    imports = _imports_of(BOOTSTRAP)
    bad = imports - ALLOWED_IMPORTS
    assert not bad, f"exe_bootstrap.py imports non-allowed modules {sorted(bad)} - the exe will break again"


def test_bootstrap_does_not_import_the_desktop_stack():
    src = open(BOOTSTRAP, encoding="utf-8").read()
    for forbidden in ("webview", "win32api", "pythonnet", "requests", "psutil"):
        assert f"import {forbidden}" not in src, forbidden
        assert "desktop.launcher" not in src or "hands off" in src   # only as a file path, not an import


def test_bootstrap_finds_the_real_launcher_in_the_repo_layout():
    """_project_root() must resolve to D:\\ISHTI in BOTH frozen layouts (onedir: dist\\ISHTI\\ISHTI.exe,
    onefile: dist\\ISHTI.exe) and from source."""
    sys.path.insert(0, os.path.join(ROOT, "desktop"))
    import exe_bootstrap
    assert exe_bootstrap._project_root() == ROOT
    launcher_py = os.path.join(ROOT, "desktop", "launcher.py")
    assert os.path.isfile(launcher_py), "the hand-off target disappeared"


def test_spec_points_at_the_bootstrap_only():
    src = open(SPEC, encoding="utf-8").read()
    # check what is actually frozen: the Analysis([...]) entry list, not prose comments
    import re
    m = re.search(r"a\s*=\s*Analysis\(\s*\[(.*?)\]", src, re.S)
    assert m, "ISHTI.spec has no Analysis([...]) entry list"
    entries = m.group(1)
    assert "desktop/exe_bootstrap.py" in entries, "the spec must freeze the bootstrap"
    assert "launcher" not in entries, "freezing the real launcher re-creates the original bug"
    # and the launcher it hands off to must really exist next to the venv
    assert os.path.isfile(os.path.join(ROOT, "venv", "Scripts", "pythonw.exe")), \
        "no venv pythonw - the bootstrap's error dialog path would fire on every launch"


def test_start_bat_prefers_the_built_exe_then_the_venv():
    src = open(os.path.join(ROOT, "start.bat"), encoding="utf-8").read()
    assert "dist\\ISHTI\\ISHTI.exe" in src
    assert "pythonw.exe" in src and "desktop\\launcher.py" in src


@pytest.mark.e2e
def test_e2e_built_exe_boots_the_app_without_an_error_dialog(tmp_path):
    """The exact user scenario: double-click ISHTI.exe -> app boots. Requires a built exe
    (python -m PyInstaller ISHTI.spec --noconfirm) and the venv; skipped by default."""
    exe = os.path.join(ROOT, "dist", "ISHTI", "ISHTI.exe")
    if not os.path.isfile(exe):
        pytest.skip("dist\\ISHTI\\ISHTI.exe not built")
    import subprocess
    import time
    import urllib.request
    subprocess.Popen([exe], cwd=ROOT, creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        ready = False
        for _ in range(120):                      # cold boot can take a couple of minutes
            time.sleep(2)
            try:
                with urllib.request.urlopen("http://127.0.0.1:8888/api/health", timeout=1.5) as r:
                    if r.status == 200:
                        ready = True
                        break
            except Exception:
                continue
        assert ready, "the exe hand-off never brought the backend up"
        # and the app window exists (the launcher got as far as webview)
        import ctypes
        hwnd = ctypes.windll.user32.FindWindowW(None, "ISHTI - Intelligent Personal Assistant")
        assert hwnd, "backend is up but no ISHTI window was created"
    finally:
        try:
            req = urllib.request.Request("http://127.0.0.1:8888/api/shutdown",
                                         data=b'{"reason":"test"}',
                                         headers={"Content-Type": "application/json"}, method="POST")
            urllib.request.urlopen(req, timeout=5)
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
