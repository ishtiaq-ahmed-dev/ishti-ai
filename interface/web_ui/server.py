"""
ISHTI Web UI Server & Real-Time Orchestration Bridge
FastAPI + WebSocket server providing the futuristic JARVIS/ISHTI HUD interface,
real-time token streaming, multi-page execution & research views,
human-in-the-loop action approvals, and live system diagnostics.
"""

import os
import sys
import json
import asyncio
import secrets
from typing import Dict, Any, List
from fastapi import APIRouter, FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse, FileResponse, Response
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

app = FastAPI(title="ISHTI Personal AI Assistant")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(ishti_api)  # /api/chat, /api/skills, /api/facts (the dispatcher)

app.add_middleware(SelectiveGZipMiddleware)  # 1.02 MB JS -> ~290 KB, 107 KB CSS -> ~16 KB on the wire

# Remote access (Cloudflare tunnel, remote/tunnel_setup.md) arrives from cloudflared on 127.0.0.1,
# so "is it localhost?" is not enough: anything proxied or non-local must carry the token.
TOKEN_PATH = os.path.join(ROOT_DIR, "data", "api_token")
PROXY_HEADERS = ("cf-connecting-ip", "x-forwarded-for", "forwarded", "x-real-ip")


def _api_token() -> str:
    if not os.path.exists(TOKEN_PATH):
        os.makedirs(os.path.dirname(TOKEN_PATH), exist_ok=True)
        with open(TOKEN_PATH, "w") as f:
            f.write(secrets.token_urlsafe(32))
    with open(TOKEN_PATH) as f:
        return f.read().strip()


API_TOKEN = _api_token()


def authorized(conn) -> bool:
    """conn: Request or WebSocket. Local, unproxied = trusted; otherwise token via header, cookie or ?token=."""
    if conn.client and conn.client.host in ("127.0.0.1", "::1") and not any(h in conn.headers for h in PROXY_HEADERS):
        return True
    supplied = (conn.headers.get("authorization", "").removeprefix("Bearer ").strip()
                or conn.cookies.get("ishti_token") or conn.query_params.get("token") or "")
    return secrets.compare_digest(supplied, API_TOKEN)


@app.middleware("http")
async def require_token_for_remote(request: Request, call_next):
    if not authorized(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    response = await call_next(request)
    if request.query_params.get("token"):  # open https://<tunnel>/?token=... once; the cookie does the rest
        response.set_cookie("ishti_token", API_TOKEN, httponly=True, secure=True, samesite="strict")
    return response

STATIC_DIR = os.path.join(ROOT_DIR, "interface", "web_ui", "static")
TEMPLATES_DIR = os.path.join(ROOT_DIR, "interface", "web_ui", "templates")
REFLECTIONS_PATH = os.path.join(ROOT_DIR, "orchestrator", "memory", "reflections.md")
RESEARCH_MEM_DIR = os.path.join(ROOT_DIR, "orchestrator", "memory", "research")

os.makedirs(STATIC_DIR, exist_ok=True)
os.makedirs(TEMPLATES_DIR, exist_ok=True)
os.makedirs(RESEARCH_MEM_DIR, exist_ok=True)

# Mount static files (the old HUD's own assets: short cache - they are not content-hashed)
app.mount("/static", CachedStaticFiles(directory=STATIC_DIR, max_age=300), name="static")

# Shared state
model_router = ModelRouter()
active_websockets: List[WebSocket] = []
last_execution_state: Dict[str, Any] = {}
last_research_state: Dict[str, Any] = {}
MAIN_LOOP: asyncio.AbstractEventLoop = None  # set at startup; worker threads have no loop of their own


@app.on_event("startup")
async def _capture_loop():
    global MAIN_LOOP
    MAIN_LOOP = asyncio.get_running_loop()
    from ishti import CONFIG
    from ishti.skills.reminders.skill import start as start_reminders

    def announce(msg, kind="job"):  # due reminders: HUD always, spoken unless the owner muted audio
        sync_event_callback("assistant_message", {"reply": "⏰ " + msg})
        text = str(msg or "").strip()
        # persistent bell entry (survives restarts); the announced prefix tells reminders/timers apart
        notifications_store.add("reminder" if text.lower().startswith(("reminder", "timer")) else kind, text)
        if get_user_memory().get_preferences().audio_output:
            from ishti.voice import speak as piper_speak
            piper_speak(msg)

    # scheduled tasks ("every morning at 8 give me my brief") run through the dispatcher; the answer is announced
    start_reminders(announce, run_task=lambda t: ishti_handle(t, session_id="scheduled", channel="scheduled")["final_answer"])
    from ishti.skills.automation.skill import start as start_automations
    # "when a pdf appears in downloads, summarize it": the action runs like a scheduled task, its answer is announced
    start_automations(announce, run_task=lambda t: ishti_handle(t, session_id="scheduled", channel="scheduled")["final_answer"])
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


async def broadcast_event(event_type: str, data: Dict[str, Any]):
    message = json.dumps({"type": event_type, "data": data}, ensure_ascii=False)
    disconnected = []
    for ws in list(active_websockets):
        try:
            await ws.send_text(message)
        except Exception:
            disconnected.append(ws)
    for ws in disconnected:
        if ws in active_websockets:
            active_websockets.remove(ws)


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
    """The new app when it's built; the old HUD otherwise (still reachable at /hud)."""
    cached = _built_index()
    if not cached:
        return await hud()
    headers = {"Cache-Control": "no-cache", "ETag": cached["etag"]}  # revalidate -> 304, never stale
    if request.headers.get("if-none-match") == cached["etag"]:
        return Response(status_code=304, headers=headers)
    return HTMLResponse(cached["html"], headers=headers)


@app.get("/hud", response_class=HTMLResponse)
async def hud():
    with open(os.path.join(TEMPLATES_DIR, "index.html"), "r", encoding="utf-8") as f:
        return HTMLResponse(f.read())


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


@app.get("/api/health")
async def health_endpoint():
    """Liveness for the watchdog (answering at all = not hung) + the latest health-loop snapshot."""
    from ishti import health, lifecycle
    try:  # remote-control channels (docs/API_CONTRACT.md §11): running/stopped, never raises here
        from ishti.channels import service as _remote_service
        remote = _remote_service.get().health()
    except Exception:
        remote = {"available": False}
    return {"ok": True, "state": lifecycle.state(), "remote": remote, **health.snapshot()}


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
        "browser_profile": os.path.join(ROOT_DIR, "data", "chrome_profile"),
        "isolation_verified": True,
        "skills_count": len(skills),
        "skills": skills,
        "last_execution": last_execution_state,
        "last_research": last_research_state,
        "hardware": {
            "gpu": "NVIDIA GeForce RTX 5070 Ti (16GB GDDR7)",
            "cpu": "Intel Core Ultra 9 (24 Cores + NPU)",
            "ram": "64GB DDR5",
        },
    }


