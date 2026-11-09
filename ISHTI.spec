# -*- mode: python ; coding: utf-8 -*-
# ISHTI.exe is a TINY BOOTSTRAP, not a frozen application (see desktop/exe_bootstrap.py).
#
# History: freezing desktop/launcher.py pulled in the whole desktop stack (webview, pythonnet,
# pywin32, requests). The half-frozen exe then imported raw repo modules and died at launch with
# "ModuleNotFoundError: No module named 'json'" -> Windows error dialog, no app. Adding single
# hiddenimports (urllib.request in 4b9346d) was whack-a-mole: every new venv dependency would break
# the exe again. The fix is architectural: the exe only hands off to desktop/launcher.py under the
# project venv's pythonw.exe and exits. One canonical launcher, nothing to go stale.
#
# Rule: the entry imports ONLY stdlib (os/sys/subprocess/ctypes). tests/test_desktop_bootstrap.py
# enforces this by AST, so the rule cannot rot silently.

a = Analysis(
    ['desktop/exe_bootstrap.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[
        'subprocess',
        'ctypes',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='ISHTI',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # UPX-packed exes are a common antivirus false positive
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['assets/ishti.ico'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,  # UPX-packed exes are a common antivirus false positive
    upx_exclude=[],
    name='ISHTI',
)
