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
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse, FileResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

ROOT_DIR = r"D:\ISHTI"
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from orchestrator.model_router import ModelRouter
from orchestrator.doctor import IshtiDoctor
from orchestrator.memory.conversation import get_conversation_manager
from orchestrator.memory.error_logger import ErrorLogger
from voice.tts import speak, speakable
from orchestrator.memory.user_profile import get_user_memory
from ishti.dispatcher import api as ishti_api, handle as ishti_handle, HOOKS as ISHTI_HOOKS

app = FastAPI(title="ISHTI Personal AI Assistant")
app.include_router(ishti_api)  # /api/chat, /api/skills, /api/facts (the dispatcher)

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

# Mount static files
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

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

    def announce(msg):  # due reminders: HUD always, spoken unless the owner muted audio
        sync_event_callback("assistant_message", {"reply": "⏰ " + msg})
        if get_user_memory().get_preferences().audio_output:
            from ishti.voice import speak as piper_speak
            piper_speak(msg)

    start_reminders(announce)
    if CONFIG["voice"].get("wake_enabled"):
        from ishti import wake

        def on_voice_command(text):
            res = ishti_handle(text, channel="voice")
            sync_event_callback("assistant_message", {"user": text, "reply": res["final_answer"],
                                                      "sources": res.get("sources", []), "channel": "voice"})
            return res["final_answer"]

        wake.start(on_voice_command)


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
    app.mount("/assets", StaticFiles(directory=os.path.join(APP_DIST, "assets")), name="app-assets")


@app.get("/favicon.ico")
@app.get("/favicon.svg")
@app.get("/favicon.png")
@app.get("/ishti-icon.svg")
@app.get("/ishti-icon.png")
@app.get("/ishti-logo.svg")
@app.get("/ishti-logo.png")
async def get_favicons_and_brand_assets(request: Request):
    """Serve favicons and brand logos with correct media types."""
    filename = request.url.path.lstrip("/")
    search_dirs = [
        APP_DIST,
        os.path.join(ROOT_DIR, "interface", "app", "public"),
        STATIC_DIR,
        os.path.join(ROOT_DIR, "assets"),
    ]
    for d in search_dirs:
        candidate = os.path.join(d, filename)
        if os.path.isfile(candidate):
            media = "image/svg+xml" if filename.endswith(".svg") else "image/png" if filename.endswith(".png") else "image/x-icon"
            return FileResponse(candidate, media_type=media)
    # Fallback if specific file missing
    for fallback in ["ishti-icon.svg", "favicon.svg", "favicon.png", "ishti-logo.png"]:
        for d in search_dirs:
            candidate = os.path.join(d, fallback)
            if os.path.isfile(candidate):
                media = "image/svg+xml" if fallback.endswith(".svg") else "image/png"
                return FileResponse(candidate, media_type=media)
    return JSONResponse({"error": "not found"}, status_code=404)


@app.get("/", response_class=HTMLResponse)
async def index():
    """The new app when it's built; the old HUD otherwise (still reachable at /hud)."""
    built = os.path.join(APP_DIST, "index.html")
    return await hud() if not os.path.exists(built) else HTMLResponse(open(built, encoding="utf-8").read())


@app.get("/hud", response_class=HTMLResponse)
async def hud():
    with open(os.path.join(TEMPLATES_DIR, "index.html"), "r", encoding="utf-8") as f:
        return HTMLResponse(f.read())


@app.get("/api/status")
async def get_status():
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
        "browser_profile": r"D:\ISHTI\data\chrome_profile",
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


def _execute(prompt, session_id, on_token=None, on_event=None) -> dict:
    """Dispatcher call shared by /api/execute and /api/execute_stream (runs in a worker thread)."""
    global last_execution_state, last_research_state
    try:
        res = ishti_handle(prompt, session_id=session_id, on_token=on_token, on_event=on_event)
        # Speak the whole answer, not just its first line, when audio output is enabled.
        answer = res.get("final_answer", "")
        if answer and get_user_memory().get_preferences().audio_output:
            from ishti.voice import speak as piper_speak
            piper_speak(answer)
            res["spoken"] = True
    except Exception as exc:
        ErrorLogger.log_error(subsystem="SERVER", error=exc, user_prompt=prompt, context="execute endpoint")
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
        result = job.result()
        await broadcast_event("task_completed_full", result)
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
    speak("Model re-benchmarking complete. Optimal models selected.", wait=False)
    return JSONResponse({"status": "success", "scores": scores})


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    if not authorized(websocket):
        await websocket.close(code=1008)
        return
    await websocket.accept()
    active_websockets.append(websocket)
    try:
        await websocket.send_text(json.dumps({
            "type": "connected",
            "data": {"message": "ISHTI Neural Core Connected."}
        }))
        while True:
            raw_text = await websocket.receive_text()
            try:
                msg = json.loads(raw_text)
                if msg.get("action") == "execute":
                    prompt = msg.get("prompt", "")
                    if prompt:
                        loop = asyncio.get_event_loop()
                        def run_sync():
                            return ishti_handle(prompt)
                        res = await loop.run_in_executor(None, run_sync)
                        await websocket.send_text(json.dumps({"type": "task_completed_full", "data": res}))
            except Exception as e:
                print(f"[WS Error]: {e}")
    except WebSocketDisconnect:
        if websocket in active_websockets:
            active_websockets.remove(websocket)


def start_server(port: int = 8888):
    print(f"\n[ISHTI Server] Launching Web UI on http://127.0.0.1:{port}")
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    start_server()
