"""Launcher window regression (LAUNCH-1..3): opens big, remembers itself, maximize/fullscreen work, content fills it.

Offline unit tests run in the default suite. The live test starts the real launcher (pywebview + backend) and needs a
free port 8888, a desktop session and ~60 s:  venv\\Scripts\\python -m pytest tests/test_launcher_window.py -m e2e -s
The 2026-10-02 root causes it guards: a hardcoded 1280x820 window (62 % of the owner's work area, no memory), and the
js_api objects that held window objects in PUBLIC attributes (pywebview walked the native form off the UI thread and
froze the splash for good).
"""
import inspect
import json
import os
import socket
import subprocess
import sys
import time
import types
import urllib.request

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from desktop import window_state  # noqa: E402

AREA = (0, 0, 2048, 1104)


def test_first_launch_is_at_least_80_percent_of_work_area():
    g = window_state.geometry(AREA)
    assert g["width"] >= 0.8 * AREA[2] and g["height"] >= 0.8 * AREA[3] and not g["maximized"]
    assert g["x"] >= 0 and g["x"] + g["width"] <= AREA[2] and g["y"] + g["height"] <= AREA[3]


def test_state_round_trip_and_clamp(tmp_path):
    f = str(tmp_path / "w.json")
    window_state.save({"width": 1400, "height": 900, "x": 50, "y": 40, "maximized": True}, f)
    g = window_state.geometry(AREA, window_state.load(f))
    assert (g["width"], g["height"], g["x"], g["y"], g["maximized"]) == (1400, 900, 50, 40, True)
    # the monitor the window was on is gone: it must come back reachable and no bigger than the screen
    off = window_state.geometry(AREA, {"width": 5000, "height": 5000, "x": 7000, "y": 7000})
    assert off["width"] <= AREA[2] and off["height"] <= AREA[3] and off["x"] < AREA[2] and off["y"] < AREA[3]
    assert window_state.load(str(tmp_path / "missing.json")) == {}
    (tmp_path / "bad.json").write_text("{not json")
    assert window_state.load(str(tmp_path / "bad.json")) == {}


def test_js_api_objects_hold_no_public_non_callable_attributes():
    """pywebview recurses into public attributes: a window stored there wedged the UI thread (the frozen splash)."""
    from desktop import app_window
    api = app_window.MainApi(win=object())
    for name in dir(api):
        if not name.startswith("_"):
            assert callable(getattr(api, name)), f"public attribute {name!r} would be walked by pywebview"
    assert {"toggle_fullscreen", "toggle_maximize", "minimize_to_tray", "window_info"} <= set(dir(api))


def test_splash_shows_progress_from_the_bridge_not_a_cross_origin_fetch():
    """The splash runs from file:// and the backend turns it away: the fetch is cross-origin (no CORS headers)
    and the server's own origin check answers 403 "foreign origin" to a null origin - /ws/events too. Sitting at
    the 0 % it starts at until the window was replaced is exactly what the owner saw (2026-10-13)."""
    src = open(os.path.join(ROOT, "desktop", "splash.html"), encoding="utf-8").read()
    assert "api.boot_state()" in src, "the splash must ask the Python bridge for the boot state"
    assert 'fetch("http://127.0.0.1' not in src and "ws://127.0.0.1" not in src, "both are refused for a file:// page"


def _response(body):
    return types.SimpleNamespace(status_code=200, ok=True, json=lambda: body)


def test_boot_state_is_the_splashes_progress_source(monkeypatch):
    from desktop import app_window
    asked = []
    snapshot = {"ready": False, "stage": "planner_warm", "progress": 71, "error": None,
                "stages": [{"id": "planner_warm", "label": "Warming up the brain", "done": False}]}
    monkeypatch.setattr("requests.get", lambda url, timeout=None: asked.append(url) or _response(snapshot))
    api = app_window.MainApi()
    assert api.boot_state() == snapshot and asked[-1].endswith("/api/ready")
    monkeypatch.setattr("requests.get", lambda url, timeout=None: (_ for _ in ()).throw(ConnectionError("refused")))
    assert api.boot_state() == {}          # not answering yet: the splash keeps "Initializing..."


def test_open_dashboard_waits_for_the_boot_and_hands_over_only_when_it_never_ends(monkeypatch):
    """It used to switch ~1 s in, before a single stage finished; now the splash drives the handover and this
    thread only covers what the splash cannot: a boot that never reports ready or an error inside the budget,
    and a window whose JS bridge never arrived."""
    from desktop import app_window

    def world(ready, error=None):
        def get(url, timeout=None):
            if url.endswith("/api/health"):
                return _response({"ok": True})
            return _response({"ready": ready, "error": error})
        return get

    splash_url = "file:///D:/ISHTI/desktop/splash.html"

    def run(ready, error=None, page_url=splash_url, wait_s=0.2):
        opened, page = [], {"now": page_url}

        def load(url):
            opened.append(url)
            page["now"] = url

        win = types.SimpleNamespace(load_url=load, get_current_url=lambda: page["now"])
        monkeypatch.setattr("requests.get", world(ready, error))
        app_window._open_dashboard(win, wait_s=wait_s)
        return opened

    assert run(ready=True, page_url=app_window.URL, wait_s=3.0) == [], \
        "the splash handed over at 100 %; navigating again would reload it underneath the owner"
    assert run(ready=True, wait_s=3.0) == [app_window.URL], \
        "a JS bridge that never arrived must not strand the owner on a splash"
    assert run(ready=False, error="The AI engine didn't start.", wait_s=1.0) == [], \
        "a failed boot stays on the splash, whose error card offers retry / view log"
    assert run(ready=False) == [app_window.URL], "a boot stuck past the budget must show where it is"


