"""WhatsApp transport over WAHA (WhatsApp HTTP API, https://waha.devlike.pro), run by ISHTI in its own local Docker
container. Draft/recipient approval belongs to the skill; this module only moves exact, approved bytes.

Security model
- The container publishes its port on 127.0.0.1 only; every call carries an API key that lives in the Windows
  Credential Manager (the container gets only its SHA-512 hash). Dashboard and Swagger are switched off.
- No proxy and no redirects are followed with the key (urllib with an empty ProxyHandler, redirects refused).
- Sends are idempotent per action id: a ledger records each attempt BEFORE the HTTP call, so an approved send is never
  repeated - an uncertain outcome is reported as uncertain, not retried.
- Incoming media is fetched only from this WAHA instance's /api/files/ path and parsed in a sandboxed, network-less
  worker (ishti/wa_media_reader.py) under a Job Object memory cap and a hard deadline.

Unofficial: WAHA speaks the WhatsApp Web protocol. Using it can get a number banned - use a number you can afford to lose.
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
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parent.parent
try:  # also importable as a standalone script (no package context)
    from ishti.test_mode import scratch_path
    DATA = scratch_path(str(ROOT / "data" / "wa_bridge"))
except Exception:  # pragma: no cover
    DATA = str(ROOT / "data" / "wa_bridge")

try:
    from ishti import CONFIG as _CONFIG
    _WA = dict(_CONFIG.get("whatsapp") or {})
except Exception:  # pragma: no cover
    _WA = {}
BASE = (os.environ.get("ISHTI_WAHA_URL") or _WA.get("waha_url") or "http://127.0.0.1:3747").rstrip("/")
SESSION = _WA.get("session") or "default"
CONTAINER = _WA.get("container") or "ishti-waha"
IMAGE = _WA.get("image") or "devlikeapro/waha:gows"
ENGINE = _WA.get("engine") or "GOWS"
VOLUME = _WA.get("volume") or "ishti_waha_sessions"
KEYRING_SERVICE, KEYRING_USER = "ishti-waha", "api-key"
MAX_MEDIA = 50 * 1024 * 1024
MAX_JSON = 4 * 1024 * 1024
LEDGER = Path(DATA) / "actions.json"
TEST_DESTINATION_FILE = Path(DATA) / "test_destination"
ACK = {"ERROR": "error", "PENDING": "pending", "SERVER": "server_ack", "DEVICE": "delivered", "READ": "read", "PLAYED": "read"}
_lock = threading.RLock()
_lids: dict[str, str] = {}


class BridgeError(RuntimeError):
    pass


def _test_mode():
    return os.environ.get("ISHTI_TEST_MODE") == "1"


# ---------------------------------------------------------------- credentials
def _api_key(create: bool = False) -> str:
    """The WAHA API key from the OS credential store; created once on first start. Never written to a file."""
    import keyring
    key = keyring.get_password(KEYRING_SERVICE, KEYRING_USER) or ""
    if key or not create:
        return key
    if _test_mode():
        raise BridgeError("Tests must inject a scratch WAHA key")
    key = secrets.token_urlsafe(32)
    keyring.set_password(KEYRING_SERVICE, KEYRING_USER, key)
    return key


# ---------------------------------------------------------------- HTTP
def _port_open(timeout: float = 0.3) -> bool:
    """A refused loopback connect costs ~2 s on this PC: probe with a short socket timeout first."""
    parts = urllib.parse.urlsplit(BASE)
    try:
        with socket.create_connection((parts.hostname, parts.port or 80), timeout=timeout):
            return True
    except OSError:
        return False


def _open(request, timeout=20):
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *_args, **_kwargs):
            return None
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect()).open(request, timeout=timeout)


def _request(method: str, path: str, payload=None, accept="application/json"):
    key = _api_key()
    if not key:
        raise BridgeError("WhatsApp is not set up yet: open Settings -> Connectors -> WhatsApp -> Link")
    data = json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None
    return urllib.request.Request(BASE + path, data=data, method=method,
                                  headers={"X-Api-Key": key, "Content-Type": "application/json", "Accept": accept})


def _call(method: str, path: str, payload=None, timeout=20):
    try:
        with _open(_request(method, path, payload), timeout) as response:
            raw = response.read(MAX_JSON + 1)
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            raise BridgeError("WAHA refused ISHTI's API key (the container was created with another key). "
                              f"Remove the '{CONTAINER}' container and link WhatsApp again.") from exc
        raise BridgeError(f"WhatsApp service rejected this operation (HTTP {exc.code})") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise BridgeError("WhatsApp service unavailable or outcome uncertain; nothing was retried") from exc
    if len(raw) > MAX_JSON:
        raise BridgeError("WhatsApp service response exceeds the size limit")
    return json.loads(raw) if raw.strip() else {}


def _q(value: str) -> str:
    return urllib.parse.quote(str(value), safe="")


# ---------------------------------------------------------------- container lifecycle
def _docker(*args, timeout=120) -> subprocess.CompletedProcess:
    docker = shutil.which("docker")
    if not docker:
        raise BridgeError("Docker Desktop is required for WhatsApp (WAHA). Install it from https://www.docker.com/, "
                          "start it, then link WhatsApp again.")
    return subprocess.run([docker, *args], capture_output=True, text=True, timeout=timeout,
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def _container_state() -> str:
    """'running', 'exited', ... or '' when the container does not exist."""
    p = _docker("inspect", "-f", "{{.State.Status}}", CONTAINER, timeout=30)
    return p.stdout.strip() if p.returncode == 0 else ""


def _run_container(key: str):
    port = urllib.parse.urlsplit(BASE).port or 3747
    digest = hashlib.sha512(key.encode()).hexdigest()
    p = _docker("run", "-d", "--name", CONTAINER, "--restart", "no",
                "-p", f"127.0.0.1:{port}:3000",              # loopback only: never reachable from the LAN
                "-v", f"{VOLUME}:/app/.sessions",
                "-e", f"WAHA_API_KEY=sha512:{digest}",
                "-e", f"WHATSAPP_DEFAULT_ENGINE={ENGINE}",
                "-e", f"WAHA_BASE_URL={BASE}",
                "-e", "WAHA_DASHBOARD_ENABLED=false", "-e", "WHATSAPP_SWAGGER_ENABLED=false",
                "-e", "WAHA_PRINT_QR=False", "-e", "WHATSAPP_RESTART_ALL_SESSIONS=True",
                "-e", "WAHA_LOG_LEVEL=warn", "--memory", "768m",
                IMAGE, timeout=600)
    if p.returncode != 0:
        raise BridgeError(f"Docker could not start WAHA: {(p.stderr or p.stdout).strip()[-200:]}")


def _ready() -> bool:
    """WAHA answers its unauthenticated /ping. Docker publishes the port BEFORE the server inside is up (measured:
    connections are accepted and reset for ~10 s), so an open port alone is not readiness."""
    if not _port_open():
        return False
    try:
        with _open(urllib.request.Request(BASE + "/ping"), timeout=3) as response:
            return response.status == 200
    except (urllib.error.URLError, OSError, TimeoutError):
        return False


def ensure_running(wait_s: float = 90) -> bool:
    """Start ISHTI's WAHA container (create it on first use). -> True when its API answers."""
    if _test_mode():
        raise BridgeError("Live WAHA start disabled in test mode")
    if _ready():
        return True
    with _lock:
        key = _api_key(create=True)
        state = _container_state()
        if state == "":
            _run_container(key)
        elif state != "running":
            p = _docker("start", CONTAINER, timeout=60)
            if p.returncode != 0:
                raise BridgeError(f"Docker could not restart WAHA: {(p.stderr or p.stdout).strip()[-200:]}")
    end = time.monotonic() + wait_s
    while time.monotonic() < end:
        if _ready():
            return True
        time.sleep(1)
    raise BridgeError("WAHA did not become ready in time; check Docker Desktop (docker logs ishti-waha)")


