"""Global microphone control (Section 7): ON/OFF for the always-on wake-word listener.

Rules this module enforces:
  * Mic OFF  - no audio is captured or processed at all; wake word disabled; any running
               listener is stopped; the setting persists across restarts (default OFF on
               first run). The state is checked before the microphone is ever opened.
  * Mic ON   - the lightweight wake-word detector listens for "Hey ISHTI", "Hey Jarvis",
               "Hey my assistant"; full STT only starts after the trigger (unchanged).
  * Every load/unload is logged with a timestamp; the kill switch and shutdown set mic OFF.
  * Live Agent (/ws/live) is NOT the wake-word mic: it is an explicit session the owner
    opens themselves, and it ends when the socket ends. Still, `mic_off(reason)` is the
    single place that must close everything, so Live sessions are stopped too on mic-off.

The state file is data/mic_state.json; it is re-read on every set so external edits
(Settings page, another process) take effect without a restart.
"""
import json
import os
import re
import threading
import time

from ishti import CONFIG

STATE_PATH = os.path.join(os.path.dirname(CONFIG["memory"]["db_path"]), "mic_state.json")

_lock = threading.Lock()
_state = {"enabled": False, "changed_at": None, "changed_by": "default"}  # default OFF on first run
_listener_stop = None   # the wake listener's stop Event, registered by whoever started it
_live_stop = None       # the Live (/ws/live) session's stop Event, registered by the server


def _load():
    global _state
    try:
        if os.path.exists(STATE_PATH):
            with open(STATE_PATH, "r", encoding="utf-8") as f:
                saved = json.load(f)
            _state = {
                "enabled": bool(saved.get("enabled", False)),
                "changed_at": saved.get("changed_at"),
                "changed_by": saved.get("changed_by", "restored"),
            }
    except Exception as exc:
        print(f"[mic] could not read {STATE_PATH}: {exc} - staying OFF")


def _save():
    try:
        os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
        with open(STATE_PATH, "w", encoding="utf-8") as f:
            json.dump(_state, f, indent=2)
    except Exception as exc:
        print(f"[mic] could not save state: {exc}")


_load()


def is_enabled() -> bool:
    """True only when the owner turned global mic listening on."""
    with _lock:
        return bool(_state["enabled"])


def status() -> dict:
    with _lock:
        s = dict(_state)
    s.update(listener_running=bool(_listener_stop and not _listener_stop.is_set()),
             live_session_running=bool(_live_stop and not _live_stop.is_set()),
             wake_phrases=CONFIG["voice"].get("wake_phrases", []),
             config_wake_enabled=CONFIG["voice"].get("wake_enabled", False))
    return s


def _stop_listener():
    global _listener_stop
    with _lock:
        stop, _listener_stop = _listener_stop, None
    if stop and not stop.is_set():
        try:
            stop.set()
            print("[mic] wake-word listener stopped")
        except Exception as exc:
            print(f"[mic] could not stop listener: {exc}")


def _start_listener() -> bool:
    """(Re)start the wake-word listener if config allows it and nothing is running."""
    global _listener_stop
    if not is_enabled():
        return False
    if not CONFIG["voice"].get("wake_enabled", True):
        print("[mic] mic is ON but config voice.wake_enabled is false; nothing to start")
        return False
    with _lock:
        if _listener_stop and not _listener_stop.is_set():
            return True  # already running
    try:
        from ishti import wake

        def on_voice_command(text, on_token=None):
            """Mirror of the server's voice handler (it lives inside lifespan, not importable):
            the reply streams to spoken sentences and to the dashboard."""
            from ishti.dispatcher import handle as ishti_handle
            res = ishti_handle(text, channel="voice", on_token=on_token)
            try:
                from interface.web_ui.server import sync_event_callback
                sync_event_callback("assistant_message", {"user": text, "reply": res["final_answer"],
                                                          "sources": res.get("sources", []), "channel": "voice"})
            except Exception:
                pass
            return res["final_answer"]

        def on_event(kind, data):
            try:
                from interface.web_ui.server import sync_event_callback
                sync_event_callback(kind, data)
            except Exception:
                pass

        _listener_stop = wake.start(on_voice_command, on_event=on_event)
        print("[mic] wake-word listener started (wake phrases: "
              f"{', '.join(CONFIG['voice'].get('wake_phrases', []))})")
        return True
    except Exception as exc:
        print(f"[mic] could not start wake listener: {exc}")
        return False


def mic_on(by="voice") -> dict:
    """Enable global mic listening (wake word detector). Takes effect immediately."""
    global _state
    with _lock:
        _state.update(enabled=True, changed_at=time.time(), changed_by=by)
        _save()
    print(f"[mic] GLOBAL MIC ON (set by {by}) at {time.strftime('%Y-%m-%d %H:%M:%S')}")
    started = _start_listener()
    return {"enabled": True, "listener_started": started, **status()}


