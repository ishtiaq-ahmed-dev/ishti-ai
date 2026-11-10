"""
ISHTI Web UI Server & Real-Time Orchestration Bridge
FastAPI + WebSocket server providing the futuristic JARVIS/ISHTI HUD interface,
real-time token streaming, multi-page execution & research views,
human-in-the-loop action approvals, and live system diagnostics.
"""

import os
import sys
import json
import time
import asyncio
import secrets
import threading
import re
from pathlib import Path
from typing import Dict, Any, List, Optional
from fastapi import APIRouter, FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse, FileResponse, Response, RedirectResponse
import uvicorn

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from orchestrator.model_router import ModelRouter
from orchestrator.doctor import IshtiDoctor
from orchestrator.memory.conversation import get_conversation_manager
from orchestrator.memory.error_logger import ErrorLogger
from voice.tts import speak, speakable
from orchestrator.memory.user_profile import get_user_memory
from ishti.dispatcher import api as ishti_api, handle as ishti_handle, HOOKS as ISHTI_HOOKS
from ishti import notifications as notifications_store
# Speed (Cycle 4): cached pre-compression, cache headers, selective gzip - with its own offline test.
from interface.web_ui.static_perf import CachedStaticFiles, SelectiveGZipMiddleware
from ishti.web_security import (authorized as dashboard_authorized, request_origin_ok, token_matches,
                                client_ip, direct_loopback, login_guard, owner_sessions, requires_local)
from ishti.core.logutil import swallowed

app = FastAPI(title="ISHTI Personal AI Assistant")


@app.exception_handler(json.JSONDecodeError)
async def _bad_json_body(_req, _exc):
    # One guard for every handler that does `await req.json()`: a malformed body is the caller's error.
    return JSONResponse({"error": "Request body must be valid JSON."}, status_code=400)
from ishti.business.api import router as business_api
app.include_router(business_api)

app.include_router(ishti_api)  # /api/chat, /api/skills, /api/facts (the dispatcher)

app.add_middleware(SelectiveGZipMiddleware)  # 1.02 MB JS -> ~290 KB, 107 KB CSS -> ~16 KB on the wire

# Remote access (tunnel, docs/REMOTE_ACCESS.md) arrives from cloudflared on 127.0.0.1,
# so "is it localhost?" is not enough: anything proxied or non-local must carry the token.
TOKEN_PATH = os.path.join(ROOT_DIR, "data", "api_token")


def _api_token() -> str:
    if not os.path.exists(TOKEN_PATH):
        os.makedirs(os.path.dirname(TOKEN_PATH), exist_ok=True)
        try:
            with open(TOKEN_PATH, "x") as f:
                f.write(secrets.token_urlsafe(32))
        except FileExistsError:
            pass  # another server process created it
    from ishti.secure_store import restrict_to_owner
    restrict_to_owner(TOKEN_PATH)  # it inherited Users:(RX) from the folder: other accounts could read the owner's token
    with open(TOKEN_PATH) as f:
        return f.read().strip()


API_TOKEN = _api_token()


def authorized(conn) -> bool:
    """Trust a same-origin direct loopback UI, or an authenticated caller."""
    return dashboard_authorized(conn, API_TOKEN)


def _start_local_search():
    """Start the local SearXNG (Docker) the app actually searches through.

    Only orchestrator/startup.py's [1b] ever called ensure_searxng(), and the desktop launch
    (desktop/launcher.py -> python -m ishti) does not go through it - so on a PC where the container had never
    been created, search stayed down for days while the health loop put a ConnectionError in the owner's bell
    on every launch (owner report 2026-10-13). create=True is the documented one-time setup: it starts Docker
    Desktop, pulls the image once, and afterwards just starts the existing container. Off the critical path:
    it runs on a thread and a failure only costs the fallback providers."""
    def run():
        try:
            from tools.web.search import ensure_searxng
            if ensure_searxng(wait_s=180, create=True):
                print("[ISHTI] local search (SearXNG) is up on 127.0.0.1:8890")
            else:
                print("[ISHTI] local search (SearXNG) could not be started; web search uses the fallback providers")
        except Exception as exc:
            print(f"[ISHTI] local search (SearXNG) could not be started ({type(exc).__name__}: {exc}); "
                  "web search uses the fallback providers")

    import threading
    threading.Thread(target=run, daemon=True, name="searxng-start").start()


@app.middleware("http")
async def require_token_for_remote(request: Request, call_next):
    if not request_origin_ok(request):
        return JSONResponse({"error": "foreign origin"}, status_code=403)
    public = request.url.path in {"/login", "/api/remote/login"}
    if public:
        return await call_next(request)
    if not authorized(request):
        if request.url.path.startswith("/api/"):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return RedirectResponse("/login", status_code=303)
    if requires_local(request.method, request.url.path) and not direct_loopback(request):
        return JSONResponse({"error": "This setting can be changed only on the local desktop"}, status_code=403)
    return await call_next(request)


def _https_request(request: Request) -> bool:
    return request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"


def _set_session_cookie(response: Response, request: Request, token: str):
    response.set_cookie("ishti_session", token, max_age=30 * 86400, path="/", httponly=True,
                        secure=_https_request(request), samesite="lax")


def _new_browser_session(request: Request, device="") -> str:
    label = str(device or request.headers.get("user-agent") or "Browser")[:120]
    return owner_sessions.create(ip=client_ip(request), device=label)


@app.get("/login")
async def remote_login_page(request: Request, code: str = ""):
    if code and owner_sessions.consume_magic(code):
        token = _new_browser_session(request)
        response = RedirectResponse("/", status_code=303)
        _set_session_cookie(response, request, token)
        return response
    path = os.path.join(ROOT_DIR, "interface", "web_ui", "static", "login.html")
    return FileResponse(path, media_type="text/html", headers={"Cache-Control": "no-store"})


@app.post("/api/remote/login")
async def remote_login(request: Request):
    ip = client_ip(request)
    remaining = login_guard.remaining(ip)
    if remaining:
        return JSONResponse({"error": "Too many attempts", "retry_after_s": remaining}, status_code=429)
    try:
        body = await request.json()
    except Exception:
        body = {}
    key, code = str(body.get("access_key") or ""), str(body.get("code") or "")
    valid = token_matches(key, API_TOKEN) or bool(code and owner_sessions.consume_magic(code))
    if not valid:
        remaining = login_guard.fail(ip)
        return JSONResponse({"error": "Invalid access key", "retry_after_s": remaining},
                            status_code=429 if remaining else 401)
    login_guard.success(ip)
    token = _new_browser_session(request, body.get("device_label", ""))
    response = JSONResponse({"ok": True})
    _set_session_cookie(response, request, token)
    return response


@app.post("/api/remote/logout")
async def remote_logout(request: Request):
    owner_sessions.revoke_token(request.cookies.get("ishti_session", ""))
    response = JSONResponse({"ok": True})
    response.delete_cookie("ishti_session", path="/")
    return response


@app.post("/api/remote/magic-link")
async def remote_magic_link(request: Request):
    if not direct_loopback(request):
        return JSONResponse({"error": "Magic links can be created only on the local desktop"}, status_code=403)
    code = owner_sessions.create_magic()
    try:
        from ishti import tunnel
        base = tunnel.url().rstrip("/")
    except Exception:
        base = ""
    return {"url": f"{base}/login?code={code}" if base else f"/login?code={code}", "expires_in_s": 300}


@app.get("/api/remote/sessions")
async def remote_sessions(_request: Request):
    return {"sessions": owner_sessions.list()}


@app.post("/api/remote/sessions/revoke")
async def remote_session_revoke(request: Request):
    try:
        session_id = str((await request.json()).get("id") or "")
    except Exception:
        session_id = ""
    return {"revoked": owner_sessions.revoke(session_id)}


@app.post("/api/remote/sessions/revoke-all")
async def remote_sessions_revoke_all(request: Request):
    if not direct_loopback(request):
        return JSONResponse({"error": "Revoke all is available only on the local desktop"}, status_code=403)
    return {"revoked": owner_sessions.revoke_all()}


@app.post("/api/remote/sessions/rotate-api-token")
async def remote_rotate_api_token(request: Request):
    if not direct_loopback(request):
        return JSONResponse({"error": "Key rotation is available only on the local desktop"}, status_code=403)
    global API_TOKEN
    new_token = secrets.token_urlsafe(32)
    tmp = TOKEN_PATH + f".{os.getpid()}.tmp"
    try:
        with open(tmp, "x", encoding="utf-8") as stream:
            stream.write(new_token)
        os.replace(tmp, TOKEN_PATH)
    finally:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
    API_TOKEN = new_token
    revoked = owner_sessions.revoke_all()
    return {"rotated": True, "sessions_revoked": revoked}


_REMOTE_SECRET = re.compile(r"(?i)\b(?P<label>bearer|api[_-]?token|authtoken|token|password|secret)\b\s*[:=]?\s*[^\s,;]+")
_REMOTE_PHONE = re.compile(r"(?<!\d)\+?\d[\d ()-]{7,}\d(?!\d)")


def _redact_remote_log(value: str) -> str:
    text = _REMOTE_SECRET.sub(lambda match: match.group("label") + ": [redacted]", str(value))

    def phone(match):
        raw = match.group(0); found = re.sub(r"\D", "", raw)
        return "•••••••" + found[-3:] if len(found) > 3 else "•••"

    return _REMOTE_PHONE.sub(phone, text)


def _tail_file(path: str, lines: int) -> list[str]:
    try:
        with open(path, "rb") as stream:
            stream.seek(0, os.SEEK_END); size = stream.tell(); stream.seek(max(0, size - 512 * 1024))
            data = stream.read(512 * 1024)
        return [_redact_remote_log(line[-2000:]) for line in data.decode("utf-8", "replace").splitlines()[-lines:]]
    except OSError:
        return []


@app.get("/api/remote/logs")
async def remote_logs(source: str = "audit", lines: int = 200):
    lines = max(1, min(500, int(lines)))
    if source == "audit":
        from ishti import audit
        rows = audit.query(limit=lines)
        output = [_redact_remote_log(f"{row[0]:.3f} {row[8] or '-'} {row[1]} {row[2] or '-'} {row[3]} {row[4]}")
                  for row in reversed(rows)]
    elif source == "backend":
        from ishti import lifecycle
        output = _tail_file(lifecycle.LOG_FILE, lines)
    elif source == "wa_bridge":
        from ishti import wa_bridge
        output = [_redact_remote_log(line) for line in wa_bridge.logs(lines)]
    else:
        return JSONResponse({"error": "source must be audit, backend or wa_bridge"}, status_code=400)
    return {"source": source, "lines": output, "count": len(output), "capped": lines}

STATIC_DIR = os.path.join(ROOT_DIR, "interface", "web_ui", "static")
REFLECTIONS_PATH = os.path.join(ROOT_DIR, "orchestrator", "memory", "reflections.md")
RESEARCH_MEM_DIR = os.path.join(ROOT_DIR, "orchestrator", "memory", "research")

os.makedirs(STATIC_DIR, exist_ok=True)
os.makedirs(RESEARCH_MEM_DIR, exist_ok=True)

# Mount static files (the old HUD's own assets: short cache - they are not content-hashed)
app.mount("/static", CachedStaticFiles(directory=STATIC_DIR, max_age=300), name="static")

# Shared state
model_router = ModelRouter()
active_websockets: List[WebSocket] = []
WS_SEND_TIMEOUT_S = 5.0
last_execution_state: Dict[str, Any] = {}
last_research_state: Dict[str, Any] = {}
MAIN_LOOP: asyncio.AbstractEventLoop = None  # set at startup; worker threads have no loop of their own