def stop() -> bool:
    """Stop ISHTI's own WAHA container (called at shutdown). The session stays in the volume: no re-pairing needed."""
    if _test_mode():
        return False
    try:
        return _container_state() == "running" and _docker("stop", "-t", "10", CONTAINER, timeout=40).returncode == 0
    except (BridgeError, OSError, subprocess.SubprocessError):
        return False


def logs(lines: int = 200) -> list[str]:
    """The last lines of the WAHA container's log (local diagnostics)."""
    try:
        p = _docker("logs", "--tail", str(max(1, min(2000, int(lines)))), CONTAINER, timeout=30)
    except BridgeError as exc:
        return [str(exc)]
    return ((p.stdout or "") + (p.stderr or "")).splitlines()[-lines:]


# ---------------------------------------------------------------- session state
def status() -> dict:
    """{connected, running, me, pairingState, qrAvailable, lastError}. Never raises."""
    if _test_mode() or not _port_open():
        return {"connected": False, "running": False}
    try:
        s = _call("GET", f"/api/sessions/{_q(SESSION)}", timeout=5)
    except BridgeError as exc:
        missing = "HTTP 404" in str(exc)
        return {"connected": False, "running": True, "pairingState": "NOT_CREATED" if missing else "ERROR",
                "lastError": "" if missing else str(exc)}
    state = str(s.get("status") or "")
    me = (s.get("me") or {}).get("id") or ""
    return {"connected": state == "WORKING", "running": True, "me": me, "pairingState": state,
            "qrAvailable": state == "SCAN_QR_CODE", "lastError": "" if state != "FAILED" else "session failed"}


