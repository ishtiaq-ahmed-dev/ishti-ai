"""Authenticated local transport. Draft/recipient approval belongs to the skill.

No history is imported automatically. An empty live store does not prove an empty
WhatsApp conversation. Auth keys and staged media are encrypted at rest.
"""
from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parent.parent
BRIDGE_DIR = str(ROOT / "tools" / "wa_bridge")
DATA = str(ROOT / "data" / "wa_bridge")
BASE = f"http://127.0.0.1:{int(os.environ.get('WA_BRIDGE_PORT', '8788'))}"
MAX_MEDIA = 50 * 1024 * 1024
_lock = threading.RLock()
_child = None


class BridgeError(RuntimeError):
    pass


def _test_mode():
    return os.environ.get("ISHTI_TEST_MODE") == "1"


def _protect(data: bytes) -> bytes:
    if os.name != "nt":
        raise BridgeError("The bridge currently requires Windows protected credential storage")
    import win32crypt
    return win32crypt.CryptProtectData(data, "ISHTI WhatsApp bridge", None, None, None, 0)


def _unprotect(data: bytes) -> bytes:
    import win32crypt
    return win32crypt.CryptUnprotectData(data, None, None, None, 0)[1]


def _credentials(create=False) -> dict:
    target = Path(DATA) / "bootstrap.dpapi"
    with _lock:
        if target.exists():
            try:
                value = json.loads(_unprotect(target.read_bytes()))
                if len(base64.b64decode(value["key"])) != 32 or len(value["token"]) < 32:
                    raise ValueError("Invalid key")
                return value
            except Exception as exc:
                raise BridgeError("Bridge credentials cannot be decrypted; existing session was preserved") from exc
        if not create:
            raise BridgeError("Bridge is not initialized")
        if _test_mode():
            raise BridgeError("Tests must inject scratch credentials")
        if any((Path(DATA) / "encrypted_state").glob("*.enc")):
            raise BridgeError("Bridge credential key is missing; existing encrypted session was preserved")
        value = {"key": base64.b64encode(secrets.token_bytes(32)).decode(), "token": secrets.token_urlsafe(32)}
        encrypted = _protect(json.dumps(value).encode())
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix("." + uuid.uuid4().hex + ".tmp")
        try:
            with tmp.open("xb") as stream:
                stream.write(encrypted)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(tmp, target)  # atomic, exclusive publication after complete write
            except FileExistsError:
                return _credentials(False)
        finally:
            tmp.unlink(missing_ok=True)
        return value


def _request(path, payload=None):
    data = json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None
    return urllib.request.Request(BASE + path, data=data,
        headers={"Authorization": "Bearer " + _credentials()["token"], "Content-Type": "application/json"},
        method="POST" if payload is not None else "GET")


def _open(request, timeout=20):
    # Do not send the local bearer token to a configured system proxy or redirect.
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *_args, **_kwargs):
            return None
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect()).open(request, timeout=timeout)


def _call(path: str, payload=None, timeout=20):
    try:
        with _open(_request(path, payload), timeout) as response:
            raw = response.read(2 * 1024 * 1024 + 1)
            if len(raw) > 2 * 1024 * 1024:
                raise BridgeError("Bridge response exceeds limit")
            return json.loads(raw)
    except urllib.error.HTTPError as exc:
        raise BridgeError(f"Bridge rejected this operation (HTTP {exc.code})") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise BridgeError("Bridge unavailable or outcome uncertain; sending was not retried") from exc


def status() -> dict:
    if _test_mode():
        return {"connected": False, "running": False}
    try:
        result = _call("/status", timeout=3)
        return {**result, "running": True}
    except Exception:
        return {"connected": False, "running": False}


def running() -> bool:
    return bool(status().get("running"))


def connected() -> bool:
    return bool(status().get("connected"))