async def _capture_loop():
    global MAIN_LOOP
    MAIN_LOOP = asyncio.get_running_loop()
    from ishti import self_heal
    from ishti.business import runtime as business_runtime, engine as business_engine
    from ishti import killswitch as business_killswitch
    if not os.environ.get("ISHTI_TEST_MODE") and os.environ.get("ISHTI_BUSINESS_DESK", "1") != "0":
        business_runtime.start()
        business_killswitch.on_kill(lambda: business_engine.pause() or "business observation paused")
    self_heal.ensure_loop()  # idles until the owner turns Auto-Heal ON
    from ishti import CONFIG
    from ishti.test_mode import enabled as _test_mode_on, scratch_dir as _scratch_dir
    if not _test_mode_on() and not _scratch_dir():
        # data/ inherited Modify for every signed-in Windows account from the drive root: lock the tree to the owner
        # (+ SYSTEM and Administrators). Off the startup path - propagating to thousands of files takes a moment.
        from ishti.secure_store import restrict_to_owner
        threading.Thread(target=restrict_to_owner, args=(os.path.dirname(CONFIG["memory"]["db_path"]),),
                         kwargs={"inherit": True}, daemon=True, name="lock-data-dir").start()
    from ishti.skills.reminders.skill import start as start_reminders

    def announce(msg, kind="job"):  # due reminders: HUD and bell always, spoken unless muted or in quiet hours
        if msg is None:  # the job already left its own bell entry (a question waiting for a yes, or a failure)
            return
        sync_event_callback("assistant_message", {"reply": "⏰ " + msg})
        text = str(msg or "").strip()
        # persistent bell entry (survives restarts); the announced prefix tells reminders/timers apart
        notifications_store.add("reminder" if text.lower().startswith(("reminder", "timer")) else kind, text)
        from ishti.channels import policy as _policy
        if get_user_memory().get_preferences().audio_output and not _policy.in_quiet_hours():
            from ishti.voice import speak as piper_speak
            piper_speak(msg)

    # scheduled tasks ("every morning at 8 give me my brief") run through the dispatcher; the answer is announced
    start_reminders(announce, run_task=run_scheduled)
    from ishti.skills.automation.skill import start as start_automations
    # "when a pdf appears in downloads, summarize it": the action runs like a scheduled task, its answer is announced
    start_automations(announce, run_task=run_scheduled)
    from ishti.skills.focus.skill import start as start_focus
    start_focus(announce)  # focus sprints: "start a 25 minute focus sprint" ends with an announcement
    from ishti.skills.webtools.skill import _start as start_page_watch
    start_page_watch()  # pages the owner asked to watch keep being checked after a restart
    from ishti.skills.shopping.skill import start_price_watch
    start_price_watch()  # products the owner asked to track keep being re-priced
    from ishti.skills.lifelog.skill import start_sampler
    start_sampler()  # screen time: the foreground app once a minute, local only
    from ishti.skills.desktop.more import start_clipboard_history
    start_clipboard_history()  # "clipboard history": last 100 text copies, local SQLite, secrets skipped
    from ishti import health, killswitch
    from ishti.talk import talker as _pc_talker
    health.start(lambda m: announce(m, kind="alert"))  # A1: degrading components are flagged before they fail
    _start_local_search()  # SearXNG in Docker: without it every search falls back and the health loop flags it
    killswitch.on_kill(lambda: _pc_talker().stop() and "PC speech")  # A2: whatever the PC is saying stops
    if not os.environ.get("ISHTI_WATCHDOG"):  # no watchdog process: the hotkey lives here (works unless this process hangs)
        try:
            from pynput import keyboard
            keyboard.GlobalHotKeys({CONFIG.get("kill_hotkey", "<ctrl>+<alt>+q"): lambda: _kill("hotkey")}).start()
        except Exception as exc:
            print(f"[ISHTI] kill switch hotkey unavailable ({exc}); the Stop button still works")
    if CONFIG["voice"].get("wake_enabled"):
        from ishti import mic_control
        mic_control.apply_on_startup()  # starts the wake listener ONLY if the owner left mic ON (default OFF)


# Register startup/shutdown handlers without the deprecated on_event() API (FastAPI >= 0.93).
app.router.on_startup.append(_capture_loop)


def _stop_business_observer():
    from ishti.business import runtime as business_runtime
    business_runtime.stop()


app.router.on_shutdown.append(_stop_business_observer)


async def broadcast_event(event_type: str, data: Dict[str, Any]):
    message = json.dumps({"type": event_type, "data": data}, ensure_ascii=False)
    disconnected = []
    for ws in list(active_websockets):
        try:
            await _send_websocket(ws, message)
        except Exception:
            disconnected.append(ws)
    for ws in disconnected:
        if ws in active_websockets:
            active_websockets.remove(ws)


async def _send_websocket(ws: WebSocket, message: str):
    """Serialize writes so event broadcasts and the endpoint loop cannot corrupt each other's frames.

    The lock lives on the socket object itself, so a closed connection's lock dies with it. A global
    id()-keyed map leaked locks bound to dead event loops, and CPython id() reuse could then hand a
    new connection a lock from a loop that no longer exists (send raises, connection drops)."""
    lock = getattr(ws, "_ishti_send_lock", None)
    if lock is None:
        lock = asyncio.Lock()
        try:
            ws._ishti_send_lock = lock
        except AttributeError:
            pass  # exotic socket double without settable attrs: fall back to unserialized sends
    async with lock:
        await asyncio.wait_for(ws.send_text(message), timeout=WS_SEND_TIMEOUT_S)


def sync_event_callback(event_type: str, data: Dict[str, Any]):
    """Called from executor worker threads to broadcast to Web UI."""
    if MAIN_LOOP and MAIN_LOOP.is_running():
        asyncio.run_coroutine_threadsafe(broadcast_event(event_type, data), MAIN_LOOP)


# Skills confirm consequential actions in the conversation itself (session 'pending'), so there is
# no separate UI approval gate any more.
ISHTI_HOOKS.update(emit=sync_event_callback)


APP_DIST = os.path.join(ROOT_DIR, "interface", "app", "dist")  # the React app (cd interface/app && npm run build)
if os.path.isdir(os.path.join(APP_DIST, "assets")):
    # Vite content-hashes every name: safe to cache for a year, repeat loads cost zero bytes.
    app.mount("/assets", CachedStaticFiles(directory=os.path.join(APP_DIST, "assets"),
                                           max_age=31536000, immutable=True), name="app-assets")

# Local UI fonts (tools/fetch_ui_fonts.py): versioned names, served from memory-fast disk cache.
_FONTS_DIR = os.path.join(APP_DIST, "fonts") if os.path.isdir(os.path.join(APP_DIST, "fonts")) \
    else os.path.join(ROOT_DIR, "interface", "app", "public", "fonts")
if os.path.isdir(_FONTS_DIR):
    app.mount("/fonts", CachedStaticFiles(directory=_FONTS_DIR, max_age=31536000, immutable=True), name="app-fonts")


_BRAND_FILES: Dict[str, tuple] = {}  # filename -> (path, media) resolved once; the HUD asks on every load


def _brand_file(filename: str):
    """(path, media type) for a brand/icon file, or None. Resolved once per name (isfile() on four
    directories per request was pure overhead on the HUD's critical path)."""
    if filename not in _BRAND_FILES:
        search_dirs = [APP_DIST, os.path.join(ROOT_DIR, "interface", "app", "public"), STATIC_DIR,
                       os.path.join(ROOT_DIR, "assets")]
        found = None
        names = [filename] if filename else []
        names += ["ishti-icon.svg", "favicon.svg", "favicon.png", "ishti-logo.png"]  # fallbacks
        for name in names:
            for d in search_dirs:
                candidate = os.path.join(d, name)
                if os.path.isfile(candidate):
                    found = (candidate, "image/svg+xml" if name.endswith(".svg") else
                             "image/png" if name.endswith(".png") else "image/x-icon")
                    break
            if found:
                break
        _BRAND_FILES[filename] = found
    return _BRAND_FILES[filename]


@app.get("/favicon.ico")
@app.get("/favicon.svg")
@app.get("/favicon.png")
@app.get("/ishti-icon.svg")
@app.get("/ishti-icon.png")
@app.get("/ishti-logo.svg")
@app.get("/ishti-logo.png")
async def get_favicons_and_brand_assets(request: Request):
    """Serve favicons and brand logos with correct media types (resolved once, cached a day)."""
    hit = _brand_file(request.url.path.lstrip("/"))
    if not hit:
        return JSONResponse({"error": "not found"}, status_code=404)
    return FileResponse(hit[0], media_type=hit[1], headers={"Cache-Control": "public, max-age=86400"})


_INDEX_CACHE = {"mtime": None, "html": "", "etag": ""}


def _built_index():
    """The built React index.html, cached in memory (it is fetched on every HUD open/reload).
    Re-read only when the file changes, so `npm run build` is picked up without a server restart."""
    built = os.path.join(APP_DIST, "index.html")
    if not os.path.exists(built):
        return None
    st = os.stat(built)
    if _INDEX_CACHE["mtime"] != st.st_mtime_ns:
        with open(built, encoding="utf-8") as f:
            _INDEX_CACHE.update(mtime=st.st_mtime_ns, html=f.read(),
                                etag=f'"{st.st_mtime_ns:x}-{st.st_size:x}"')
    return _INDEX_CACHE


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """Serve the modern React dashboard application."""
    cached = _built_index()
    if not cached:
        return HTMLResponse(
            "<!DOCTYPE html><html><head><title>ISHTI Dashboard</title></head><body style='font-family:sans-serif;padding:40px;background:#0d1117;color:#e6edf3;'>"
            "<h2>ISHTI Dashboard Not Built</h2>"
            "<p>Please build the dashboard by running: <code>cd interface/app && npm run build</code></p>"
            "</body></html>",
            status_code=503
        )
    headers = {"Cache-Control": "no-cache", "ETag": cached["etag"]}  # revalidate -> 304, never stale
    if request.headers.get("if-none-match") == cached["etag"]:
        return Response(status_code=304, headers=headers)
    return HTMLResponse(cached["html"], headers=headers)


@app.get("/hud")
async def hud():
    """Redirect legacy HUD route to main modern dashboard."""
    return RedirectResponse(url="/", status_code=307)


def _kill(reason):
    from ishti import killswitch
    stopped = killswitch.kill(reason)
    sync_event_callback("killed", {"reason": reason, "stopped": stopped})  # the dashboard marks running answers stopped
    return stopped


@app.post("/api/kill")
async def kill_endpoint(req: Request):
    """A2 kill switch: stop everything now (speech, generation, browser steps, tools, recordings, pending questions)."""
    try:
        reason = (await req.json()).get("reason", "stop button")
    except Exception:
        reason = "stop button"
    return {"stopped": _kill(str(reason)[:40])}


# POST /api/shutdown, /api/ready, /api/vram, /ws/events ...: ishti/lifecycle_api.py (docs/API_CONTRACT.md), attached below.


@app.get("/api/diagnostics/calls")
async def diagnostic_calls():
    from ishti import calltrace, background
    return {"requests": list(calltrace.RECENT), "background": background.snapshot()}


@app.get("/api/setup")
async def setup_get():
    """The first-run choice: this PC (Local) or a cloud API. chosen=False means the owner has not picked yet."""
    from ishti import llm, setup_choice
    return {**setup_choice.state(llm.api_settings().get("api_key_env", "ISHTI_API_KEY")), "provider_now": llm.provider()}


@app.post("/api/setup")
async def setup_post(req: Request):
    """Save the owner's choice. The API key is never sent here: it is read from the environment variable named in config."""
    from ishti import setup_choice
    body = await req.json()
    try:
        if body.get("api_key"):
            from ishti.files_api import _not_direct
            if _not_direct(req.scope):
                return JSONResponse({"error": "Credentials may only be set from this PC's window."}, status_code=403)
            setup_choice.save_key(str(body.get("api_provider", "")), body["api_key"])
        d = setup_choice.save(str(body.get("provider", "")), str(body.get("api_provider", "")), str(body.get("model", "")),
                              credential_source="keyring" if body.get("api_key") else None)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    except Exception:
        return JSONResponse({"error": "The OS credential store could not save the key. Use the configured environment variable instead."}, status_code=503)
    from ishti import llm
    try:  # one 4-token call, off the event loop, so a rejected key shows up here and not on the first question
        check = await asyncio.wait_for(asyncio.to_thread(llm.check_provider), timeout=25)
    except Exception:
        check = {"ok": False, "message": "The provider did not answer in time. Saved anyway - try a question to check."}
    return JSONResponse({"saved": True, "choice": d, "check": check})