def running() -> bool:
    return bool(status().get("running"))


def connected() -> bool:
    return bool(status().get("connected"))


def start() -> dict:
    """Start the container and the WhatsApp session; the UI then polls get_qr_data() until the owner scans."""
    if _test_mode():
        raise BridgeError("Live bridge launch disabled in test mode")
    ensure_running()
    state = status()
    if state.get("connected"):
        return state
    if state.get("pairingState") == "NOT_CREATED":
        _call("POST", "/api/sessions", {"name": SESSION, "start": True})
    elif state.get("pairingState") in ("STOPPED", "FAILED", "ERROR", ""):
        try:
            _call("POST", f"/api/sessions/{_q(SESSION)}/start", {})
        except BridgeError as exc:
            if "HTTP 422" not in str(exc):  # 422: already starting
                raise
    return {"running": True, "connected": False, "starting": True}


def _digits(value: str) -> str:
    return re.sub(r"\D", "", str(value or ""))


def _jid_number(jid: str) -> str:
    """The phone number of an account id, without its device suffix ("9230...:12@c.us" -> "9230...")."""
    return _digits(str(jid or "").split("@", 1)[0].split(":", 1)[0])


def get_qr_data() -> dict:
    state = status()
    image = None
    if state.get("qrAvailable"):
        try:
            qr = _call("GET", f"/api/{_q(SESSION)}/auth/qr", timeout=10)
            if qr.get("data") and str(qr.get("mimetype", "")).startswith("image/"):
                image = f"data:{qr['mimetype']};base64,{qr['data']}"
        except BridgeError as exc:
            state["lastError"] = str(exc)
    destination = test_destination()
    return {"connected": bool(state.get("connected")), "running": bool(state.get("running")),
            "qr_image": image, "raw_qr": None, "ascii_qr": "", "me": state.get("me") or "",
            "testDestination": destination,
            "accountMatches": bool(_digits(destination)) and _digits(destination) == _jid_number(state.get("me") or ""),
            "lastError": state.get("lastError"), "historyScope": "WhatsApp history synced by WAHA"}


def pair_hint() -> str:
    if connected():
        return "WhatsApp is linked through WAHA. I can read your chats and send messages after your yes."
    return ("Open Settings -> Connectors -> WhatsApp -> Link: ISHTI starts its local WAHA service and shows a QR code. "
            "Scan it on your phone under WhatsApp -> Linked devices.")


def install_hint() -> str:
    return ("WhatsApp runs through WAHA in Docker on this PC (loopback only). It is unofficial: use a number you can "
            "afford to lose.")


# ---------------------------------------------------------------- self-test destination (owner's own number)
def test_destination() -> str:
    """The owner-authorized self-chat destination, or '' when no gate is armed (ISHTI_WA_TEST_DESTINATION wins)."""
    explicit = os.environ.get("ISHTI_WA_TEST_DESTINATION", "").strip()
    if explicit:
        return explicit
    try:
        return TEST_DESTINATION_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def account_matches_self_test() -> bool:
    destination = _digits(test_destination())
    return bool(destination) and destination == _jid_number(status().get("me") or "")