def mic_off(reason="owner command", by="voice", also_stop_live=False) -> dict:
    """Disable all mic capture now and persist OFF. The kill switch and shutdown call this."""
    global _state
    with _lock:
        _state.update(enabled=False, changed_at=time.time(), changed_by=by)
        _save()
    print(f"[mic] GLOBAL MIC OFF ({reason}, set by {by}) at {time.strftime('%Y-%m-%d %H:%M:%S')}")
    _stop_listener()
    if also_stop_live:
        _stop_live()
    return {"enabled": False, **status()}


def toggle(by="voice") -> dict:
    return mic_off("toggle", by) if is_enabled() else mic_on(by)


def register_listener(stop_event):
    """The server registers the wake listener it started so mic_off can stop it."""
    global _listener_stop
    with _lock:
        _listener_stop = stop_event


def register_live(stop_event):
    """The server registers the /ws/live session's stop Event so mic-off closes it too."""
    global _live_stop
    with _lock:
        _live_stop = stop_event


def _stop_live():
    global _live_stop
    with _lock:
        stop, _live_stop = _live_stop, None
    if stop and not stop.is_set():
        try:
            stop.set()
            print("[mic] Live voice session stopped")
        except Exception as exc:
            print(f"[mic] could not stop live session: {exc}")


def apply_on_startup() -> dict:
    """Called once when the server starts: nothing runs unless the persisted setting says ON."""
    _load()  # re-read so an external edit during shutdown wins
    if is_enabled():
        started = _start_listener()
        return {"enabled": True, "listener_started": started}
    print("[mic] global mic OFF (default): no audio is captured or processed")
    return {"enabled": False, "listener_started": False}


# ---- voice commands (routed by dispatcher.match to this module's ops) ------------------------
COMMANDS = (
    (re.compile(r"\b(?:turn\s+off|disable|stop|mute|close)\b.*\b(?:global\s+)?mic(?:rophone)?\b|\b"
                r"(?:global\s+)?mic(?:rophone)?\b.*\b(?:off|mute)\b|\bstop\s+listening\b"), "off"),
    (re.compile(r"\b(?:turn\s+on|enable|open|start|unmute)\b.*\b(?:global\s+)?mic(?:rophone)?\b|\b"
                r"(?:global\s+)?mic(?:rophone)?\b.*\b(?:on|unmute)\b|\bstart\s+listening\b"), "on"),
    (r"\b(?:auto\s+language|switch\s+to\s+(?:urdu|pashto|english)|speak\s+(?:urdu|pashto|english)|"
     r"speak\s+english)\b", None),  # language commands belong to voice.py; not matched here
)

_WAKE_TOGGLE = ("hey ishti", "hey jarvis", "hey my assistant")


def handle_command(text: str) -> str | None:
    """Voice/hotkey entry: returns the reply, or None when the text is not a mic command."""
    t = (text or "").strip().lower()
    for rx, op in COMMANDS[:2]:
        if rx.search(t):
            res = mic_on(by="voice") if op == "on" else mic_off("voice command", "voice",
                                                                also_stop_live=(op == "off"))
            if op == "on":
                return ("Microphone on. I'm listening for the wake word now - "
                        f"{' / '.join(_WAKE_TOGGLE)} - and full speech starts only after it.")
            return ("Microphone off. Nothing is being captured or processed; the wake word is "
                    "disabled. Turn me back on with 'microphone on' or the dashboard toggle.")
    return None


if __name__ == "__main__":
    import tempfile
    STATE_PATH = os.path.join(tempfile.mkdtemp(), "mic_state.json")
    _load()
    assert is_enabled() is False, "default must be OFF on first run"
    # persistence: write ON, reload, still ON
    mic_on(by="self-check")
    assert is_enabled() is True
    _load()
    assert is_enabled() is True, "state must persist"
    # voice commands
    assert "off" in handle_command("turn off the microphone").lower()
    assert is_enabled() is False
    assert "on" in handle_command("microphone on").lower()
    assert is_enabled() is True
    assert handle_command("what's the weather") is None  # not a mic command
    assert handle_command("switch to urdu") is None  # belongs to language control
    # mic_off stops things registered
    import threading
    stop = threading.Event()
    register_listener(stop)
    register_live(threading.Event())
    mic_off("self-check", also_stop_live=True)
    assert stop.is_set() and is_enabled() is False
    # and the file survived
    _load()
    assert is_enabled() is False
    print("[self-check] mic_control OK - default OFF, persists, voice commands, stop hooks fire")