_REMOTE_HEALTH = {"value": {"available": False}, "at": 0.0, "busy": False}


async def _refresh_remote_health():
    """The remote-channel probe makes a network call (the WhatsApp bridge). It runs in a worker thread, never on the event
    loop: a probe on the loop stalled every request while the bridge was down, so the boot health check never got an answer."""
    _REMOTE_HEALTH["busy"] = True
    try:
        from ishti.channels import service as _remote_service
        _REMOTE_HEALTH["value"] = await asyncio.to_thread(lambda: _remote_service.get().health())
    except Exception:
        _REMOTE_HEALTH["value"] = {"available": False}
    finally:
        _REMOTE_HEALTH["at"] = time.time()
        _REMOTE_HEALTH["busy"] = False


@app.get("/api/health")
async def health_endpoint():
    """Liveness for the watchdog (answering at all = not hung) + the latest health-loop snapshot."""
    from ishti import health, lifecycle
    # remote-control channels (docs/API_CONTRACT.md §11): the last probe answers at once; a stale one is refreshed in the background
    if not _REMOTE_HEALTH["busy"] and time.time() - _REMOTE_HEALTH["at"] > 5:
        asyncio.ensure_future(_refresh_remote_health())
    return {"ok": True, "state": lifecycle.state(), "remote": _REMOTE_HEALTH["value"], **health.snapshot()}


# ---------------------------------------------------------------- Connectors
# A proper Connectors section, backed by ishti/connectors.py. GET lists them with their real state;
# the POSTs are the three actions a card offers: save the Client ID/Secret, start the consent flow, disconnect.
# The consent flow runs on a worker thread (it waits on a loopback redirect), so the page polls progress.
@app.get("/api/connectors")
def connectors_list():
    from ishti import connectors, contract_api
    out = connectors.summary()
    # one list for the UI: legacy OAuth rows + the id/kind/status/configured view + WhatsApp/Telegram channel rows
    extra = {r["id"]: r for r in contract_api._connector_rows()}
    rows = [{**r, **{k: v for k, v in extra.pop(r.get("service"), {}).items() if k != "detail"}} for r in out["connectors"]]
    out["connectors"] = rows + list(extra.values())
    return out


@app.get("/api/connectors/{service}/progress")
def connectors_progress(service: str):
    from ishti import connectors
    from ishti import oauth
    if service not in oauth.PROVIDERS:
        return JSONResponse({"ok": False, "error": f"{service} is not a connector ISHTI knows."}, status_code=404)
    return JSONResponse({"ok": True, "progress": connectors.progress(service), "connector": connectors.entry(service)})


@app.post("/api/connectors/{service}/credentials")
async def connectors_credentials(service: str, req: Request):
    """Save the pasted Client ID/Secret. The secret is write-only - it is never echoed back."""
    from ishti import connectors
    try:
        body = await req.json()
    except Exception:  # noqa: BLE001
        body = {}
    if not isinstance(body, dict):
        return JSONResponse({"ok": False, "error": "body must be a JSON object"}, status_code=400)
    res = await asyncio.to_thread(connectors.set_credentials, service, str(body.get("client_id") or ""),
                                  str(body.get("client_secret") or ""))
    return JSONResponse(res, status_code=200 if res.get("ok") else 400)


@app.post("/api/connectors/{service}/connect")
async def connectors_connect(service: str):
    """Open the consent page and return at once; the page polls /progress until it lands."""
    from ishti import connectors, contract_api
    if service in ("whatsapp", "telegram"):  # channel connectors live in contract_api (this route is registered first)
        return await contract_api.connector_connect(service)
    res = connectors.start(service)
    return JSONResponse(res, status_code=200 if res.get("ok") else 400)


@app.post("/api/connectors/{service}/disconnect")
async def connectors_disconnect(service: str):
    from ishti import connectors, contract_api
    if service in ("whatsapp", "telegram"):
        return await contract_api.connector_disconnect(service)
    res = connectors.disconnect(service)
    return JSONResponse(res, status_code=200 if res.get("ok") else 400)


@app.get("/api/inputs")
async def inputs_endpoint():
    """Every way ISHTI can take input, with its live state (the dashboard's Inputs section).

    Thin wrapper: the state-gathering lives in ishti/inputs.py so it is testable on its own and never
    depends on the web server being up.
    """
    from ishti import inputs
    return inputs.snapshot()


@app.get("/api/inputs/wa-qr")
async def inputs_wa_qr():
    from ishti import wa_bridge
    return JSONResponse(await asyncio.to_thread(wa_bridge.get_qr_data))


@app.post("/api/inputs/wa-start")
async def inputs_wa_start():
    from ishti import wa_bridge
    try:
        return JSONResponse(await asyncio.to_thread(wa_bridge.start))
    except wa_bridge.BridgeError as exc:
        return JSONResponse({"error": str(exc)}, status_code=503)


@app.get("/api/inputs/clipboard")
async def inputs_clipboard_test():
    try:
        import pyperclip
        text = await asyncio.to_thread(pyperclip.paste)
        preview = (text[:120] + "...") if len(text) > 120 else text
        return JSONResponse({"ok": True, "preview": preview or "(Clipboard is empty)", "length": len(text)})
    except Exception as exc:
        return JSONResponse({"ok": False, "error": f"Clipboard unavailable: {exc}"}, status_code=503)





@app.get("/api/status")
def get_status():  # a plain def: FastAPI runs it in a thread; it makes blocking HTTP calls (Ollama) and must not stall the event loop
    installed = model_router.get_installed_models()
    primary = model_router.get_model("primary")
    fast = model_router.get_model("fast")
    fallback = model_router.get_model("fallback")

    from ishti.dispatcher import SKILLS as ISHTI_SKILLS
    skills = [s.info() for s in ISHTI_SKILLS.values()]  # the live skill manifests, with load status

    return {
        "status": "online",
        "assistant_name": "ISHTI",
        "primary_model": primary,
        "fast_model": fast,
        "fallback_model": fallback,
        "installed_models": installed,
        "skills_count": len(skills),
        "skills": skills,
        "last_execution": last_execution_state,
        "last_research": last_research_state,
    }


_DOCTOR = {"report": None, "at": 0.0, "task": None}


def _doctor_refresh():
    """One diagnostics run in the background; the result is cached so reads answer at once."""
    t = _DOCTOR["task"]
    if t is None or t.done():
        _DOCTOR["task"] = asyncio.get_event_loop().run_in_executor(None, IshtiDoctor().check_all)

        def _store(fut):
            try:
                _DOCTOR["report"], _DOCTOR["at"] = fut.result(), time.time()
            except Exception as exc:
                swallowed("server.doctor_refresh", exc)
        _DOCTOR["task"].add_done_callback(_store)
    return _DOCTOR["task"]


@app.get("/api/doctor")
async def get_doctor(fresh: int = 0):
    """fresh=1 (the Run diagnostics button) waits for a new run (up to 55 s). Otherwise the last report is returned
    immediately (with age_s) and a refresh is started when it is older than 60 s; the very first call waits."""
    age = time.time() - _DOCTOR["at"]
    try:
        if fresh or _DOCTOR["report"] is None:
            await asyncio.wait_for(asyncio.shield(_doctor_refresh()), timeout=55.0)
        elif age > 60:
            _doctor_refresh()
    except asyncio.TimeoutError:
        if _DOCTOR["report"] is None:
            return JSONResponse({"ok": False, "error": "Diagnostics are still running; try again in a moment."}, status_code=202)
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)[:500]}, status_code=503)
    rep = dict(_DOCTOR["report"] or {})
    rep["age_s"] = round(time.time() - _DOCTOR["at"], 1) if _DOCTOR["at"] else None
    return JSONResponse(rep)


@app.get("/api/research")
async def get_research_memories():
    memories = []
    if os.path.exists(RESEARCH_MEM_DIR):
        for fname in os.listdir(RESEARCH_MEM_DIR):
            if fname.endswith(".json"):
                try:
                    with open(os.path.join(RESEARCH_MEM_DIR, fname), "r", encoding="utf-8") as f:
                        memories.append(json.load(f))
                except Exception:
                    swallowed("server.research_archive_read")
    # Sort newest first
    memories.sort(key=lambda m: m.get("timestamp", ""), reverse=True)
    return JSONResponse({"archives": memories, "count": len(memories)})


@app.get("/api/reflections")
async def get_reflections():
    if os.path.exists(REFLECTIONS_PATH):
        with open(REFLECTIONS_PATH, "r", encoding="utf-8") as f:
            return {"content": f.read()}
    return {"content": "No reflection log found."}


@app.get("/api/self_heal/status")
async def self_heal_status():
    from ishti import self_heal
    return JSONResponse(self_heal.get_status())


@app.post("/api/self_heal/toggle")
async def self_heal_toggle(req: Request):
    from ishti import self_heal
    body = await req.json()
    enabled = body.get("enabled", not self_heal.is_enabled())
    return JSONResponse({"enabled": self_heal.set_enabled(enabled)})


@app.post("/api/self_heal/trigger")
async def self_heal_trigger():
    from ishti import self_heal
    loop = asyncio.get_running_loop()
    res = await loop.run_in_executor(None, self_heal.trigger_heal)
    return JSONResponse(res)


# ---- Memory maintenance (the one architecture's memory store) ---------------------------------
@app.get("/api/memory/stats")
async def memory_stats():
    from ishti.memstore import memory as amem
    try:
        return JSONResponse(amem.stats())
    except Exception as exc:
        return JSONResponse({"error": str(exc)})


@app.post("/api/memory/promote")
async def arch_memory_promote():
    """Run the observation->semantic promotion now (normally automatic at the thresholds)."""
    from ishti.memstore import memory as amem
    promoted = amem.promote_observations()
    return JSONResponse({"promoted": len(promoted), "items": [p["content"] for p in promoted]})


_MEMORY_VIEW = ("active", "needs_confirmation", "superseded")


@app.get("/api/memory/items")
async def memory_items(status: str = "active", q: str = ""):
    """Everything ISHTI remembers, for the owner to read, correct or forget (newest first)."""
    from ishti.memstore import memory as amem
    if status not in _MEMORY_VIEW:
        return JSONResponse({"error": f"status must be one of {', '.join(_MEMORY_VIEW)}"}, status_code=400)
    rows = amem.search(text=q.strip()[:200] or None, status=status, limit=300)
    rows.sort(key=lambda r: r.get("created_at") or 0, reverse=True)
    keep = ("id", "type", "content", "source", "confidence", "created_at", "valid_until", "times_observed")
    return JSONResponse({"items": [{k: r.get(k) for k in keep} for r in rows]})


@app.post("/api/memory/items/{mid}")
async def memory_item_edit(mid: str, req: Request):
    """A correction supersedes the old row (kept as 'was true until now'), it never overwrites it."""
    from ishti.memstore import memory as amem
    content = str((await req.json()).get("content", "")).strip()
    if not content or len(content) > 1000:
        return JSONResponse({"error": "content must be 1-1000 characters"}, status_code=400)
    old = amem.get(mid)
    if not old:
        return JSONResponse({"error": "no such memory"}, status_code=404)
    if old["content"].strip() == content:  # unchanged: superseding would hide the very row being kept
        return JSONResponse({"item": old})
    return JSONResponse({"item": amem.supersede(mid, content, source="explicit_user")})


@app.post("/api/memory/items/{mid}/confirm")
async def memory_item_confirm(mid: str):
    from ishti.memstore import memory as amem
    return JSONResponse({"confirmed": amem.confirm(mid)})


@app.delete("/api/memory/items/{mid}")
async def memory_item_forget(mid: str):
    from ishti.memstore import memory as amem
    if not amem.get(mid):
        return JSONResponse({"error": "no such memory"}, status_code=404)
    return JSONResponse({"forgotten": amem.forget(mid)})