def authorize_self_test(destination: str) -> dict:
    """Persist an owner authorization of the LINKED account as the self-test destination; any other number is refused."""
    if _test_mode():
        raise BridgeError("Live self-test authorization is disabled in test mode")
    number = _digits(destination)
    if not 6 <= len(number) <= 15:
        raise BridgeError("A complete international phone number is required")
    state = status()
    linked = _jid_number(state.get("me") or "")
    if not linked:
        raise BridgeError("The bridge is not connected to a WhatsApp account yet")
    if number != linked:
        raise BridgeError("The authorized number must be the account linked to this bridge")
    with _lock:
        TEST_DESTINATION_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = TEST_DESTINATION_FILE.with_suffix("." + uuid.uuid4().hex + ".tmp")
        try:
            tmp.write_text(number, encoding="utf-8")
            os.replace(tmp, TEST_DESTINATION_FILE)
        finally:
            tmp.unlink(missing_ok=True)
    return {"ok": True, "destination": number, "linked": linked, "requiresRestart": False}


# ---------------------------------------------------------------- reading
def chat_id(value: str) -> str:
    """Canonical WAHA chat id: '<digits>@c.us' for people ('@s.whatsapp.net' is converted), groups/lids unchanged."""
    value = str(value or "").strip()
    if value.endswith("@s.whatsapp.net"):
        return _jid_number(value) + "@c.us"
    return value


def _phone_of_lid(lid: str) -> str:
    """'<lid>@lid' -> '<digits>@c.us' through WAHA's lid map ('' when unknown). Cached per process."""
    if lid in _lids:
        return _lids[lid]
    try:
        pn = str(_call("GET", f"/api/{_q(SESSION)}/lids/{_q(lid)}", timeout=5).get("pn") or "")
    except BridgeError:
        pn = ""
    _lids[lid] = chat_id(pn) if pn else ""
    return _lids[lid]


def _msg_id(raw) -> str:
    if isinstance(raw, dict):
        return str(raw.get("_serialized") or raw.get("id") or "")
    return str(raw or "")


def _kind(m: dict) -> str:
    if not m.get("hasMedia"):
        return "text"
    mime = str((m.get("media") or {}).get("mimetype") or "").split(";")[0].lower()
    if mime.startswith("image/"):
        return "image"
    if mime.startswith("video/"):
        return "video"
    if mime.startswith("audio/"):
        return "voice" if (m.get("_data") or {}).get("ptt") or "ogg" in mime else "audio"
    return "document"


def _row(m: dict, chat: str) -> dict:
    """WAHA WAMessage -> ISHTI's message row (the shape every caller already uses)."""
    from_me = bool(m.get("fromMe"))
    sender = str(m.get("participant") or m.get("from") or "")
    if sender.endswith("@lid"):
        sender = _phone_of_lid(sender) or sender
    ts = int(float(m.get("timestamp") or 0) * 1000)
    media = m.get("media") or {}
    raw = json.dumps(m.get("_data") or {}, ensure_ascii=False)[:20000].lower()
    return {"chat": chat, "id": _msg_id(m.get("id")), "fromMe": from_me, "participant": m.get("participant"),
            "text": str(m.get("body") or ""), "kind": _kind(m), "restricted": '"viewonce": true' in raw or '"isviewonce": true' in raw,
            "media": {"mimetype": media.get("mimetype"), "fileName": media.get("filename")} if m.get("hasMedia") else None,
            "senderJid": "" if from_me else chat_id(sender), "ts": ts, "observedAt": ts,
            "viaApi": m.get("source") == "api", "delivery": ACK.get(str(m.get("ackName") or "").upper(), "")}


def chats(limit: int = 100) -> list:
    """[{id, name, unread, last{text, ts, fromMe}}], most recent first."""
    rows = _call("GET", f"/api/{_q(SESSION)}/chats/overview?" + urllib.parse.urlencode({"limit": max(1, min(500, int(limit)))}))
    out = []
    for c in rows if isinstance(rows, list) else []:
        last = c.get("lastMessage") or {}
        cid = chat_id(c.get("id") or "")
        if cid.endswith("@lid"):
            cid = _phone_of_lid(cid) or cid
        out.append({"id": cid, "name": str(c.get("name") or ""),
                    "unread": int(c.get("unreadCount") or (c.get("_chat") or {}).get("unreadCount") or 0),
                    "last": {"text": str(last.get("body") or ""), "ts": int(float(last.get("timestamp") or 0) * 1000),
                             "fromMe": bool(last.get("fromMe"))}})
    return out


