"""ISHTI system-tray icon (Section 7): global mic ON/OFF at a glance, plus Open/Stop.

Runs alongside the desktop window (pystray + PIL, both already in the venv for other tools).
The mic item is the unmistakable indicator: the icon text and tooltip always say
"ISHTI - mic ON (listening)" or "ISHTI - mic OFF", and turning it off here closes the
wake listener and Live sessions immediately (same mic_control.mic_off path as the
dashboard, voice commands, the kill switch and shutdown).
"""
import os
import sys
import threading

ROOT = r"D:\ISHTI"
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _notify(title, msg):
    try:
        from plyer import notification
        notification.notify(title=title, message=msg, app_name="ISHTI", timeout=4)
    except Exception:
        pass


def _set_mic(enabled: bool, icon):
    from ishti import mic_control
    res = mic_control.mic_on(by="tray") if enabled else mic_control.mic_off("tray toggle", by="tray")
    _notify("ISHTI microphone " + ("ON" if res.get("enabled") else "OFF"),
            "Wake-word listening is active." if res.get("enabled")
            else "No audio is being captured or processed.")
    _refresh(icon, res)


def _refresh(icon, res=None):
    try:
        from ishti import mic_control
        res = res or mic_control.status()
        enabled = bool(res.get("enabled"))
        if icon is not None:
            icon.title = f"ISHTI - mic {'ON (listening)' if enabled else 'OFF'}"
    except Exception:
        pass


def _open_dashboard():
    import webbrowser
    webbrowser.open("http://127.0.0.1:8888")


def _stop_all():
    from desktop.shutdown import shutdown
    threading.Thread(target=shutdown, daemon=True).start()


def run_tray():
    import pystray
    from PIL import Image, ImageDraw

    def _icon_image(on: bool, live: bool = False):
        # green ring = mic live, grey ring = mic off; red dot = live voice session
        img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        ring = (46, 204, 113, 255) if on else (120, 128, 140, 255)
        d.ellipse([6, 6, 58, 58], outline=ring, width=6)
        d.ellipse([22, 22, 42, 42], fill=ring)
        if live:
            d.ellipse([46, 2, 62, 18], fill=(231, 76, 60, 255))
        return img

    state = {"on": False, "live": False}

    def menu_mic_toggle(icon, item):
        _set_mic(not state["on"], icon)

    def menu_mic_on(icon, item):
        _set_mic(True, icon)

    def menu_mic_off(icon, item):
        _set_mic(False, icon)

    def menu_open(icon, item):
        _open_dashboard()

    def menu_quit(icon, item):
        _stop_all()
        icon.stop()

    icon = pystray.Icon("ISHTI", _icon_image(state["on"]), "ISHTI - mic OFF",
                        menu=pystray.Menu(
                            pystray.MenuItem("Microphone ON", menu_mic_on),
                            pystray.MenuItem("Microphone OFF", menu_mic_off),
                            pystray.MenuItem("Toggle microphone", menu_mic_toggle),
                            pystray.Menu.SEPARATOR,
                            pystray.MenuItem("Open dashboard", menu_open),
                            pystray.MenuItem("Stop ISHTI", menu_quit),
                        ))

    def poll_state():
        import time
        from ishti import mic_control
        while True:
            try:
                s = mic_control.status()
                new = (bool(s.get("enabled")), bool(s.get("live_session_running")))
                if new != (state["on"], state["live"]):
                    state["on"], state["live"] = new
                    icon.icon = _icon_image(*new)
                    icon.title = f"ISHTI - mic {'ON (listening)' if new[0] else 'OFF'}" + \
                                 (" - live" if new[1] else "")
            except Exception:
                pass
            time.sleep(2)

    threading.Thread(target=poll_state, daemon=True).start()

    # Exit when the backend stays gone (external POST /api/shutdown, crash): a tray icon around a
    # dead ISHTI is an orphan (found 2026-09-29).
    try:
        from desktop import backend_watch
        backend_watch.start(lambda: icon.stop())
    except Exception:
        pass

    icon.run()


if __name__ == "__main__":
    run_tray()