@app.post("/api/memory/decay")
async def arch_memory_decay():
    """Run a decay pass now: stale low-importance memories are marked needs_confirmation (never deleted)."""
    from ishti.memstore import memory as amem
    flagged = amem.decay()
    return JSONResponse({"flagged": len(flagged), "items": [r["content"] for r in flagged]})


# ---- Global microphone control (Section 7) ---------------------------------------------------
@app.get("/api/mic/status")
async def mic_status():
    from ishti import mic_control
    return JSONResponse(mic_control.status())


@app.post("/api/mic/on")
async def mic_on_endpoint():
    from ishti import mic_control
    res = mic_control.mic_on(by="dashboard")
    await broadcast_event("mic_state", res)
    return JSONResponse(res)


@app.post("/api/mic/off")
async def mic_off_endpoint():
    from ishti import mic_control
    res = mic_control.mic_off("dashboard toggle", by="dashboard", also_stop_live=True)
    await broadcast_event("mic_state", res)
    return JSONResponse(res)


@app.post("/api/mic/toggle")
async def mic_toggle_endpoint():
    from ishti import mic_control
    res = mic_control.toggle(by="dashboard")
    await broadcast_event("mic_state", res)
    return JSONResponse(res)


@app.post("/api/execute")
async def execute_task(req: Request):
    body = await req.json()
    prompt = body.get("prompt", "").strip()
    session_id = body.get("session_id", "").strip() or None
    if not prompt:
        return JSONResponse({"error": "Empty prompt"}, status_code=400)

    result = await asyncio.get_running_loop().run_in_executor(None, _execute, prompt, session_id)
    # Broadcast stream_end with full payload for synchronized UI completion
    await broadcast_event("task_completed_full", result)
    return JSONResponse(result)


def _until_killed(fn, prompt, session_id):
    """Run a request; if the kill switch fires, answer "Stopped." at once - the abandoned work winds down in the
    background (it stops at its next checkpoint and its result is discarded)."""
    import concurrent.futures
    from ishti import killswitch
    gen = killswitch.generation()
    box = concurrent.futures.Future()

    def work():
        try:
            box.set_result(fn())
        except BaseException as exc:
            box.set_exception(exc)
    import threading
    threading.Thread(target=work, daemon=True, name="request").start()
    while True:
        try:
            return box.result(timeout=0.05)
        except concurrent.futures.TimeoutError:
            if killswitch.generation() != gen:
                return {"request": prompt, "final_answer": "Stopped.", "mode": "STOPPED", "skill": None, "sources": [],
                        "session_id": session_id}


def _execute(prompt, session_id, on_token=None, on_event=None) -> dict:
    """Dispatcher call shared by /api/execute and /api/execute_stream (runs in a worker thread)."""
    global last_execution_state, last_research_state
    try:
        # Typed (mode B) and recorded (mode A) messages get text replies only - ISHTI speaks only in Live mode
        # (/ws/live) and hands-free wake-word mode. It used to speak typed chats whenever audio was switched on.
        res = _until_killed(lambda: ishti_handle(prompt, session_id=session_id, on_token=on_token, on_event=on_event),
                            prompt, session_id)
    except Exception as exc:
        ErrorLogger.log_error(subsystem="SERVER", error=exc, user_prompt=prompt, context="execute endpoint")
        notifications_store.add("error", f"Request failed: {str(exc)[:200]}")
        res = {"error": str(exc), "final_answer": f"Server error: {str(exc)[:200]}"}
    last_execution_state = res
    if res.get("mode") == "DEEP_RESEARCH":
        last_research_state = res
    return res


@app.post("/api/execute_stream")
async def execute_stream(req: Request):
    """NDJSON: {"event": {...}} trace lines and {"token": "..."} reply chunks as they happen,
    then {"done": true, "result": {...}}."""
    body = await req.json()
    prompt = body.get("prompt", "").strip()
    session_id = body.get("session_id", "").strip() or None
    if not prompt:
        return JSONResponse({"error": "Empty prompt"}, status_code=400)
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()  # {"token": str} or {"event": dict}, in arrival order
    job = loop.run_in_executor(None, _execute, prompt, session_id,
                               lambda t: loop.call_soon_threadsafe(queue.put_nowait, {"token": t}),
                               lambda ev: loop.call_soon_threadsafe(queue.put_nowait, {"event": ev}))

    async def lines():
        while not (job.done() and queue.empty()):
            try:
                yield json.dumps(await asyncio.wait_for(queue.get(), 0.1), ensure_ascii=False, default=str) + "\n"
            except asyncio.TimeoutError:
                continue
        try:
            result = job.result()
        except Exception as exc:
            result = {"error": str(exc), "final_answer": f"Execution failed: {str(exc)}", "sources": []}
        try:
            await broadcast_event("task_completed_full", result)
        except Exception:
            swallowed("server.broadcast_completion")
        yield json.dumps({"done": True, "result": result}, ensure_ascii=False) + "\n"

    return StreamingResponse(lines(), media_type="application/x-ndjson")


# ========================================================
# SESSION MANAGEMENT API
# ========================================================
@app.get("/api/sessions")
async def list_sessions():
    conv = get_conversation_manager()
    return JSONResponse({"sessions": conv.list_sessions(), "active_session_id": conv.active_session_id})


@app.post("/api/sessions/new")
async def create_new_session(req: Request):
    try:
        body = await req.json()
    except Exception:
        return JSONResponse({"error": "Send a JSON object with an optional title."}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"error": "Send a JSON object with an optional title."}, status_code=400)
    title = body.get("title", "New Conversation")
    conv = get_conversation_manager()
    new_id = conv.new_chat(title)
    return JSONResponse({"session_id": new_id, "title": title})


@app.get("/api/sessions/{session_id}/messages")
async def get_session_messages(session_id: str):
    conv = get_conversation_manager()
    messages = conv.get_messages(session_id)
    session = conv.load_session(session_id)
    return JSONResponse({
        "session_id": session_id,
        "title": session.get("title", "Untitled") if session else "Not Found",
        "messages": messages,
    })


@app.post("/api/sessions/{session_id}/feedback")
async def rate_session_answer(session_id: str, req: Request):
    """Rate one saved answer. A dislike creates a text-only revision, never a tool action."""
    body = await req.json()
    from ishti.answer_feedback import submit
    try:
        result = await asyncio.to_thread(submit, session_id, str(body.get("message_id") or ""),
                                         str(body.get("rating") or ""), str(body.get("reason") or ""))
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return JSONResponse(result)


@app.delete("/api/sessions/{session_id}")
async def delete_session(session_id: str):
    conv = get_conversation_manager()
    deleted = conv.delete_session(session_id)
    return JSONResponse({"deleted": deleted, "session_id": session_id})


@app.post("/api/sessions/{session_id}/activate")
async def activate_session(session_id: str):
    conv = get_conversation_manager()
    session = conv.load_session(session_id)
    if session:
        conv.active_session_id = session_id
        return JSONResponse({"activated": True, "session_id": session_id})
    return JSONResponse({"activated": False, "error": "Session not found"}, status_code=404)


# ========================================================
# NOTIFICATION API (the bell: reminders, jobs, downloads, alerts, errors)
# ========================================================
@app.get("/api/notifications")
async def list_notifications(limit: int = 50):
    return JSONResponse({"notifications": notifications_store.recent(limit), "unread": notifications_store.unread()})


@app.post("/api/notifications/read")
async def notifications_mark_read(req: Request):
    try:
        body = await req.json()
    except Exception:
        body = {}
    nid = body.get("id") if isinstance(body, dict) else None
    return JSONResponse({"unread": notifications_store.mark_read(nid),
                         "marked": "all" if nid in (None, "") else nid})


def run_scheduled(t):
    """A scheduled task / automation runs in its OWN session, so its yes/no question belongs to it: in the one shared
    "scheduled" session a question could only be answered from that session (nobody could) and concurrent jobs
    overwrote each other's. The question waits in the bell with Yes/No; a failure is reported, never swallowed."""
    import hashlib
    sid = "scheduled-" + hashlib.sha1(t.encode("utf-8")).hexdigest()[:10]  # no ":" - session ids become file names
    try:
        reply = ishti_handle(t, session_id=sid, channel="scheduled").get("final_answer") or ""
    except Exception as exc:
        streak = _FAIL_STREAK[sid] = _FAIL_STREAK.get(sid, 0) + 1
        if streak in (1, 3):  # first failure, then once more when it keeps failing - never a flood
            notifications_store.add("error", f"Scheduled task failed{' 3 times in a row' if streak == 3 else ''}: {t[:120]}",
                                    f"{type(exc).__name__}: {exc}"[:300] + (" - say \"list my reminders\" to cancel it."
                                                                             if streak == 3 else ""))
        return None
    _FAIL_STREAK.pop(sid, None)
    if re.search(r"\(yes/no\)\s*$", reply):
        from ishti.dispatcher import SESSIONS
        pending = (SESSIONS.get(sid) or {}).get("pending") or {}
        for old in notifications_store.recent(100):  # an older question of this job can no longer be answered
            if (old.get("meta") or {}).get("session") == sid and (old.get("meta") or {}).get("answerable"):
                notifications_store.set_meta(old["id"], {**old["meta"], "answerable": False})
        # the full question in the body (what will be sent/run), the pending's timestamp ties the answer to it
        notifications_store.add("alert", f"Waiting for your yes: {reply[:120]}", body=reply[:2000],
                                meta={"session": sid, "answerable": True, "pending_at": pending.get("at")})
        return None
    # silent unless something changed: a repeating check ("every hour check my inbox") with the same answer as last
    # time says nothing. ponytail: in memory, so the first run after a restart always speaks.
    digest = hashlib.sha1(reply.strip().lower().encode("utf-8")).hexdigest()
    if _LAST_RESULT.get(sid) == digest:
        return None
    _LAST_RESULT[sid] = digest
    return reply


_LAST_RESULT: dict = {}
_FAIL_STREAK: dict = {}


@app.post("/api/notifications/{nid}/answer")
async def notification_answer(nid: int, req: Request):
    """The owner's yes/no to a scheduled job's question, sent into that job's own session (the dispatcher's approval
    rules apply unchanged, including the 10-minute expiry)."""
    try:
        body = await req.json()
    except Exception:
        body = {}
    answer = str((body or {}).get("answer", "")).strip().lower()
    if answer not in ("yes", "no"):
        return JSONResponse({"error": "answer must be yes or no"}, status_code=400)
    n = notifications_store.get(nid) or {}
    meta = n.get("meta") or {}
    sid = str(meta.get("session", ""))
    if not (meta.get("answerable") and sid.startswith("scheduled-")):
        return JSONResponse({"error": "nothing is waiting for an answer here"}, status_code=404)
    from ishti.dispatcher import SESSIONS
    live = (SESSIONS.get(sid) or {}).get("pending") or {}
    if meta.get("pending_at") is None or live.get("at") != meta.get("pending_at"):
        # a later run of the job asked something else: a yes here would approve THAT, not what this entry shows
        notifications_store.set_meta(nid, {**meta, "answerable": False})
        return JSONResponse({"error": "That question has expired or been replaced - nothing was done."}, status_code=409)
    notifications_store.set_meta(nid, {**meta, "answerable": False})
    # a second or late answer is safe: the session's pending action is consumed once and expires after 10 minutes
    notifications_store.mark_read(nid)
    res = await asyncio.to_thread(ishti_handle, answer, session_id=sid, channel="scheduled")
    return JSONResponse({"reply": res.get("final_answer", "")})


# ========================================================
# CHAT MANAGEMENT API (rename, bulk delete with undo, local share/export)
# ========================================================
def _safe_session_ids(raw) -> list:
    """Only real session ids: no path separators can reach the sessions folder."""
    return [str(i) for i in (raw or [])
            if str(i).startswith("sess_") and "/" not in str(i) and "\\" not in str(i)]


