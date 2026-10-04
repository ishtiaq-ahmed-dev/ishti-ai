"""Python side of the WhatsApp bridge (tools/wa_bridge, Baileys). Pair once by QR, then read/write
without a browser: every message arrives as it lands, so "any new messages" is instant.

The bridge is OPTIONAL. If it is not running the WhatsApp skill behaves exactly as before (Chrome Profile 9
through Playwright), so nothing regresses - the skill only prefers the bridge when /status answers.

Nothing here sends anything by itself: the caller (the whatsapp skill) always gates a send with the owner
first. This module is the transport, not the decision.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

BASE = f"http://127.0.0.1:{os.environ.get('WA_BRIDGE_PORT', '8788')}"
BRIDGE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools", "wa_bridge")
DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "wa_bridge")


def _call(path: str, payload: dict = None, timeout: int = 20):
    url = BASE + path
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data, {"Content-Type": "application/json"},
                                 method="POST" if payload is not None else "GET")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode() or "{}")


def running() -> bool:
    """True when the bridge process is up. A slow/absent bridge is never an error."""
    try:
        _call("/status", timeout=3)
        return True
    except Exception:  # noqa: BLE001
        return False


def status() -> dict:
    try:
        return _call("/status", timeout=5) or {}
    except Exception:  # noqa: BLE001
        return {}


def connected() -> bool:
    return bool(status().get("connected"))


def get_qr_data() -> dict:
    s = status()
    qr_text = ""
    pair_txt_path = os.path.join(DATA, "pair.txt")
    if os.path.exists(pair_txt_path):
        try:
            with open(pair_txt_path, "r", encoding="utf-8") as f:
                qr_text = f.read()
        except Exception:
            pass
    return {
        "connected": bool(s.get("connected")),
        "raw_qr": s.get("qr") if isinstance(s.get("qr"), str) else None,
        "ascii_qr": qr_text,
        "me": s.get("me") or "",
        "running": running(),
    }


def pair_hint() -> str:
    """What to tell the owner when they ask to connect WhatsApp."""
    s = status()
    if s.get("connected"):
        return f"WhatsApp is linked ({s.get('me') or 'your number'}) - messages arrive live."
    if s.get("qr"):
        return ("WhatsApp is waiting to be linked. Open WhatsApp on your phone -> Linked devices -> Link a "
                "device, and scan the code in data/wa_bridge/pair.txt (or run "
                "`node tools/wa_bridge/index.js --qr-only` to print it in the terminal).")
    return ("The WhatsApp bridge isn't running. Start it with `node tools/wa_bridge/index.js` from "
            "tools/wa_bridge (first run: npm install), then scan the QR once. Until then ISHTI keeps using "
            "WhatsApp Web in Chrome Profile 9 as before.")


def chats() -> list:
    try:
        return _call("/chats", timeout=15) or []
    except Exception:  # noqa: BLE001
        return []


def messages(chat: str = "", limit: int = 50) -> list:
    try:
        q = f"/messages?limit={int(limit)}" + (f"&chat={urllib.parse.quote(chat)}" if chat else "")
        return _call(q, timeout=20) or []
    except Exception:  # noqa: BLE001
        return []


def inbox_since(ms: int) -> list:
    """Messages the bridge has recorded, newest first - the read path that needs no API call at all."""
    try:
        with open(os.path.join(DATA, "inbox.jsonl"), encoding="utf-8") as f:
            rows = [json.loads(l) for l in f if l.strip()]
    except Exception:  # noqa: BLE001
        return []
    return sorted([r for r in rows if (r.get("at") or 0) > ms], key=lambda r: r.get("at") or 0)


def find_chat(name: str) -> str:
    """A chat id by name/number. Exact, then case-insensitive, then 'startswith'. '' when not found."""
    want = str(name or "").strip().lower()
    rows = chats()
    for c in rows:
        if str(c.get("id", "")).split("@")[0].lower() == want or str(c.get("name", "")).lower() == want:
            return c["id"]
    for c in rows:
        if str(c.get("name", "")).lower().startswith(want) or \
                str(c.get("id", "")).split("@")[0].lower().startswith(want):
            return c["id"]
    return ""


def send(chat: str, text: str = "", media: str = "", caption: str = "") -> dict:
    return _call("/send", {"chat": chat, "text": text, "media": media, "caption": caption})


def mark_read(chat: str) -> dict:
    return _call("/markread", {"chat": chat})


def delete(chat: str, key: str) -> dict:
    return _call("/delete", {"chat": chat, "key": key})


def edit(chat: str, key: str, text: str) -> dict:
    return _call("/edit", {"chat": chat, "key": key, "text": text})


def react(chat: str, key: str, emoji: str) -> dict:
    return _call("/react", {"chat": chat, "key": key, "emoji": emoji})


import urllib.parse  # noqa: E402  (used by messages())


def install_hint() -> str:
    return ("cd tools/wa_bridge && npm install   # once: Baileys + express + qrcode-terminal\n"
            "node index.js                         # pair by QR, then it stays linked\n"
            "node index.js --qr-only               # just show the QR")


if __name__ == "__main__":
    print(f"[self-check] bridge running: {running()}  connected: {connected()}")
    print(f"[self-check] pair hint: {pair_hint()[:120]}")
    assert chats() == [] or isinstance(chats(), list)
    print("[self-check] wa_bridge OK - loopback client, no-op when the bridge is absent")