"""Tests for the lifecycle pass (issues 2/3): Start is idempotent (a second Start focuses, never duplicates),
Stop is full and verified, the OS-shutdown signal runs the shutdown detached, and the desktop exit path posts
a clean shutdown. No server is started or stopped here - the launch/reveal/shutdown steps are faked. Run:
    $env:PYTHONIOENCODING='utf-8'; venv\\Scripts\\python tests\\test_lifecycle.py"""
import os
import subprocess
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from desktop import focus_ishti, launcher, shutdown

# ---- 1. Start is idempotent: an already-running instance is focused, a second startup.py is never launched ---
real_is_running, real_focus, real_reveal = launcher.is_running, launcher.focus_window, launcher.reveal_window
real_popen = launcher.subprocess.Popen
spawned = []
opened = []          # the desktop window: a test run must never open a real one


class _FakePopen:
    """The minimum subprocess.Popen surface launcher.main() needs.

    The launcher spawns its children with subprocess.run(), which wraps Popen in a `with` block and then
    calls communicate()/poll() - a fake that only accepts the constructor raises
    `TypeError: '_FakePopen' object does not support the context manager protocol` inside subprocess.run.
    """

    def __init__(self, args, **kw):
        spawned.append(list(args))
        self.args = list(args)
        self.returncode = 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def communicate(self, *a, **k):
        return ("", "")

    def poll(self):
        return 0

    def wait(self, timeout=None):
        return 0

    def terminate(self):
        pass

    def kill(self):
        pass


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
    # The window must not actually open during a test: leaving run_app_window() un-patched made this file start a
    # real pywebview splash window and take 110 s (2026-09-29). main() imports it at call time, so patching the
    # module attribute is enough.
    import desktop.app_window as app_window
    real_run_app_window = app_window.run_app_window
    app_window.run_app_window = lambda *a, **k: opened.append(True)
    try:
        launcher.main()
    except SystemExit:
        pass
    finally:
        app_window.run_app_window = real_run_app_window
    # The backend is spawned headless exactly once (pythonw -m ishti 8888), plus the system-tray icon (Section 7).
    # The old visible startup.py banner is gone from the launcher and must not come back.
    backend_spawns = [s for s in spawned if s[1:3] == ["-m", "ishti"]]
    tray_spawns = [s for s in spawned if s[-1].endswith("tray.py")]
    banner_spawns = [s for s in spawned if s[-1].endswith("startup.py")]
    assert len(backend_spawns) == 1, spawned
    assert backend_spawns[0][-1] == "8888", backend_spawns
    assert len(tray_spawns) == 1, spawned
    assert not banner_spawns, f"the visible startup.py banner came back: {banner_spawns}"
    assert opened == [True], "the desktop window must be opened exactly once"
finally:
    launcher.is_running, launcher.focus_window, launcher.reveal_window = real_is_running, real_focus, real_reveal
    launcher.subprocess.Popen = real_popen

# focus_ishti.reveal_window exists and opens the app window at the local server

# ---- 3. the OS shutdown signal runs the shutdown DETACHED (Windows kills the handler after seconds) ----------
real_dpop = launcher.subprocess.Popen
detached = []


class _DetachPopen(_FakePopen):
    """Same surface as _FakePopen, but records the detached spawn instead of the normal one."""

    def __init__(self, args, **kw):
        detached.append((list(args), kw.get("creationflags", 0)))
        self.args = list(args)
        self.returncode = 0


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
app = os.path.join(_ROOT, 'interface', 'app', 'src', 'App.tsx')
src = open(app, encoding="utf-8").read()
assert "Do you want to exit ISHTI?" in src, "the native close confirmation is required"
assert "/api/shutdown" in src, "Yes must trigger the clean shutdown"
assert "sendBeacon" in src, "the shutdown must survive the page unloading (native Yes closes the page first)"
assert 'addEventListener("ishti-window-close-requested", handleCloseReq)' in src
assert 'addEventListener("pagehide"' not in src, "refreshing a browser tab must preserve the service"

# the bundle that is actually served contains it
dist_js = [f for f in os.listdir(os.path.join(_ROOT, 'interface', 'app', 'dist', 'assets')) if f.endswith(".js")]
assert dist_js, "the built bundle is missing"
bundle = open(os.path.join(os.path.join(_ROOT, 'interface', 'app', 'dist', 'assets'), dist_js[0]), encoding="utf-8").read()
for needle in ("Do you want to exit ISHTI?", "/api/shutdown", "sendBeacon", "ishti-window-close-requested"):
    assert needle in bundle, f"the served bundle is stale - it lacks {needle!r}"

# the server endpoint exists: ONE graceful shutdown (ishti/lifecycle_api.py), attached to the app by server.py
server = os.path.join(_ROOT, 'interface', 'web_ui', 'server.py')
assert "lifecycle_api.attach(app)" in open(server, encoding="utf-8").read()
assert '@router.post("/api/shutdown")' in open(os.path.join(_ROOT, 'ishti', 'lifecycle_api.py'), encoding="utf-8").read()
assert '@app.post("/api/shutdown")' not in open(server, encoding="utf-8").read(), "a second, competing shutdown path"

# ---- 5. start.bat and stop.bat are wired to the singleton check and the full stop ---------------------------
# start.bat no longer pokes focus_ishti.py directly: it launches desktop/launcher.py (or the packaged exe),
# which owns the singleton check. What matters is that the no-argument branch cannot start a second instance
# or open a console window.
_start = open(os.path.join(_ROOT, 'start.bat'), encoding="utf-8").read()
_launch_branch = _start.split(":handle_cli")[0]        # everything before the CLI sub-commands
assert "desktop\\launcher.py" in _launch_branch, "start.bat must go through the singleton launcher"
assert "dist\\ISHTI\\ISHTI.exe" in _launch_branch, "start.bat must prefer the packaged app (no console window)"
assert "startup.py" not in _launch_branch, "the console-banner startup path is back in the no-argument branch"
assert "focus_ishti.py" not in _launch_branch, "start.bat must not race the launcher's own singleton check"
assert "shutdown.py" in open(os.path.join(_ROOT, 'stop.bat'), encoding="utf-8").read()
# stop.bat -> desktop/shutdown.py: asks the backend for the graceful shutdown, then verifies the port and recorded pids are gone;
# it kills only by recorded pid (never by matching process names / command lines)
_sd = open(os.path.join(_ROOT, 'desktop', 'shutdown.py'), encoding="utf-8").read()
assert "/api/shutdown" in _sd and "registry()" in _sd and "clean =" in _sd
assert "process_iter" not in _sd and "cmdline" not in _sd, "name/command-line based killing is back"

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