@app.post("/api/sessions/{session_id}/rename")
async def rename_session(session_id: str, req: Request):
    try:
        body = await req.json()
    except Exception:
        body = {}
    saved = get_conversation_manager().rename_session(session_id, (body or {}).get("title", ""))
    if saved is None:
        return JSONResponse({"error": "That chat is gone, or the title was empty."}, status_code=400)
    return JSONResponse({"ok": True, "session_id": session_id, "title": saved})


@app.post("/api/sessions/delete")
async def delete_sessions(req: Request):
    """Delete selected chats, but keep them in a trash folder for the undo toast (8 s)."""
    try:
        body = await req.json()
    except Exception:
        body = {}
    ids = _safe_session_ids((body or {}).get("ids"))
    token = get_conversation_manager().trash(ids)
    return JSONResponse({"ok": True, "deleted": ids, "undo_token": token, "undo_seconds": 8})


@app.post("/api/sessions/undo")
async def undo_delete_sessions(req: Request):
    try:
        body = await req.json()
    except Exception:
        body = {}
    restored = get_conversation_manager().restore((body or {}).get("undo_token"))
    return JSONResponse({"ok": True, "restored": restored})


@app.post("/api/sessions/export")
async def export_sessions(req: Request):
    """Share: write the chosen chats to a local Markdown/PDF file the owner picked. Nothing leaves this PC."""
    import time
    try:
        body = await req.json()
    except Exception:
        body = {}
    body = body if isinstance(body, dict) else {}
    ids = _safe_session_ids(body.get("ids"))
    fmt = "pdf" if str(body.get("format", "md")).lower() == "pdf" else "md"
    from ishti import files_api
    wanted = str(body.get("dir") or "").strip()
    folder = os.path.realpath(wanted) if wanted else os.path.join(ROOT_DIR, "data", "exports")
    roots = [os.path.realpath(r) for r in files_api.allowed_roots()]
    if not any(folder == r or folder.startswith(r + os.sep) for r in roots) or not os.path.isdir(folder):
        return JSONResponse({"error": "I can only save into ISHTI's reports/data folders or your Documents/ISHTI, "
                                      "Downloads and Desktop."}, status_code=403)
    markdown = get_conversation_manager().export_markdown(ids)
    if not ids:
        return JSONResponse({"error": "Pick at least one chat to share."}, status_code=400)
    name = files_api.safe_name(body.get("name") or f"ishti-chats-{time.strftime('%Y%m%d-%H%M%S')}.{fmt}")
    if not name.lower().endswith("." + fmt):
        name = os.path.splitext(name)[0] + "." + fmt
    path = os.path.join(folder, name)
    method = ""
    try:
        from ishti.skills.chat_digest import export as digest_export
        written = digest_export.save_pdf(markdown, path=path) if fmt == "pdf" else digest_export.save_md(markdown, path=path)
        # save_pdf answers (path, engine); save_md answers the path
        saved = written[0] if isinstance(written, (tuple, list)) else written
        method = str(written[1]) if isinstance(written, (tuple, list)) and len(written) > 1 else ""
    except Exception as exc:
        return JSONResponse({"error": f"Could not write the file: {exc}"}, status_code=500)
    if not saved or not os.path.isfile(saved):
        return JSONResponse({"error": "The export came back empty."}, status_code=500)
    return JSONResponse({"ok": True, "path": saved, "format": fmt, "chats": len(ids), "method": method})


# ========================================================
# ERROR LOG API
# ========================================================
@app.get("/api/errors")
async def get_error_log():
    records = ErrorLogger.get_all_errors()
    return JSONResponse({"errors": records, "count": len(records)})


@app.post("/api/errors/clear")
async def clear_errors():
    cleared = ErrorLogger.clear_all()
    return JSONResponse({"cleared": cleared})


@app.post("/api/rebenchmark")
async def trigger_rebenchmark():
    loop = asyncio.get_event_loop()
    await broadcast_event("benchmark_started", {"status": "Benchmarking local Ollama models on RTX 5070 Ti..."})

    def run_bm():
        return model_router.run_full_benchmark(reason="user_ui_request")

    scores = await loop.run_in_executor(None, run_bm)
    await broadcast_event("benchmark_completed", {"scores": scores})
    notifications_store.add("job", "Model re-benchmarking complete", body="The best models were selected.")  # shown in the bell, not spoken
    return JSONResponse({"status": "success", "scores": scores})


LIVE = {"stop": None}  # one Live conversation at a time


@app.websocket("/ws/live")
async def live_ws(ws: WebSocket):
    """Mode C - Live agent: the browser streams its microphone (16 kHz mono int16, binary frames); the voice loop runs
    in Live mode (no wake word, barge-in, replies spoken sentence by sentence - played by the browser, so it works
    from a phone and the browser's echo cancellation hears past ISHTI's own voice) and every event (state,
    live transcript, steps, reply, stage timings) comes back on this socket. The talk is saved in the open chat."""
    if not authorized(ws):
        await ws.close(code=1008)
        return
    import queue as queue_mod
    import threading
    import numpy as np
    from ishti import wake
    from ishti.talk import RemoteTalker
    await ws.accept()
    session_id = ws.query_params.get("session") or None
    loop = asyncio.get_running_loop()
    live_send_lock = asyncio.Lock()
    frames, stop = queue_mod.Queue(), threading.Event()
    from ishti.voice import stt_model

    def _warm_voice():  # Whisper AND the reply voice load while the owner starts talking (turn 1 paid both)
        stt_model()
        try:
            from ishti.voice import current_voice, kokoro, tts_voice
            v = current_voice()
            kokoro(v) if v.get("engine") == "kokoro" else tts_voice(v)
        except Exception as exc:
            print(f"[live] voice warm-up skipped: {type(exc).__name__}")
    threading.Thread(target=_warm_voice, daemon=True).start()
    if LIVE["stop"]:
        LIVE["stop"].set()
    LIVE["stop"] = stop

    async def _live_send(payload, binary=False):
        async with live_send_lock:
            if binary:
                await ws.send_bytes(payload)
            else:
                await ws.send_text(payload)

    def send(kind, data):  # called from worker threads
        if not stop.is_set():
            asyncio.run_coroutine_threadsafe(_live_send(json.dumps({"type": kind, "data": data}, default=str)), loop)

    def send_audio(msg):  # the reply's voice plays in the browser: binary = 16-bit PCM, dict = format/stop
        if stop.is_set():
            return
        payload = msg if isinstance(msg, bytes) else json.dumps(msg)
        asyncio.run_coroutine_threadsafe(_live_send(payload, binary=isinstance(msg, bytes)), loop)
    voice = RemoteTalker(send_audio)
    from ishti import killswitch
    killswitch.on_kill(lambda: voice.stop() and "Live voice")  # the browser stops talking too

    def on_command(text, on_token=None):
        res = ishti_handle(text, session_id=session_id, channel="voice", on_token=on_token,
                           on_event=lambda ev: send("trace", ev))
        send("reply", {"user": text, "reply": res["final_answer"], "session_id": res.get("session_id")})
        sync_event_callback("task_completed_full", res)  # keep the normal chat view in sync with Live mode
        return res["final_answer"]

    listener = wake.Listener(on_command, send, live=True, talker=voice)
    from ishti import mic_control
    mic_control.register_live(stop)  # mic-off from anywhere closes this live session too

    def read_block():
        while not stop.is_set():
            try:
                return frames.get(timeout=0.5)
            except queue_mod.Empty:
                continue
        return None
    threading.Thread(target=listener.loop, args=(read_block, stop), daemon=True, name="live").start()
    carry = np.zeros(0, np.int16)
    try:
        while True:
            msg = await ws.receive()
            if msg.get("type") == "websocket.disconnect":
                break
            if msg.get("bytes"):
                carry = np.concatenate([carry, np.frombuffer(msg["bytes"], np.int16)])
                while len(carry) >= wake.BLOCK:
                    frames.put(carry[:wake.BLOCK].copy())
                    carry = carry[wake.BLOCK:]
            elif msg.get("text") == "end":
                break
            elif msg.get("text") == "stop":  # "Stop talking" without ending the call: like speaking over ISHTI
                listener.interrupted = {"request": getattr(listener, "request", ""), "rest": voice.stop()}
                listener.cancel.set()
    except WebSocketDisconnect:
        return
    finally:
        stop.set()
        voice.close()


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    if not authorized(websocket):
        await websocket.close(code=1008)
        return
    await websocket.accept()
    active_websockets.append(websocket)
    from ishti import events
    loop = asyncio.get_running_loop()
    q = asyncio.Queue(maxsize=500)

    def _put(m):
        if q.full():
            # A dropped event may be a chat completion. Discard the stale backlog and tell the UI
            # to refetch durable state instead of silently leaving the screen stale.
            while not q.empty():
                try:
                    q.get_nowait()
                except Exception:
                    break
            try:
                q.put_nowait({"type": "resync_required", "data": {"reason": "event queue overflow"}})
            except Exception:
                swallowed("server.websocket_resync_queue")
        try:
            q.put_nowait(m)
        except Exception:
            swallowed("server.websocket_event_queue")

    def push(m):
        loop.call_soon_threadsafe(_put, m)

    recv_task = get_task = None
    try:
        events.subscribe(push)
        await _send_websocket(websocket, json.dumps({
            "type": "connected",
            "data": {"message": "ISHTI Neural Core Connected."}
        }))
        for m in events.replay():
            await _send_websocket(websocket, json.dumps(m, ensure_ascii=False, default=str))

        recv_task = asyncio.ensure_future(websocket.receive_text())
        while True:
            get_task = asyncio.ensure_future(q.get())
            done, _ = await asyncio.wait({recv_task, get_task}, return_when=asyncio.FIRST_COMPLETED)
            if get_task in done:
                event_msg = get_task.result()
                await _send_websocket(websocket, json.dumps(event_msg, ensure_ascii=False, default=str))
            else:
                get_task.cancel()

            if recv_task in done:
                raw_text = recv_task.result()
                recv_task = asyncio.ensure_future(websocket.receive_text())
                try:
                    msg = json.loads(raw_text)
                    if msg.get("action") == "execute":
                        prompt = msg.get("prompt", "")
                        if prompt:
                            def run_sync():
                                return ishti_handle(prompt)
                            res = await loop.run_in_executor(None, run_sync)
                            await _send_websocket(websocket, json.dumps({"type": "task_completed_full", "data": res}))
                except Exception as e:
                    print(f"[WS Error]: {e}")
    except (WebSocketDisconnect, RuntimeError):
        return
    finally:
        events.unsubscribe(push)
        if websocket in active_websockets:
            active_websockets.remove(websocket)
        tasks = [task for task in (recv_task, get_task) if task is not None]
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)




# ---- Learning dashboard API (docs/LEARNING_INTERFACE.md section 4) -----------------------------
# The learning package (ishti/learning/) may still be under construction: every endpoint imports it
# lazily inside the handler, so a missing or half-built module answers {"error": ...} (400) instead
# of breaking the server import. Only stores and runtime settings change here - never model weights.
learning_api = APIRouter()


def _learning():
    """Lazy handle on ishti.learning.dashboard; keeps a broken package from breaking this server."""
    from ishti.learning import dashboard as api
    return api


def _learning_fail(exc: Exception) -> JSONResponse:
    return JSONResponse({"error": str(exc)}, status_code=400)


async def _learning_body(req: Request) -> dict:
    try:
        body = await req.json()
    except Exception:
        return {}
    return body if isinstance(body, dict) else {}


def _learning_rows(result, key: str) -> list:
    """dashboard helpers may return a bare list or {"<key>": [...]}; the HTTP shape is fixed."""
    value = result.get(key, []) if isinstance(result, dict) else result
    return value if isinstance(value, list) else []