def start() -> dict:
    global _child
    if _test_mode():
        raise BridgeError("Live bridge launch disabled in test mode")
    with _lock:
        state = status()
        if state.get("running"):
            return state
        if _child is not None and _child.poll() is None:
            return {"running": True, "connected": False, "starting": True}
        bootstrap = _credentials(create=True)
        node = os.environ.get("ISHTI_NODE_EXE") or shutil.which("node")
        if not node or not Path(node).is_file():
            raise BridgeError("Node.js runtime is unavailable")
        log_path = Path(DATA) / "bridge.log"
        with log_path.open("ab") as log:
            _child = subprocess.Popen([node, str(Path(BRIDGE_DIR) / "index.js")], cwd=BRIDGE_DIR,
                stdin=subprocess.PIPE, stdout=log, stderr=log,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        _child.stdin.write((json.dumps(bootstrap) + "\n").encode())
        _child.stdin.close()
        return {"running": True, "connected": False, "starting": True}


def get_qr_data() -> dict:
    state = status()
    image = None
    matrix = state.get("qr_matrix")
    if not state.get("connected") and isinstance(matrix, list) and 20 <= len(matrix) <= 200:
        import io
        from PIL import Image, ImageDraw
        size = len(matrix)
        if any(not isinstance(row, list) or len(row) != size for row in matrix):
            raise BridgeError("Invalid QR matrix")
        picture = Image.new("RGB", ((size + 8) * 6, (size + 8) * 6), "white")
        draw = ImageDraw.Draw(picture)
        for y, row in enumerate(matrix):
            for x, dark in enumerate(row):
                if dark:
                    draw.rectangle(((x + 4) * 6, (y + 4) * 6, (x + 5) * 6 - 1, (y + 5) * 6 - 1), fill="black")
        buffer = io.BytesIO()
        picture.save(buffer, format="PNG")
        image = "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()
    return {"connected": bool(state.get("connected")), "running": bool(state.get("running")),
        "qr_image": image, "raw_qr": None, "ascii_qr": "", "me": state.get("me") or "",
        "lastError": state.get("lastError"), "historyScope": state.get("historyScope")}


def pair_hint() -> str:
    state = status()
    if state.get("connected"):
        return "WhatsApp is linked. I can read messages observed since the bridge started."
    return "Open Settings → Inputs → WhatsApp → Link to start the bridge and scan its QR with Linked devices on your phone."


def install_hint() -> str:
    return "The local bridge uses your linked WhatsApp account. Older conversation history is available through WhatsApp Web."


def chats() -> list:
    return _call("/chats")


def messages(chat: str = "", limit: int = 50) -> list:
    query = urllib.parse.urlencode({"limit": max(1, min(200, int(limit))), **({"chat": chat} if chat else {})})
    return _call("/messages?" + query)


def inbox_since(ms: int) -> list:
    return [row for row in messages(limit=200) if row.get("observedAt", 0) > ms]


def find_chat(name: str) -> str:
    want = str(name or "").strip().casefold()
    if not want:
        return ""
    number = re.sub(r"[+ ()-]", "", want)
    if re.fullmatch(r"\d{6,15}", number):
        return number + "@s.whatsapp.net"
    matched = {row["id"] for row in chats() if want in {str(row.get("id", "")).casefold(), str(row.get("name", "")).strip().casefold()}}
    return next(iter(matched)) if len(matched) == 1 else ""


def _stage_media(media: str, kind: str, expected_hash: str | None):
    path = Path(media)
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_MEDIA:
        raise BridgeError("Attachment is unreadable or exceeds 50 MB")
    with path.open("rb") as stream:
        data = stream.read(MAX_MEDIA + 1)
    if not data or len(data) > MAX_MEDIA:
        raise BridgeError("Empty or oversized attachment")
    digest = hashlib.sha256(data).hexdigest()
    if expected_hash is not None and digest != expected_hash:
        raise BridgeError("Attachment changed after approval")
    suffix = path.suffix.casefold()
    inferred = "image" if suffix in {".png", ".jpg", ".jpeg", ".webp", ".gif"} else "video" if suffix in {".mp4", ".webm", ".mov"} else "audio" if suffix in {".wav", ".mp3", ".ogg", ".opus", ".m4a", ".aac"} else "document"
    kind = kind or inferred
    if kind == "voice" and (not data.startswith(b"OggS") or b"OpusHead" not in data[:4096]):
        raise BridgeError("Native voice notes require Ogg Opus; conversion failed")
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    key = base64.b64decode(_credentials()["key"])
    name = digest + ".bin"
    nonce = secrets.token_bytes(12)
    encrypted = AESGCM(key).encrypt(nonce, data, name.encode())
    # Node's format stores the tag before ciphertext.
    target = Path(DATA) / "encrypted_outbox" / (hashlib.sha256(name.encode()).hexdigest() + ".enc")
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix("." + uuid.uuid4().hex + ".tmp")
    try:
        tmp.write_bytes(b"IWA1" + nonce + encrypted[-16:] + encrypted[:-16])
        os.replace(tmp, target)
    finally:
        tmp.unlink(missing_ok=True)
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return {"file": name, "sha256": digest, "kind": kind, "mimetype": mime, "name": path.name}, target


def send(chat: str, text: str = "", media: str = "", caption: str = "", *, action_id: str,
         media_kind: str = "", expected_hash: str | None = None) -> dict:
    if _test_mode():
        raise BridgeError("Live sending disabled in test mode")
    payload = {"chat": chat, "text": text, "caption": caption, "actionId": action_id}
    if media:
        payload["media"], _target = _stage_media(media, media_kind, expected_hash)
    result = _call("/send", payload, timeout=30)
    if media:
        _target.unlink(missing_ok=True)
    # Read-only polling never repeats the send, including on uncertain outcomes.
    if result.get("id") and result.get("state") == "submitted":
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline and result.get("delivery") not in {"server_ack", "delivered", "read"}:
            time.sleep(0.3)
            result = _call("/action?" + urllib.parse.urlencode({"id": action_id}), timeout=3)
    return result


def mark_read(chat: str) -> dict:
    if _test_mode():
        raise BridgeError("Live marking read disabled in test mode")
    return _call("/markread", {"chat": chat})


def _read_worker(kind, data) -> dict:
    """Separate process with network disabled, memory limit and a hard deadline."""
    environment = {key: value for key, value in os.environ.items() if key.upper() in {
        "PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "LOCALAPPDATA", "APPDATA", "USERPROFILE", "PYTHONUTF8"}}
    environment["PYTHONUTF8"] = "1"
    process = subprocess.Popen([sys.executable, "-m", "ishti.wa_media_reader"], cwd=str(ROOT), env=environment,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    job = None
    try:
        if os.name == "nt":
            import win32job
            job = win32job.CreateJobObject(None, "")
            limits = win32job.QueryInformationJobObject(job, win32job.JobObjectExtendedLimitInformation)
            limits["BasicLimitInformation"]["LimitFlags"] = win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE | win32job.JOB_OBJECT_LIMIT_PROCESS_MEMORY
            limits["ProcessMemoryLimit"] = 1536 * 1024 * 1024
            win32job.SetInformationJobObject(job, win32job.JobObjectExtendedLimitInformation, limits)
            win32job.AssignProcessToJobObject(job, int(process._handle))
        payload = json.dumps({"kind": kind, "bytes": base64.b64encode(data).decode()}).encode()
        output, _stderr = process.communicate(payload, timeout=45)
        if process.returncode or len(output) > 256 * 1024:
            raise BridgeError("Media reader failed or exceeded its limits")
        result = json.loads(output)
        if result.get("error"):
            raise BridgeError(result["error"])
        return result
    except subprocess.TimeoutExpired as exc:
        raise BridgeError("Media reader exceeded its 45-second deadline") from exc
    except BridgeError:
        raise
    except Exception as exc:
        raise BridgeError("Media reader could not start within its isolation limits") from exc
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)
        if job is not None:
            job.Close()


def read_media(chat: str, message_id: str) -> dict:
    rows = messages(chat, 200)
    row = next((item for item in rows if item.get("chat") == chat and item.get("id") == message_id), None)
    if not row or row.get("restricted") or row.get("kind") not in {"image", "document"}:
        raise BridgeError("An exact retained image or PDF message is required")
    with _open(_request("/media", {"chat": chat, "id": message_id}), timeout=30) as response:
        data = response.read(MAX_MEDIA + 1)
        expected = response.headers.get("X-Content-SHA256")
    if not data or len(data) > MAX_MEDIA or hashlib.sha256(data).hexdigest() != expected:
        raise BridgeError("Incoming media is oversized or its integrity could not be verified")
    result = _read_worker(row["kind"], data)
    return {**result, "source": {"chat": chat, "messageId": message_id, "sha256": expected,
        "kind": row["kind"], "fromMe": row.get("fromMe"), "timestamp": row.get("ts")}, "trust": "untrusted_message_content"}


def delete(*_args, **_kwargs):
    return {"ok": False, "error": "Deferred in NEEDS; no mutation performed"}


edit = react = delete


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", action="store_true")
    args = parser.parse_args()
    state = start() if args.start else status()
    print(json.dumps({key: state.get(key) for key in ("running", "connected", "starting") if key in state}))