def messages(chat: str = "", limit: int = 50, since_ms: int = 0) -> list:
    """Messages of one chat, oldest first. Without a chat: the recent messages of the most recently active chats."""
    if not chat:
        rows = []
        for c in chats(30):
            if c["last"]["ts"] > since_ms:
                rows += messages(c["id"], limit, since_ms)
        return sorted(rows, key=lambda r: r["ts"])[-max(1, int(limit)):]
    cid = chat_id(chat)
    params = {"limit": max(1, min(200, int(limit))), "downloadMedia": "false"}
    if since_ms:
        params["filter.timestamp.gte"] = int(since_ms // 1000)
    raw = _call("GET", f"/api/{_q(SESSION)}/chats/{_q(cid)}/messages?" + urllib.parse.urlencode(params))
    rows = [_row(m, cid) for m in (raw if isinstance(raw, list) else [])]
    return sorted(rows, key=lambda r: r["ts"])


def inbox_since(ms: int) -> list:
    """Messages newer than ms across chats with fresh activity: one overview call, then one call per such chat."""
    return [row for row in messages("", 200, since_ms=int(ms)) if row.get("observedAt", 0) > ms]


def find_chat(name: str) -> str:
    """Exact resolution only: a phone number, a full chat id, or ONE chat/contact whose name equals the request."""
    want = str(name or "").strip()
    if not want:
        return ""
    if re.fullmatch(r"[\w.-]+@(?:c\.us|g\.us|s\.whatsapp\.net|lid)", want):
        return chat_id(want)
    number = re.sub(r"[+ ()-]", "", want)
    if re.fullmatch(r"\d{6,15}", number):
        return number + "@c.us"
    key = want.casefold()
    hits = {c["id"] for c in chats(200) if c["name"].strip().casefold() == key}
    if not hits:
        try:
            contacts = _call("GET", "/api/contacts/all?" + urllib.parse.urlencode({"session": SESSION}))
        except BridgeError:
            contacts = []
        hits = {chat_id(c.get("id") or "") for c in (contacts if isinstance(contacts, list) else [])
                if key in {str(c.get(k) or "").strip().casefold() for k in ("name", "pushname", "shortName")}}
    return next(iter(hits)) if len(hits) == 1 else ""


def chat_names(limit: int = 200) -> dict:
    """{display name: chat id} of recent chats (fuzzy candidates for the skill to rank and ask about)."""
    return {c["name"]: c["id"] for c in chats(limit) if c["name"]}


# ---------------------------------------------------------------- sending
def _ledger() -> dict:
    try:
        return json.loads(LEDGER.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _record(action_id: str, entry: dict):
    data = _ledger()
    data[action_id] = {**entry, "at": time.time()}
    if len(data) > 2000:  # keep the newest entries; a week of sends is far below this
        data = dict(sorted(data.items(), key=lambda kv: kv[1].get("at", 0))[-1500:])
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    tmp = LEDGER.with_suffix("." + uuid.uuid4().hex + ".tmp")
    try:
        tmp.write_text(json.dumps(data), encoding="utf-8")
        os.replace(tmp, LEDGER)
    finally:
        tmp.unlink(missing_ok=True)


def _media_payload(media: str, kind: str, expected_hash: str | None) -> tuple[str, dict]:
    path = Path(str(media).strip('"\' '))
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_MEDIA:
        raise BridgeError("Attachment is unreadable or exceeds 50 MB")
    data = path.read_bytes()
    if not data or len(data) > MAX_MEDIA:
        raise BridgeError("Empty or oversized attachment")
    if expected_hash is not None and hashlib.sha256(data).hexdigest() != expected_hash:
        raise BridgeError("Attachment changed after approval")
    suffix = path.suffix.casefold()
    inferred = ("image" if suffix in {".png", ".jpg", ".jpeg", ".webp", ".gif"} else
                "video" if suffix in {".mp4", ".webm", ".mov"} else
                "audio" if suffix in {".wav", ".mp3", ".ogg", ".opus", ".m4a", ".aac"} else "document")
    kind = kind or inferred
    if kind == "voice" and (not data.startswith(b"OggS") or b"OpusHead" not in data[:4096]):
        raise BridgeError("Native voice notes require Ogg Opus; conversion failed")
    mime = mimetypes.guess_type(path.name)[0] or {".opus": "audio/ogg; codecs=opus", ".ogg": "audio/ogg"}.get(suffix, "application/octet-stream")
    endpoint = {"image": "sendImage", "video": "sendVideo", "voice": "sendVoice"}.get(kind, "sendFile")
    return endpoint, {"mimetype": mime, "filename": path.name, "data": base64.b64encode(data).decode()}


def send(chat: str, text: str = "", media: str = "", caption: str = "", *, action_id: str,
         media_kind: str = "", expected_hash: str | None = None) -> dict:
    """Send ONE approved item. An action id that was already attempted is never sent again."""
    if _test_mode():
        raise BridgeError("Live sending disabled in test mode")
    with _lock:
        previous = _ledger().get(action_id)
        if previous:
            return {**previous, "actionId": action_id, "replayed": True}
        cid = chat_id(chat)
        if media:
            endpoint, file = _media_payload(media, media_kind, expected_hash)
            payload = {"session": SESSION, "chatId": cid, "file": file, **({"caption": caption} if caption else {})}
        else:
            endpoint, payload = "sendText", {"session": SESSION, "chatId": cid, "text": text}
        _record(action_id, {"state": "attempting", "chat": cid})
    try:
        result = _call("POST", f"/api/{endpoint}", payload, timeout=60)
    except BridgeError:
        _record(action_id, {"state": "uncertain", "chat": cid})
        raise
    out = {"actionId": action_id, "state": "submitted", "id": _msg_id(result.get("id")),
           "delivery": ACK.get(str(result.get("ackName") or "").upper()) or "submitted", "chat": cid}
    _record(action_id, out)
    return out


def mark_read(chat: str) -> dict:
    if _test_mode():
        raise BridgeError("Live marking read disabled in test mode")
    return _call("POST", "/api/sendSeen", {"session": SESSION, "chatId": chat_id(chat)})


# ---------------------------------------------------------------- incoming media (sandboxed)
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
    """OCR/PDF text of one exact image or PDF message, read in the sandboxed worker."""
    cid = chat_id(chat)
    m = _call("GET", f"/api/{_q(SESSION)}/chats/{_q(cid)}/messages/{_q(message_id)}?downloadMedia=true", timeout=60)
    row = _row(m, cid) if isinstance(m, dict) else {}
    if not row or row.get("restricted") or row.get("kind") not in {"image", "document"}:
        raise BridgeError("An exact image or PDF message is required")
    url = str((m.get("media") or {}).get("url") or "")
    path = urllib.parse.urlsplit(url).path
    if not path.startswith("/api/files/"):
        raise BridgeError("The media file is no longer available (WAHA keeps downloads for a few minutes)")
    with _open(_request("GET", path, accept="*/*"), timeout=60) as response:  # always THIS instance, whatever host it names
        data = response.read(MAX_MEDIA + 1)
    if not data or len(data) > MAX_MEDIA:
        raise BridgeError("Incoming media is empty or oversized")
    digest = hashlib.sha256(data).hexdigest()
    result = _read_worker(row["kind"], data)
    return {**result, "source": {"chat": cid, "messageId": message_id, "sha256": digest, "kind": row["kind"],
                                 "fromMe": row.get("fromMe"), "timestamp": row.get("ts")}, "trust": "untrusted_message_content"}


def delete(*_args, **_kwargs):
    return {"ok": False, "error": "Message editing, deletion and reactions are not enabled; nothing was changed"}


edit = react = delete


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="ISHTI WhatsApp (WAHA) transport")
    parser.add_argument("--start", action="store_true", help="start WAHA and the WhatsApp session")
    parser.add_argument("--stop", action="store_true", help="stop ISHTI's WAHA container")
    parser.add_argument("--self-test", metavar="NUMBER", default="",
                        help="authorize the linked account as the self-chat test destination")
    args = parser.parse_args()
    if args.self_test:
        print(json.dumps(authorize_self_test(args.self_test)))
    elif args.stop:
        print(json.dumps({"stopped": stop()}))
    else:
        state = start() if args.start else status()
        print(json.dumps({key: state.get(key) for key in ("running", "connected", "starting", "pairingState") if key in state}))