def _learning_bool(value, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    if isinstance(value, (int, float)):
        return bool(value)
    return default


def _learning_settings(result) -> dict:
    data = result if isinstance(result, dict) else {}
    return {"settings": data.get("settings") or {}, "thresholds": data.get("thresholds") or {},
            "evidence": data.get("evidence") or {}}


@learning_api.get("/api/learning/overview")
async def learning_overview():
    """The three feedback loops at a glance: counts, confidence buckets, rewards, recent activity."""
    try:
        data = _learning().overview()
        if not isinstance(data, dict):
            raise RuntimeError("learning overview returned no data")
        return {
            "enabled": _learning_bool(data.get("enabled"), True),
            "db": data.get("db", ""),
            "counts": data.get("counts") or {},
            "loops": data.get("loops") or {},
            "confidence_buckets": data.get("confidence_buckets") or {},
            "thresholds": data.get("thresholds") or {},
            "settings": data.get("settings") or {},
            "recent": data.get("recent") or [],
        }
    except Exception as exc:
        return _learning_fail(exc)


@learning_api.get("/api/learning/lessons")
async def learning_lessons(status: str = "", kind: str = "", q: str = "", limit: int = 200):
    """Lesson store, filterable by status/kind and free-text search."""
    try:
        result = _learning().lessons(status=status or None, kind=kind or None, q=q or None, limit=limit)
        return {"lessons": _learning_rows(result, "lessons")}
    except Exception as exc:
        return _learning_fail(exc)


@learning_api.post("/api/learning/lessons/{lesson_id}")
async def learning_lesson_update(lesson_id: str, req: Request):
    """Edit a lesson in place (statement/scope/context/confidence/status/note)."""
    try:
        body = await _learning_body(req)
        fields = {k: body[k] for k in ("status", "statement", "scope", "context", "note")
                  if body.get(k) not in (None, "")}
        if body.get("confidence") not in (None, ""):
            fields["confidence"] = float(body["confidence"])
        if not fields:
            raise ValueError("nothing to update")
        result = _learning().update_lesson(lesson_id, **fields)
        lesson = result.get("lesson") if isinstance(result, dict) and "lesson" in result else result
        changed = result.get("changed") if isinstance(result, dict) else None
        return {"lesson": lesson, "changed": changed if isinstance(changed, list) else []}
    except Exception as exc:
        return _learning_fail(exc)


@learning_api.post("/api/learning/lessons/{lesson_id}/confirm")
async def learning_lesson_confirm(lesson_id: str):
    """HITL: the owner confirms a lesson; the change is recorded in the evidence trail."""
    try:
        api = _learning()
        fn = getattr(api, "confirm_lesson", None) or getattr(api, "confirm", None)
        result = fn(lesson_id) if callable(fn) else api.update_lesson(lesson_id, status="confirmed")
        lesson = result.get("lesson") if isinstance(result, dict) and "lesson" in result else result
        return {"lesson": lesson, "action": "confirmed"}
    except Exception as exc:
        return _learning_fail(exc)


@learning_api.post("/api/learning/lessons/{lesson_id}/reject")
async def learning_lesson_reject(lesson_id: str):
    """HITL: the owner rejects a lesson (kept for audit, no longer applied)."""
    try:
        api = _learning()
        fn = getattr(api, "reject_lesson", None) or getattr(api, "reject", None)
        result = fn(lesson_id) if callable(fn) else api.update_lesson(lesson_id, status="rejected")
        lesson = result.get("lesson") if isinstance(result, dict) and "lesson" in result else result
        return {"lesson": lesson, "action": "rejected"}
    except Exception as exc:
        return _learning_fail(exc)

@learning_api.delete("/api/learning/lessons/{lesson_id}")
async def learning_lesson_delete(lesson_id: str, forget: int = 0):
    """Delete a lesson; ?forget=1 also forgets the memory/fact it wrote back."""
    try:
        result = _learning().delete_lesson(lesson_id, forget=bool(forget))
        data = result if isinstance(result, dict) else {}
        return {"deleted": _learning_bool(data.get("deleted"), True),
                "memory_forgotten": _learning_bool(data.get("memory_forgotten"), False)}
    except Exception as exc:
        return _learning_fail(exc)


@learning_api.get("/api/learning/candidates")
async def learning_candidates(status: str = "open"):
    """Candidate rules/questions waiting for the owner's yes/no."""
    try:
        result = _learning().candidates(status=status or None)
        return {"candidates": _learning_rows(result, "candidates")}
    except Exception as exc:
        return _learning_fail(exc)


@learning_api.post("/api/learning/candidates/{candidate_id}")
async def learning_candidate_answer(candidate_id: int, req: Request):
    """Keep a candidate (lesson written) or discard it."""
    try:
        body = await _learning_body(req)
        keep = _learning_bool(body.get("keep"), True)
        edits = body.get("edits") if isinstance(body.get("edits"), dict) else None
        result = _learning().answer_candidate(candidate_id, keep, edits)
        data = result if isinstance(result, dict) else {}
        return {"candidate": data.get("candidate"), "lesson": data.get("lesson")}
    except Exception as exc:
        return _learning_fail(exc)


@learning_api.get("/api/learning/feedback")
async def learning_feedback(limit: int = 100):
    """Structured feedback rows (corrections, praise, error classes)."""
    try:
        return {"feedback": _learning_rows(_learning().feedback_list(limit), "feedback")}
    except Exception as exc:
        return _learning_fail(exc)


@learning_api.get("/api/learning/reflections")
async def learning_reflections(limit: int = 100):
    """Verbal reflections (Reflexion) plus mined-but-not-yet-promoted insights (ExpeL).

    These are records of what happened, kept at the weak confidence band: retrievable on an exact
    word match, but never able to out-rank something the owner confirmed.
    """
    try:
        data = _learning().reflections(limit)
        data = data if isinstance(data, dict) else {}
        return {"reflections": data.get("reflections") or [],
                "mined": data.get("mined") or [],
                "counts": data.get("counts") or {}}
    except Exception as exc:
        return _learning_fail(exc)


@learning_api.post("/api/learning/reflections/mine")
async def learning_mine_insights(req: Request):
    """Mine now: promote interactions that read as rules into low-confidence lessons."""
    try:
        body = await _learning_body(req)
        limit = int(body.get("limit") or 50)
        data = _learning().promote_insights(limit)
        data = data if isinstance(data, dict) else {}
        return {"promoted": data.get("promoted") or [], "error": data.get("error")}
    except Exception as exc:
        return _learning_fail(exc)


@learning_api.get("/api/learning/mistakes")
async def learning_mistakes(limit: int = 200):
    """Repeated, error-classified failures grouped by signature."""
    try:
        return {"mistakes": _learning_rows(_learning().mistakes(limit), "mistakes")}
    except Exception as exc:
        return _learning_fail(exc)


@learning_api.get("/api/learning/interactions")
async def learning_interactions(limit: int = 50):
    """One row per agent turn: request, decision, status, reward."""
    try:
        return {"interactions": _learning_rows(_learning().interactions(limit), "interactions")}
    except Exception as exc:
        return _learning_fail(exc)


@learning_api.get("/api/learning/tasks")
async def learning_tasks(limit: int = 50):
    """Multi-step tasks with per-step results and final status."""
    try:
        return {"tasks": _learning_rows(_learning().tasks(limit), "tasks")}
    except Exception as exc:
        return _learning_fail(exc)


@learning_api.get("/api/learning/workflows")
async def learning_workflows():
    """Recurring successful procedures mined from tasks (candidate workflows)."""
    try:
        return {"workflows": _learning_rows(_learning().workflows(), "workflows")}
    except Exception as exc:
        return _learning_fail(exc)


@learning_api.post("/api/learning/workflows/{workflow_id}")
async def learning_workflow_status(workflow_id: int, req: Request):
    """Activate or retire a workflow."""
    try:
        body = await _learning_body(req)
        status = str(body.get("status", "")).strip().lower()
        if status not in ("active", "retired"):
            raise ValueError("status must be 'active' or 'retired'")
        api = _learning()
        fn = getattr(api, "set_workflow", None) or getattr(api, "update_workflow", None)
        if not callable(fn):
            from ishti.learning import workflows as workflow_api
            fn = workflow_api.set_status
        result = fn(workflow_id, status)
        data = result if isinstance(result, dict) else {}
        return {"workflow": data.get("workflow", result)}
    except Exception as exc:
        return _learning_fail(exc)


@learning_api.get("/api/learning/people")
async def learning_people():
    """People/entities with context; similar names are separate rows, never merged by name."""
    try:
        return {"people": _learning_rows(_learning().people(), "people")}
    except Exception as exc:
        return _learning_fail(exc)


KIN_GENERATIONS = {
    # 1: Grandparents
    "grandfather": 1, "grandmother": 1, "dada": 1, "dadi": 1, "nana": 1, "nani": 1,
    # 2: Parents & Elders
    "father": 2, "mother": 2, "uncle": 2, "aunt": 2, "chacha": 2, "chachi": 2, "mamu": 2, "mami": 2,
    "khala": 2, "khalu": 2, "phuppo": 2, "phupha": 2, "abu": 2, "ami": 2, "abbu": 2, "ammi": 2, "baba": 2,
    # 3: Self, Siblings & Peers
    "self": 3, "me": 3, "brother": 3, "sister": 3, "sibling": 3, "spouse": 3, "wife": 3, "husband": 3,
    "cousin": 3, "bro": 3, "bros": 3, "bhai": 3, "bhaiya": 3, "sis": 3, "behan": 3, "baji": 3,
    # 4: Children & Youth
    "son": 4, "daughter": 4, "child": 4, "nephew": 4, "niece": 4, "beta": 4, "beti": 4,
    "bhanja": 4, "bhanji": 4, "bhatija": 4, "bhatiji": 4, "grandchild": 4,
}

REL_NORMALIZED = {
    "bro": "brother", "bros": "brother", "bhai": "brother", "bhaiya": "brother",
    "sis": "sister", "behan": "sister", "baji": "sister",
    "ami": "mother", "ammi": "mother", "abu": "father", "abbu": "father", "baba": "father",
}


def _get_family_members():
    from ishti import rgraph
    from ishti.learning import entities as ent_module
    from ishti.facts import get_facts
    owner_name = get_facts().get("owner").get("name") or "You"
    members = {
        "me": {
            "id": "me",
            "name": owner_name,
            "relationship": "self",
            "generation": 3,
            "phone": "",
            "email": "",
            "note": "Primary user & account owner",
            "first_seen": 1727800000,
            "last_confirmed": time.time(),
            "confidence": 1.0,
            "source": "owner"
        }
    }
    try:
        edges = rgraph.edges_of("me", direction="out") or []
        for e in edges:
            rel = (e.get("rel") or "").lower().strip()
            name = (e.get("d_name") or "").strip()
            if not name or name.lower() in ("me", "you", owner_name.lower()):
                continue
            if rel in KIN_GENERATIONS:
                rel = REL_NORMALIZED.get(rel, rel)
                gen = KIN_GENERATIONS.get(rel, 3)
                key = name.lower()
                members[key] = {
                    "id": key,
                    "name": name.title(),
                    "relationship": rel,
                    "generation": gen,
                    "phone": "",
                    "email": "",
                    "note": e.get("detail") or "",
                    "first_seen": e.get("first_seen") or time.time(),
                    "last_confirmed": e.get("last_confirmed") or time.time(),
                    "confidence": float(e.get("confidence") or 1.0),
                    "source": e.get("source_type") or "explicit_user"
                }
    except Exception:
        swallowed("server.family_relationship_read")

    try:
        rows = ent_module.list_people()
        for r in rows:
            name = (r.get("name") or "").strip()
            rel = (r.get("relationship") or "").lower().strip()
            if not name or name.lower() in ("me", "you", owner_name.lower()):
                continue
            if rel in KIN_GENERATIONS:
                rel = REL_NORMALIZED.get(rel, rel)
                key = name.lower()
                gen = KIN_GENERATIONS.get(rel, 3)
                if key in members:
                    if r.get("phone"):
                        members[key]["phone"] = r["phone"]
                    if r.get("email"):
                        members[key]["email"] = r["email"]
                    if r.get("note"):
                        members[key]["note"] = r["note"]
                    if r.get("last_seen"):
                        members[key]["last_confirmed"] = max(members[key]["last_confirmed"], float(r["last_seen"]))
                else:
                    members[key] = {
                        "id": key,
                        "name": name.title(),
                        "relationship": rel,
                        "generation": gen,
                        "phone": r.get("phone") or "",
                        "email": r.get("email") or "",
                        "note": r.get("note") or "",
                        "first_seen": float(r.get("last_seen") or time.time()),
                        "last_confirmed": float(r.get("last_seen") or time.time()),
                        "confidence": float(r.get("confidence") or 0.9),
                        "source": r.get("source") or "learning"
                    }
    except Exception:
        swallowed("server.family_entities_read")

    return sorted(members.values(), key=lambda m: (m["generation"], -m["last_confirmed"]))


@learning_api.get("/api/learning/family")
async def learning_family_get():
    """Retrieve family tree members organized by generation and chronology."""
    try:
        return {"family": _get_family_members()}
    except Exception as exc:
        return _learning_fail(exc)


@learning_api.post("/api/learning/family")
async def learning_family_add(req: Request):
    """Add or update a family member in both rgraph kin and learning entities."""
    try:
        body = await _learning_body(req)
        name = str(body.get("name", "")).strip()
        rel = str(body.get("relationship", "")).strip().lower()
        if not name or not rel:
            raise ValueError("name and relationship are required")
        if rel not in KIN_GENERATIONS or rel in ("self", "me"):
            raise ValueError("Select a supported family relationship")
        from ishti import rgraph
        from ishti.learning import entities as ent_module
        phone = str(body.get("phone", "")).strip()
        email = str(body.get("email", "")).strip()
        note = str(body.get("note", "")).strip()
        gen = int(body.get("generation", 0)) or KIN_GENERATIONS.get(rel, 3)

        eid, _ = rgraph.relate("me", rel, name, private=True, confidence=1.0, source_type="explicit_user")
        if not eid:
            raise ValueError("Could not store that family relationship")
        if note:
            rgraph.set_detail("me", rel, name, note)

        ent_module.observe(name, relationship=rel, phone=phone, email=email, note=note, source="owner")
        from ishti import events
        events.publish("memory_updated", {"type": "family", "entity": name, "action": "upsert"})

        return {"ok": True, "member": {
            "id": name.lower(),
            "name": name.title(),
            "relationship": rel,
            "generation": gen,
            "phone": phone,
            "email": email,
            "note": note,
            "first_seen": time.time(),
            "last_confirmed": time.time(),
            "confidence": 1.0,
            "source": "owner"
        }}
    except Exception as exc:
        return _learning_fail(exc)


@learning_api.delete("/api/learning/family/{name}")
async def learning_family_delete(name: str):
    """Delete a family member from rgraph and learning entities."""
    try:
        from ishti import rgraph
        from ishti.learning import entities as ent_module
        nm = name.strip()
        edges = rgraph.edges_of("me", direction="out") or []
        for e in edges:
            if (e.get("d_name") or "").lower() == nm.lower():
                rgraph.unrelate("me", e.get("rel"), e.get("d_name"))
        ent_module.remove(nm)
        from ishti import events
        events.publish("memory_updated", {"type": "family", "entity": nm, "action": "delete"})
        return {"deleted": True, "name": nm}
    except Exception as exc:
        return _learning_fail(exc)


@learning_api.get("/api/learning/sources")
async def learning_sources():
    """Where lessons came from (owner, dispatcher, skill, ...), with counts."""
    try:
        return {"sources": _learning_rows(_learning().sources(), "sources")}
    except Exception as exc:
        return _learning_fail(exc)


@learning_api.get("/api/learning/dataset")
async def learning_dataset(limit: int = 200):
    """Curated offline training examples plus stats (total/exported/by_skill)."""
    try:
        result = _learning().dataset(limit)
        stats = result.get("stats") if isinstance(result, dict) else None
        return {"dataset": _learning_rows(result, "dataset"), "stats": stats if isinstance(stats, dict) else {}}
    except Exception as exc:
        return _learning_fail(exc)


@learning_api.post("/api/learning/dataset")
async def learning_dataset_add(req: Request):
    """Manually add an example to the offline fine-tune set (dataset, never memory)."""
    try:
        body = await _learning_body(req)
        instruction = str(body.get("instruction", "")).strip()
        if not instruction:
            raise ValueError("instruction is required")
        payload = {"instruction": instruction}
        for key in ("correct", "reason", "skill"):
            if body.get(key) not in (None, ""):
                payload[key] = body[key]
        if body.get("quality") not in (None, ""):
            payload["quality"] = float(body["quality"])
        result = _learning().add_dataset(payload)
        example = result.get("example") if isinstance(result, dict) and "example" in result else result
        return {"example": example}
    except Exception as exc:
        return _learning_fail(exc)


@learning_api.delete("/api/learning/dataset/{example_id}")
async def learning_dataset_delete(example_id: int):
    """Remove one curated example."""
    try:
        result = _learning().delete_dataset(example_id)
        data = result if isinstance(result, dict) else {}
        return {"deleted": _learning_bool(data.get("deleted"), True)}
    except Exception as exc:
        return _learning_fail(exc)


@learning_api.post("/api/learning/dataset/export")
async def learning_dataset_export():
    """Write the curated set to disk for the offline LoRA/QLoRA job (eval-gated, never deployed here)."""
    try:
        api = _learning()
        fn = getattr(api, "export_dataset", None) or getattr(api, "dataset_export", None)
        result = fn() if callable(fn) else api.export("dataset")
        if isinstance(result, (list, tuple)) and len(result) == 2:
            result = {"path": result[0], "count": result[1]}
        data = result if isinstance(result, dict) else {}
        return {"path": data.get("path", ""), "count": int(data.get("count") or 0),
                "skipped": data.get("skipped") or {}}
    except Exception as exc:
        return _learning_fail(exc)


@learning_api.post("/api/learning/export")
async def learning_export(req: Request):
    """Export lessons/feedback/mistakes/dataset (or all) to the learning export directory."""
    try:
        body = await _learning_body(req)
        what = str(body.get("what", "all")).strip().lower()
        if what not in ("lessons", "feedback", "mistakes", "dataset", "all"):
            raise ValueError("what must be lessons|feedback|mistakes|dataset|all")
        result = _learning().export(what)
        if isinstance(result, (list, tuple)) and len(result) == 2:
            result = {"path": result[0], "count": result[1]}
        data = result if isinstance(result, dict) else {}
        return {"path": data.get("path", ""), "count": int(data.get("count") or 0)}
    except Exception as exc:
        return _learning_fail(exc)


@learning_api.get("/api/learning/settings")
async def learning_settings():
    """Runtime overrides from the learning DB settings table (never config.json)."""
    try:
        return _learning_settings(_learning().settings())
    except Exception as exc:
        return _learning_fail(exc)


@learning_api.post("/api/learning/settings")
async def learning_settings_update(req: Request):
    """Update runtime overrides: enabled, proactive, thresholds, evidence minimums."""
    try:
        body = await _learning_body(req)
        kw = {}
        if "enabled" in body:
            kw["enabled"] = _learning_bool(body.get("enabled"), True)
        if "proactive" in body:
            kw["proactive"] = _learning_bool(body.get("proactive"), False)
        if isinstance(body.get("thresholds"), dict):
            kw["thresholds"] = {k: float(v) for k, v in body["thresholds"].items() if isinstance(v, (int, float))}
        if isinstance(body.get("evidence"), dict):
            kw["evidence"] = {k: int(v) for k, v in body["evidence"].items() if isinstance(v, (int, float))}
        if not kw:
            raise ValueError("no settings supplied")
        return _learning_settings(_learning().update_settings(**kw))
    except Exception as exc:
        return _learning_fail(exc)


@learning_api.get("/api/learning/ask")
async def learning_ask():
    """The pending human-in-the-loop question slot (empty when proactive learning is off)."""
    try:
        result = _learning().ack_and_ask()
        data = result if isinstance(result, dict) else {}
        if not data.get("question"):
            return {"question": None}
        return {"question": data.get("question"), "candidate_id": data.get("candidate_id")}
    except Exception as exc:
        return _learning_fail(exc)


# ---- Prompt 2: memory dashboard (people/graph, conflicts, forget log, vault, backups) -------
# Same fail-soft pattern as /api/learning/*: lazy imports, {"error": ...} on trouble.
# Actions map to H4: Confirm (person), Forget (person - real deletion + audit), Export,
# Edit/Confirm/Reject/Delete stay on /api/learning/lessons/*.


@learning_api.get("/api/memory/people")
async def memory_people(limit: int = 200):
    """Person records with relationship + confidence + needs_confirmation (C2/C5)."""
    try:
        from ishti.memstore import people
        return {"people": people.list_people(limit)}
    except Exception as exc:
        return _learning_fail(exc)


@learning_api.get("/api/memory/graph")
async def memory_graph(person: str = "", limit: int = 60):
    """Relationship edges for the graph view (C6); ?person= narrows to one node."""
    try:
        from ishti import rgraph
        if person:
            edges = rgraph.edges_of(person)[:max(1, limit)]
        else:
            edges = [rgraph._dec_edge(dict(r)) for r in rgraph.conn().execute(
                "SELECT e.*, s.name AS s_name, d.name AS d_name FROM edges e "
                "JOIN nodes s ON s.id=e.src JOIN nodes d ON d.id=e.dst "
                "ORDER BY e.last_confirmed DESC LIMIT ?", (max(1, limit),)).fetchall()]
        return {"edges": [{"s": e.get("s_name"), "rel": e.get("rel"), "d": e.get("d_name"),
                           "detail": e.get("detail", ""), "confidence": e.get("confidence"),
                           "source_type": e.get("source_type"), "last_confirmed": e.get("last_confirmed")}
                          for e in edges],
                "stats": rgraph.stats(), "questions": rgraph.pending_questions()}
    except Exception as exc:
        return _learning_fail(exc)


@learning_api.get("/api/memory/conflicts")
async def memory_conflicts(limit: int = 50):
    """Contradiction records (D5) + the deletion audit log (H2: that a forget happened)."""
    try:
        from ishti.memstore import memory
        return {"conflicts": memory.conflicts(limit), "forget_audit": memory.forget_audit_log(limit)}
    except Exception as exc:
        return _learning_fail(exc)


@learning_api.post("/api/memory/conflicts/{conflict_id}")
async def memory_conflict_resolve(conflict_id: int, req: Request):
    """The owner decides a contradiction: keep=new (default) or keep=old."""
    try:
        body = await _learning_body(req)
        keep = str(body.get("keep", "new")).strip().lower()
        if keep not in ("new", "old"):
            raise ValueError("keep must be 'new' or 'old'")
        from ishti.memstore import memory
        return {"resolved": bool(memory.resolve_conflict(conflict_id, keep=keep,
                                                         note=str(body.get("note", ""))[:200]))}
    except Exception as exc:
        return _learning_fail(exc)


@learning_api.post("/api/memory/people/{name}/confirm")
async def memory_person_confirm(name: str):
    """HITL: the owner confirms who this person is (clears needs_confirmation)."""
    try:
        from ishti.memstore import people
        return {"confirmed": bool(people.confirm(name))}
    except Exception as exc:
        return _learning_fail(exc)


@learning_api.post("/api/memory/people/{name}/forget")
async def memory_person_forget(name: str, req: Request):
    """'Forget everything about X' from the dashboard: REAL deletion + audit row (H2).
    Requires {"confirm": true} - it is irreversible by design."""
    try:
        body = await _learning_body(req)
        if not _learning_bool(body.get("confirm"), False):
            raise ValueError("this permanently deletes data - send confirm=true")
        from ishti.memstore import memory
        res = memory.forget_hard(name)
        return {"forgotten": True, "memories": res["memories"], "entities": res["entities"],
                "edges": res["edges"], "audit_id": res["audit_id"]}
    except Exception as exc:
        return _learning_fail(exc)


@learning_api.get("/api/memory/vault")
async def memory_vault():
    """Encryption-at-rest status (A3)."""
    try:
        from ishti.core import vault
        return vault.status()
    except Exception as exc:
        return _learning_fail(exc)


@learning_api.post("/api/memory/vault")
async def memory_vault_toggle(req: Request):
    """Turn field encryption on/off in place ({"enabled": true|false})."""
    try:
        body = await _learning_body(req)
        from ishti.core import vault
        return vault.set_enabled(_learning_bool(body.get("enabled"), False))
    except Exception as exc:
        return _learning_fail(exc)


@learning_api.post("/api/memory/backup")
async def memory_backup():
    """Consistent online backups of both memory stores (A3)."""
    try:
        from ishti.core import vault
        from ishti.memstore import memory
        from ishti import rgraph
        return {"backups": [vault.backup(memory.db_path()), vault.backup(rgraph.DB_PATH)]}
    except Exception as exc:
        return _learning_fail(exc)


@learning_api.post("/api/memory/export")
async def memory_export(req: Request):
    """Portable decrypted export ZIP of memories + entities + graph (A3/H4 Export)."""
    try:
        body = await _learning_body(req)
        from ishti.core import vault
        return {"path": vault.export_all(include_private=_learning_bool(body.get("include_private"), True))}
    except Exception as exc:
        return _learning_fail(exc)


@learning_api.post("/api/privacy/export-all")
async def privacy_export_all():
    """One ZIP of everything ISHTI keeps (every store, the chats, readable memories), locked to the owner."""
    try:
        from ishti.core import vault
        return {"path": await asyncio.to_thread(vault.export_everything)}
    except Exception as exc:
        return _learning_fail(exc)


@learning_api.get("/api/memory/training-dry-run")
async def memory_training_dry_run():
    """G3/G4: the offline pipeline's dry-run report (no training, no deployment)."""
    try:
        import orchestrator.finetune as FT
        return FT.dry_run()
    except Exception as exc:
        return _learning_fail(exc)


# ---- Skills registry, tasks, telemetry, leads ---------------
p3_api = APIRouter()


@p3_api.get("/api/skills/registry")
async def skills_registry(q: str = "", domain: str = "", k: int = 20):
    """The Skill Registry: records (optionally retrieved for a request) + enable/disable flags."""
    try:
        from ishti.skills import registry as REG, load_skills
        skills = load_skills()
        if q:
            win = REG.retrieve(q, skills, k=k, domain=domain or None, with_scores=True)
            return {"window": win, "describe": REG.describe(skills)}
        return {"records": list(REG.records(skills).values()), "describe": REG.describe(skills)}
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)