def test_is_running_does_not_pay_the_closed_port_penalty():
    """A closed loopback port takes ~2 s to refuse here; the launcher used to probe it three times (4 s per launch)."""
    from desktop.focus_ishti import is_running
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        free = s.getsockname()[1]
    t = time.time()
    assert is_running(free) is False
    assert time.time() - t < 1.0
    with socket.socket() as srv:  # something else holding the port is not ISHTI
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        assert is_running(srv.getsockname()[1]) is False
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class Health(BaseHTTPRequestHandler):
        def do_GET(self):
            body = b'{"ok": true, "state": "ready"}' if self.path == "/api/health" else b"{}"
            self.send_response(200)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass
    server = HTTPServer(("127.0.0.1", 0), Health)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        assert is_running(server.server_address[1]) is True
    finally:
        server.shutdown()


def test_launcher_registers_shell_off_the_critical_path():
    from desktop import launcher
    src = inspect.getsource(launcher.main)
    assert "_register_shell" in src and src.index("_register_shell") < src.index("run_app_window")


# ------------------------------------------------------------------ live (real window)
def _cdp(port, expr):
    import websocket
    tabs = json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/json", timeout=3))
    tab = next(t for t in tabs if t.get("type") == "page")
    ws = websocket.create_connection(tab["webSocketDebuggerUrl"], timeout=5, suppress_origin=True)
    ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate", "params": {"expression": expr, "returnByValue": True}}))
    while True:
        m = json.loads(ws.recv())
        if m.get("id") == 1:
            ws.close()
            return m["result"]["result"].get("value")


def _wait(pred, secs, what):
    end = time.time() + secs
    while time.time() < end:
        try:
            v = pred()
            if v:
                return v
        except Exception:
            pass
        time.sleep(0.2)
    raise AssertionError(f"timed out waiting for {what}")


@pytest.mark.e2e
def test_live_window_size_maximize_fullscreen_persistence(tmp_path):
    import win32con
    import win32gui
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    import launch_probe as P

    if P.listening():
        pytest.skip("ISHTI already running on 8888")
    state = str(tmp_path / "window.json")
    env = {**os.environ, "ISHTI_WINDOW_STATE": state, "ISHTI_DEBUG_PORT": "9333"}
    pw = os.path.join(ROOT, "venv", "Scripts", "pythonw.exe")

    def launch():
        subprocess.Popen([pw, os.path.join(ROOT, "desktop", "launcher.py")], cwd=ROOT, env=env)
        w = _wait(P.main_window, 40, "the ISHTI window")
        _wait(lambda: _cdp(9333, "location.href").startswith("http://127.0.0.1:8888"), 40, "the dashboard")
        return w

    def stop():
        try:
            urllib.request.urlopen(urllib.request.Request("http://127.0.0.1:8888/api/shutdown", method="POST", data=b""), timeout=3)
        except Exception:
            pass
        _wait(lambda: not P.listening() and not P.main_window(), 60, "shutdown")
        time.sleep(1)

    def fills():
        """The page's viewport equals the window's client area (dashboard CSS fills whatever size the window has)."""
        w = P.main_window()["hwnd"]
        l, t, r, b = win32gui.GetClientRect(w)
        dpr = _cdp(9333, "devicePixelRatio")
        inner = _cdp(9333, "[innerWidth, innerHeight, document.documentElement.scrollWidth, document.documentElement.scrollHeight]")
        assert abs(inner[0] * dpr - (r - l)) <= 4 and abs(inner[1] * dpr - (b - t)) <= 4, (inner, dpr, (r - l, b - t))
        assert inner[2] <= inner[0] + 1 and inner[3] <= inner[1] + 1, f"page overflows the window: {inner}"

    wa = P.work_area()
    try:
        w = launch()
        assert w["w"] >= 0.8 * wa[0] and w["h"] >= 0.8 * wa[1], (w, wa)       # first launch: >= 80 % of the work area
        fills()
        hwnd = w["hwnd"]
        win32gui.ShowWindow(hwnd, win32con.SW_MAXIMIZE)                      # the native maximize button's message
        _wait(lambda: P.main_window()["maximized"], 5, "maximize")
        time.sleep(0.8)
        fills()
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        win32gui.MoveWindow(hwnd, 200, 120, 1500, 900, True)                 # a user resize, then check the content follows
        time.sleep(1.0)
        fills()
        win32gui.ShowWindow(hwnd, win32con.SW_MAXIMIZE)                      # leave it maximized: that is what must come back
        time.sleep(1.5)                                                      # > the 1 s debounce
        stop()
        saved = json.load(open(state))
        assert saved["maximized"] is True and saved["width"] >= 900, saved
        launch()                                                             # second launch restores it
        assert P.main_window()["maximized"], "the window did not come back maximized"
        fills()
    finally:
        stop()
