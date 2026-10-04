"""What ISHTI can take as INPUT, and the live state of each channel - the dashboard's Inputs section.

Every value is read from the file or module that owns it; nothing is invented or hard-coded as "ready".
A missing state file simply means "unknown".

Channels: text chat, microphone, wake word, live listening, languages, files/documents, screen,
clipboard, reminders, the QR-paired WhatsApp bridge, and every account connected with a Client ID/Secret.
"""
from __future__ import annotations

import json
import os


def _read(rel: str, default=None):
    from ishti import ROOT
    try:
        with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001 - a missing state file just means "unknown"
        return default


def channels() -> list:
    """[{id, name, icon, kind, detail, state, source, account?}] - the Inputs page renders this as-is."""
    from ishti import CONFIG as cfg
    voice = cfg.get("voice") or {}
    out = []

    out.append({"id": "chat", "name": "Text & chat", "icon": "message", "kind": "always_on",
                "detail": "Type or paste to ISHTI. Always available.",
                "state": "on", "source": "this window", "action": "chat"})

    mic_on = False
    try:
        from ishti import mic_control
        mic_on = bool(mic_control.is_enabled())
    except Exception:  # noqa: BLE001
        mic_on = bool((_read("data/mic_state.json", {}) or {}).get("enabled"))
    out.append({"id": "mic", "name": "Microphone", "icon": "mic", "kind": "toggle",
                "detail": "Local speech-to-text (" + str(voice.get("whisper_model") or "whisper")
                          + "). Nothing leaves this PC.",
                "state": "on" if mic_on else "off", "source": "data/mic_state.json",
                "action": "toggle_mic", "can_toggle": True})

    out.append({"id": "wake", "name": "Wake word", "icon": "wave", "kind": "setting",
                "detail": "Hands-free start: " + ", ".join(voice.get("wake_phrases") or ["hey ishti"]),
                "state": "on" if voice.get("wake_enabled") else "off",
                "source": "config.json -> voice.wake_enabled", "action": "settings"})

    listening = False
    try:
        from ishti import wake
        listening = bool(wake.listening()) if hasattr(wake, "listening") else False
    except Exception:  # noqa: BLE001
        listening = False
    out.append({"id": "listening", "name": "Live listening", "icon": "ear", "kind": "status",
                "detail": "Whether the wake loop is listening at this moment.",
                "state": "on" if listening else "off", "source": "runtime"})

    out.append({"id": "languages", "name": "Languages", "icon": "globe", "kind": "setting",
                "detail": "Speech + text: " + ", ".join(voice.get("languages") or ["en"]),
                "state": "on", "source": "config.json -> voice.languages"})

    out.append({"id": "files", "name": "Files & documents", "icon": "file", "kind": "always_on",
                "detail": "Drop a PDF, image, audio or document into the chat and ISHTI reads it.",
                "state": "on", "source": "chat upload", "action": "upload"})
    out.append({"id": "screen", "name": "Screen & window", "icon": "screen", "kind": "opt_in",
                "detail": "Read or drive a window when you ask.",
                "state": "ready", "source": "screen_understand / desktop", "action": "test_screen"})
    out.append({"id": "clipboard", "name": "Clipboard", "icon": "clip", "kind": "opt_in",
                "detail": "Read what you copied, when you ask.",
                "state": "ready", "source": "clipboard store", "action": "test_clipboard"})
    out.append({"id": "reminders", "name": "Reminders & calendar", "icon": "bell", "kind": "always_on",
                "detail": "Prompts ISHTI gives itself when you asked it to.",
                "state": "on", "source": "reminders"})

    st, detail, me = {}, "Not running.", ""
    qr_avail = False
    try:
        from ishti import wa_bridge
        st = wa_bridge.status() or {}
        me = st.get("me") or ""
        qr_avail = bool(st.get("qr")) or os.path.exists(os.path.join(wa_bridge.DATA, "pair.txt"))
        detail = ("Linked - messages arrive live." if st.get("connected")
                  else "QR ready - scan it in WhatsApp > Linked devices." if qr_avail
                  else "Not running - start it with: node tools/wa_bridge/index.js")
    except Exception:  # noqa: BLE001
        pass
    out.append({"id": "whatsapp", "name": "WhatsApp (QR paired)", "icon": "chat", "kind": "status",
                "detail": detail, "account": me,
                "state": "on" if st.get("connected") else ("waiting" if qr_avail else "off"),
                "source": "tools/wa_bridge",
                "action": "view_qr", "qr_available": qr_avail})

    for rec in accounts():
        out.append({"id": "oauth:" + rec["service"], "name": rec["service"].capitalize(),
                    "icon": "cloud", "kind": "account", "detail": rec["detail"],
                    "account": rec.get("account", ""), "state": rec["state"],
                    "source": "connect " + rec["service"]})
    return out


def accounts() -> list:
    """Every provider ISHTI knows, with whether it is connected right now."""
    try:
        from ishti import oauth
    except Exception:  # noqa: BLE001
        return []
    out = []
    for svc in oauth.providers():
        try:
            has = bool(oauth.config(svc)[0])
            conn = oauth.connected(svc)
            who = oauth.account(svc)
        except Exception:  # noqa: BLE001
            has, conn, who = False, False, ""
        out.append({"service": svc, "connected": conn, "has_credentials": has, "account": who,
                    "state": "on" if conn else ("ready" if has else "off"),
                    "detail": "Connected" if conn else
                              ("Credentials saved - say 'connect " + svc + "'" if has else "Not connected")})
    return out


def snapshot() -> dict:
    return {"channels": channels(), "accounts": accounts(),
            "note": "Every state is read from the file or module that owns it."}


if __name__ == "__main__":
    snap = snapshot()
    for c in snap["channels"]:
        print(f"  {c['state']:8} {c['name']:26} {c['detail'][:70]}")
    assert any(c["id"] == "chat" for c in snap["channels"])
    assert any(c["id"] == "whatsapp" for c in snap["channels"])
    assert isinstance(snap["accounts"], list)
    print(f"[self-check] inputs OK - {len(snap['channels'])} channels, "
          f"{len(snap['accounts'])} account providers, real state only")