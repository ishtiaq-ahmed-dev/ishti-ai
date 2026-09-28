"""Tests for the lifecycle pass (issues 2/3): Start is idempotent (a second Start focuses, never duplicates),
Stop is full and verified, the OS-shutdown signal runs the shutdown detached, and the desktop exit path posts
a clean shutdown. No server is started or stopped here - the launch/reveal/shutdown steps are faked. Run:
    $env:PYTHONIOENCODING='utf-8'; D:\\ISHTI\\venv\\Scripts\\python.exe D:\\ISHTI\\tests\\test_lifecycle.py"""
import os
import subprocess
import sys

sys.path.insert(0, r"D:\ISHTI")

from desktop import focus_ishti, launcher, shutdown

# ---- 1. Start is idempotent: an already-running instance is focused, a second startup.py is never launched ---
real_is_running, real_focus, real_reveal = launcher.is_running, launcher.focus_window, launcher.reveal_window
real_popen = launcher.subprocess.Popen
spawned = []


class _FakePopen:
    def __init__(self, args, **kw):
        spawned.append(list(args))


try:
    launcher.is_running = lambda *a, **k: True
    launcher.focus_window = lambda *a, **k: True
    launcher.reveal_window = lambda *a, **k: False
    launcher.subprocess.Popen = _FakePopen
    try:
        launcher.main()
        raise AssertionError("main() must not continue when an instance is already running")
    except SystemExit as e:
        assert e.code == 0, e.code
    assert not spawned, f"a second instance was launched: {spawned}"

    # the window was closed but the server is up: focus fails, so the window is reopened - still no new instance
    launcher.focus_window = lambda *a, **k: False
    revealed = []
    launcher.reveal_window = lambda: revealed.append(True) or True
    try:
        launcher.main()
        raise AssertionError("main() must exit instead of starting a second instance")
    except SystemExit as e:
        assert e.code == 0
    assert revealed == [True], "the app window must be reopened, not replaced by a second instance"
    assert not spawned, f"a second instance was launched: {spawned}"

    # nothing running -> this is the one case that starts ISHTI
    launcher.is_running = lambda *a, **k: False
    launcher.focus_window = lambda *a, **k: False
    try:
        launcher.main()
    except SystemExit:
        pass
    # startup.py is launched exactly once; the system-tray icon (Section 7) is the one extra process allowed
    startup_spawns = [s for s in spawned if s[-1].endswith("startup.py")]
    tray_spawns = [s for s in spawned if s[-1].endswith("tray.py")]
    assert len(startup_spawns) == 1, spawned
    assert len(tray_spawns) == 1 and "tray.py" in tray_spawns[0][-1], spawned
finally:
    launcher.is_running, launcher.focus_window, launcher.reveal_window = real_is_running, real_focus, real_reveal
    launcher.subprocess.Popen = real_popen

# focus_ishti.reveal_window exists and opens the app window at the local server

# ---- 3. the OS shutdown signal runs the shutdown DETACHED (Windows kills the handler after seconds) ----------
real_dpop = launcher.subprocess.Popen
detached = []


class _DetachPopen:
    def __init__(self, args, **kw):
        detached.append((list(args), kw.get("creationflags", 0)))


try:
    launcher.subprocess.Popen = _DetachPopen
    assert launcher._shutdown_detached() is True
finally:
    launcher.subprocess.Popen = real_dpop
assert len(detached) == 1 and detached[0][0][-1].endswith("shutdown.py"), detached
assert detached[0][1] != 0, "the shutdown child must be detached so it outlives the closing process"
if hasattr(launcher, "ctrl_handler"):
    # a real console signal (2/5/6) must return fast and hand the work to the detached child
    assert launcher.ctrl_handler(0) is False, "only close/logoff/shutdown events are handled"

# ---- 4. the dashboard exit path is a real clean-shutdown call ----------------------------------------------
app = r"D:\ISHTI\interface\app\src\App.tsx"
src = open(app, encoding="utf-8").read()
assert "Do you want to exit ISHTI?" in src, "the native close confirmation is required"
assert "/api/shutdown" in src, "Yes must trigger the clean shutdown"
assert "sendBeacon" in src, "the shutdown must survive the page unloading (native Yes closes the page first)"
assert "pagehide" in src, "the native prompt's Yes must be what triggers the shutdown"

# the bundle that is actually served contains it
dist_js = [f for f in os.listdir(r"D:\ISHTI\interface\app\dist\assets") if f.endswith(".js")]
assert dist_js, "the built bundle is missing"
bundle = open(os.path.join(r"D:\ISHTI\interface\app\dist\assets", dist_js[0]), encoding="utf-8").read()
for needle in ("Do you want to exit ISHTI?", "/api/shutdown", "sendBeacon", "pagehide"):
    assert needle in bundle, f"the served bundle is stale - it lacks {needle!r}"

# the server endpoint exists and answers
server = r"D:\ISHTI\interface\web_ui\server.py"
assert '@app.post("/api/shutdown")' in open(server, encoding="utf-8").read()

# ---- 5. start.bat and stop.bat are wired to the singleton check and the full stop ---------------------------
assert "focus_ishti.py" in open(r"D:\ISHTI\start.bat", encoding="utf-8").read()
assert "shutdown.py" in open(r"D:\ISHTI\stop.bat", encoding="utf-8").read()
# stop.bat verifies nothing is left behind
assert "verify_clean_state" in open(r"D:\ISHTI\desktop\shutdown.py", encoding="utf-8").read()
assert "release_gpu_vram" in open(r"D:\ISHTI\desktop\shutdown.py", encoding="utf-8").read()

print("[self-check] lifecycle OK - Start is idempotent, Stop verifies, OS shutdown is detached, exit confirms")

assert callable(focus_ishti.reveal_window), "launcher needs focus_ishti.reveal_window"
if focus_ishti.is_running():
    # ISHTI is live right now (the owner's session, e.g. the Web UI server). Everything above ran,
    # and what follows is process-safe, but the old hard assert failed the whole suite whenever the
    # owner simply had ISHTI open. Skip the not-running-only checks instead - and NEVER touch the
    # running instance: this test stops nothing, kills nothing, shuts nothing down.
    print("[self-check] lifecycle OK (static checks) - ISHTI is currently running; the "
          "not-running-only checks were skipped and the live instance was left untouched")
    sys.exit(0)

# ---- 2. Stop never kills its own caller (self + ancestors) -------------------------------------------------
keep = shutdown._self_and_ancestors()
assert os.getpid() in keep, keep
assert len(keep) >= 1