@p3_api.post("/api/skills/{name}/enabled")
async def skill_set_enabled(name: str, req: Request):
    """Enable/disable one skill (?domain=1 for a whole domain)."""
    try:
        body = await _learning_body(req)
        from ishti.skills import registry as REG
        return REG.set_enabled(name, _learning_bool(body.get("enabled"), True),
                               is_domain=_learning_bool(body.get("domain"), False))
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)


@p3_api.get("/api/tasks")
async def tasks_list(status: str = ""):
    """Background task records (D4)."""
    try:
        from ishti import taskmgr
        return {"tasks": taskmgr.list_tasks(status or None), "stats": taskmgr.stats()}
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)


@p3_api.post("/api/tasks/{task_id}/{action}")
async def task_action(task_id: str, action: str):
    """pause | resume | cancel a background task."""
    try:
        from ishti import taskmgr
        fn = {"pause": taskmgr.pause, "resume": taskmgr.resume, "cancel": taskmgr.cancel}.get(action)
        if not fn:
            raise ValueError("action must be pause|resume|cancel")
        return {"done": bool(fn(task_id))}
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)


@p3_api.get("/api/metrics")
async def metrics_snapshot():
    """H2: per-stage percentiles, counters, tokens, agents, queue, system."""
    try:
        from ishti import telemetry
        return telemetry.metrics()
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)