@app.get("/api/doctor")
async def get_doctor():
    loop = asyncio.get_event_loop()
    doc = IshtiDoctor()
    report = await loop.run_in_executor(None, doc.check_all)
    return JSONResponse(report)


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
                    pass
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


# ---- Architecture control (ARCH1 dispatcher vs ARCH2 two-pass brain) -------------------------
@app.get("/api/arch")
async def arch_state():
    """Which pipeline is active + the memory store's live stats (the dashboard's Architecture card)."""
    from ishti.arch2 import registry
    out = registry.describe()
    try:
        from ishti.arch2 import memory as amem
        out["memory"] = amem.stats()
    except Exception as exc:
        out["memory"] = {"error": str(exc)}
    try:
        from ishti.arch2 import pipeline as apipe
        from ishti import llm
        out["models"] = {"main": llm.model_for("main"), "validation": llm.model_for("validation")}
        out["tool_rounds"] = apipe.MAX_TOOL_ROUNDS
    except Exception:
        out["models"] = {}
    return JSONResponse(out)


@app.post("/api/arch/switch")
async def arch_switch(req: Request):
    """{"backend": "arch1" | "arch2"} - the Settings -> Architecture card's switch."""
    from ishti.arch2 import registry
    body = await req.json()
    try:
        d = registry.set_active(str(body.get("backend", "")), by="dashboard")
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    await broadcast_event("arch_state", d)
    return JSONResponse(d)


@app.post("/api/arch/memory/promote")
async def arch_memory_promote():
    """Run the observation->semantic promotion now (normally automatic at the thresholds)."""
    from ishti.arch2 import memory as amem
    promoted = amem.promote_observations()
    return JSONResponse({"promoted": len(promoted), "items": [p["content"] for p in promoted]})