@p3_api.get("/api/telemetry/spans")
async def telemetry_spans(limit: int = 100, name: str = "", status: str = ""):
    """H1: recent spans (filterable) for the observability view."""
    try:
        from ishti import telemetry
        return {"spans": telemetry.recent_spans(limit, name or "", status or "")}
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)


@p3_api.get("/api/telemetry/errors")
async def telemetry_errors(limit: int = 50):
    try:
        from ishti import telemetry
        return {"errors": telemetry.errors(limit)}
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)


@p3_api.post("/api/eval/run")
async def eval_run():
    """H5: run the fixed regression set on demand; results stored under data/eval/."""
    try:
        from ishti import telemetry
        return telemetry.run_eval()
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)


@p3_api.get("/api/leads")
async def leads_list(q: str = "", verified: int = 0, limit: int = 200):
    try:
        import ishti.leads as L
        return {"leads": L.rows(q, limit=limit, verified_only=bool(verified)), "stats": L.stats()}
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)


@p3_api.post("/api/leads/export")
async def leads_export(req: Request):
    try:
        body = await _learning_body(req)
        import ishti.leads as L
        return {"path": L.export(fmt=str(body.get("fmt", "csv")), q=str(body.get("q", "")))}
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)


app.include_router(p3_api)  # /api/skills/* /api/tasks /api/metrics /api/telemetry/* /api/eval /api/leads
app.include_router(learning_api)  # /api/learning/* + /api/memory/* - learning & memory dashboards

from ishti import files_api  # /api/upload, /api/reveal
files_api.attach(app)

# ---------------------------------------------------------------- Media Vault & CRM Endpoints
_VAULT_APPROVALS = {}
_VAULT_APPROVALS_LOCK = threading.RLock()
_VAULT_APPROVAL_MAX = 256


def _vault_sweep(now):
    # Exact expiry is invalid. Bound live approvals as well as expired ones (fail closed).
    for token, row in list(_VAULT_APPROVALS.items()):
        if row["expires"] <= now:
            _VAULT_APPROVALS.pop(token, None)
    while len(_VAULT_APPROVALS) >= _VAULT_APPROVAL_MAX:
        _VAULT_APPROVALS.pop(next(iter(_VAULT_APPROVALS)))


def _vault_target(body):
    target = body.get("chat_id") if isinstance(body, dict) else None
    return str(target).strip() if target is not None and str(target).strip() else "all"


def _vault_prepare(target):
    token = secrets.token_urlsafe(24)
    with _VAULT_APPROVALS_LOCK:
        now = time.monotonic()
        _vault_sweep(now)
        _VAULT_APPROVALS[token] = {"target": target, "expires": now + 120.0}
    return {"approval_token": token, "target": target, "expires_in_s": 120}


def _vault_consume(target, token):
    with _VAULT_APPROVALS_LOCK:
        now = time.monotonic()
        row = _VAULT_APPROVALS.pop(str(token or ""), None)
        _vault_sweep(now)
    if not row or row["target"] != target or row["expires"] <= now:
        return False
    return True


@app.get("/api/vault/status")
async def vault_status():
    """Encrypted-at-rest media vault metrics and retention policy."""
    try:
        from ishti.security import media_vault
        return media_vault.get_vault_stats()
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@app.post("/api/vault/purge/prepare")
async def vault_purge_prepare(req: Request):
    try:
        body = await req.json()
        return _vault_prepare(_vault_target(body))
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)


@app.post("/api/vault/purge")
async def vault_purge(req: Request):
    """Purge media files from the encrypted vault."""
    try:
        body = await req.json() if req.headers.get("content-type") == "application/json" else {}
        target = _vault_target(body)
        if not _vault_consume(target, body.get("approval_token")):
            return JSONResponse({"error": "exact vault purge approval required or expired"}, status_code=409)
        from ishti.security import media_vault
        if target != "all":
            count = media_vault.purge_chat_media(target)
        else:
            count = media_vault.purge_all_media()
        return {"ok": True, "purged": count}
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@app.post("/api/vault/cleanup")
async def vault_cleanup():
    """Trigger background expired media scrubber."""
    try:
        from ishti.security import media_vault
        count = media_vault.cleanup_expired_media()
        return {"ok": True, "cleaned": count}
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@app.get("/api/crm/clients")
async def crm_clients(status: Optional[str] = None):
    """List business CRM clients adhering to the separation contract."""
    try:
        from ishti import crm_contract
        clients = [{**row, "id": row["client_id"], "name": row["contact_name"],
                    "source": "contract_crm"} for row in crm_contract.list_clients(status=status)]
        chat_store_error = None
        try:
            from ishti.skills.clients.skill import list_profiles
            clients.extend(row for row in list_profiles() if not status or row["status"] == status)
        except Exception:
            chat_store_error = "Chat-created client profiles are temporarily unavailable"
        return {"ok": True, "clients": clients, "count": len(clients),
                "partial_error": chat_store_error,
                "separate_personal": crm_contract.SEPARATE_PERSONAL_AND_BUSINESS}
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@app.post("/api/crm/clients")
async def crm_create_client(req: Request):
    """Explicitly create a new business client."""
    try:
        body = await req.json()
        from ishti import crm_contract
        rec = crm_contract.create_client(
            name=body.get("name", ""),
            company=body.get("company", ""),
            channel_source=body.get("channel_source", "manual"),
            phone=body.get("phone", ""),
            email=body.get("email", ""),
            status=body.get("status", "lead"),
            financial_terms=body.get("financial_terms", ""),
            notes=body.get("notes", ""),
            allow_auto=True,
        )
        return {"ok": True, "client": rec}
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)


@app.post("/api/crm/export")
async def crm_export(req: Request):
    """Export CRM client records."""
    try:
        body = await req.json() if req.headers.get("content-type") == "application/json" else {}
        fmt = body.get("fmt", "json")
        from ishti import crm_contract
        path = crm_contract.export_crm(fmt=fmt)
        return {"ok": True, "path": path}
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@app.post("/api/crm/delete/prepare")
async def crm_delete_prepare(req: Request):
    from ishti import crm_contract
    try:
        body = await req.json()
        return crm_contract.prepare_delete(body.get("client_id"))
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=409)


@app.post("/api/crm/delete")
async def crm_delete(req: Request):
    """Delete a CRM client or purge CRM data."""
    try:
        body = await req.json()
        from ishti import crm_contract
        client_id = body.get("client_id")
        if client_id:
            record = crm_contract.get_client(client_id)
            if not record or str(client_id) != record.get("client_id"):
                return JSONResponse({"error": "An exact contract CRM record ID is required"}, status_code=400)
            if body.get("confirm_name") != record.get("contact_name"):
                return JSONResponse({"error": "Confirm the exact client name before deletion"}, status_code=409)
            try:
                ok = crm_contract.delete_approved(client_id, body.get("confirm_name"), body.get("approval_token"))
            except PermissionError as exc:
                return JSONResponse({"error": str(exc)}, status_code=409)
            return {"ok": ok}
        purge_all = body.get("purge_all", False)
        if purge_all:
            return JSONResponse({"error": "Bulk CRM purge is deferred until a bound, one-shot approval flow exists"},
                                status_code=501)
        return JSONResponse({"error": "client_id or purge_all required"}, status_code=400)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)

from ishti import lifecycle_api  # noqa: E402  /api/ready, /api/vram, /api/shutdown, /ws/events (docs/API_CONTRACT.md)
lifecycle_api.attach(app)

from ishti import privacy_api  # noqa: E402  /api/privacy: outbound ledger + Internet switch + egress guard (docs/SAFETY.md)
privacy_api.attach(app)

from ishti import version as product_version  # noqa: E402  /api/version: version + source revision (ishti/version.py)


@app.get("/api/version")
async def get_version():
    """The product version and the source hash this process was started from. No machine paths leave here."""
    return product_version.info()


from ishti.channels import api as remote_api  # noqa: E402  /api/remote/* + channel boot/stop (docs/API_CONTRACT.md §11)
remote_api.attach(app)

from ishti import contract_api  # noqa: E402  Contract v2 §14: chats/runs/feedback/profiles/connectors/agents/learning (docs/API_CONTRACT.md)
contract_api.attach(app)


def start_server(port: int = 8888):
    from ishti import lifecycle
    lifecycle._st["port"] = port
    print(f"\n[ISHTI Server] Launching Web UI on http://127.0.0.1:{port}")
    lifecycle.serve(app, port)  # uvicorn with a handle the graceful shutdown can stop


if __name__ == "__main__":
    start_server()