@app.post("/api/arch/memory/decay")
async def arch_memory_decay():
    """Run a decay pass now: stale low-importance memories are marked needs_confirmation (never deleted)."""
    from ishti.arch2 import memory as amem
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
            pass
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
    body = await req.json()
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
    import queue as queue_mod
    import threading
    import numpy as np
    from ishti import wake
    from ishti.talk import RemoteTalker
    if not authorized(ws):
        await ws.close(code=1008)
        return
    await ws.accept()
    session_id = ws.query_params.get("session") or None
    loop = asyncio.get_running_loop()
    frames, stop = queue_mod.Queue(), threading.Event()
    from ishti.voice import stt_model
    threading.Thread(target=stt_model, daemon=True).start()  # load Whisper while the owner starts talking (was ~2.3 s on turn 1)
    if LIVE["stop"]:
        LIVE["stop"].set()
    LIVE["stop"] = stop

    def send(kind, data):  # called from worker threads
        if not stop.is_set():
            asyncio.run_coroutine_threadsafe(ws.send_text(json.dumps({"type": kind, "data": data}, default=str)), loop)

    def send_audio(msg):  # the reply's voice plays in the browser: binary = 16-bit PCM, dict = format/stop
        if stop.is_set():
            return
        coro = ws.send_bytes(msg) if isinstance(msg, bytes) else ws.send_text(json.dumps(msg))
        asyncio.run_coroutine_threadsafe(coro, loop)
    voice = RemoteTalker(send_audio)
    from ishti import killswitch
    killswitch.on_kill(lambda: voice.stop() and "Live voice")  # the browser stops talking too

    def on_command(text, on_token=None):
        res = ishti_handle(text, session_id=session_id, channel="voice", on_token=on_token,
                           on_event=lambda ev: send("trace", ev))
        send("reply", {"user": text, "reply": res["final_answer"], "session_id": res.get("session_id")})
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
    except WebSocketDisconnect:
        pass
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
            try:
                q.get_nowait()
            except Exception:
                pass
        try:
            q.put_nowait(m)
        except Exception:
            pass

    def push(m):
        loop.call_soon_threadsafe(_put, m)

    events.subscribe(push)
    recv_task = None
    try:
        await websocket.send_text(json.dumps({
            "type": "connected",
            "data": {"message": "ISHTI Neural Core Connected."}
        }))
        for m in events.replay():
            await websocket.send_text(json.dumps(m, ensure_ascii=False, default=str))

        recv_task = asyncio.ensure_future(websocket.receive_text())
        while True:
            get_task = asyncio.ensure_future(q.get())
            done, _ = await asyncio.wait({recv_task, get_task}, return_when=asyncio.FIRST_COMPLETED)
            if get_task in done:
                event_msg = get_task.result()
                await websocket.send_text(json.dumps(event_msg, ensure_ascii=False, default=str))
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
                            await websocket.send_text(json.dumps({"type": "task_completed_full", "data": res}))
                except Exception as e:
                    print(f"[WS Error]: {e}")
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        if recv_task and not recv_task.done():
            recv_task.cancel()
        events.unsubscribe(push)
        if websocket in active_websockets:
            active_websockets.remove(websocket)




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
        from ishti.arch2 import people
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
        from ishti.arch2 import memory
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
        from ishti.arch2 import memory
        return {"resolved": bool(memory.resolve_conflict(conflict_id, keep=keep,
                                                         note=str(body.get("note", ""))[:200]))}
    except Exception as exc:
        return _learning_fail(exc)


@learning_api.post("/api/memory/people/{name}/confirm")
async def memory_person_confirm(name: str):
    """HITL: the owner confirms who this person is (clears needs_confirmation)."""
    try:
        from ishti.arch2 import people
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
        from ishti.arch2 import memory
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
        from ishti.arch2 import memory
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


@learning_api.get("/api/memory/training-dry-run")
async def memory_training_dry_run():
    """G3/G4: the offline pipeline's dry-run report (no training, no deployment)."""
    try:
        import orchestrator.finetune as FT
        return FT.dry_run()
    except Exception as exc:
        return _learning_fail(exc)


# ---- Prompt 3: skills registry, multi-agent, tasks, telemetry, leads, flowise ---------------
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
        from ishti.skills import registry as REG, load_skills
        REG.reset_cache()
        return REG.set_enabled(name, _learning_bool(body.get("enabled"), True),
                               is_domain=_learning_bool(body.get("domain"), False))
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)


@p3_api.get("/api/agents")
async def agents_overview():
    """Agent roster + live performance (C2/C8)."""
    try:
        import ishti.agents as A
        return {"agents": {k: {"goal": v["goal"], "skills": v["skills"]} for k, v in A.AGENTS.items()},
                "perf": A.perf(), "budget": A._budget()}
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


@p3_api.get("/api/flowise/flows")
async def flowise_flows():
    """E1: configured? + the chatflows available for intent binding."""
    try:
        from ishti import flowise_client as FC
        ok, detail = FC.health()
        return {"configured": FC.configured(), "health": detail, "flows": FC.list_flows()}
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)


app.include_router(p3_api)  # /api/skills/* /api/agents /api/tasks /api/metrics /api/telemetry/* /api/eval /api/leads /api/flowise
app.include_router(learning_api)  # /api/learning/* + /api/memory/* - learning & memory dashboards

from ishti import lifecycle_api  # noqa: E402  /api/ready, /api/vram, /api/shutdown, /ws/events (docs/API_CONTRACT.md)
lifecycle_api.attach(app)

from ishti import privacy_api  # noqa: E402  /api/privacy: outbound ledger + Internet switch + egress guard (docs/SAFETY.md)
privacy_api.attach(app)

from ishti.channels import api as remote_api  # noqa: E402  /api/remote/* + channel boot/stop (docs/API_CONTRACT.md §11)
remote_api.attach(app)


def start_server(port: int = 8888):
    from ishti import lifecycle
    lifecycle._st["port"] = port
    print(f"\n[ISHTI Server] Launching Web UI on http://127.0.0.1:{port}")
    lifecycle.serve(app, port)  # uvicorn with a handle the graceful shutdown can stop


if __name__ == "__main__":
    start_server()
