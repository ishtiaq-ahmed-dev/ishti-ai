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
    health.start(announce)  # A1: degrading components are flagged before they fail
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
    return {"ok": True, "state": lifecycle.state(), **health.snapshot()}


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


app.include_router(learning_api)  # /api/learning/* - learning dashboard (see docs/LEARNING_INTERFACE.md)

from ishti import lifecycle_api  # noqa: E402  /api/ready, /api/vram, /api/shutdown, /ws/events (docs/API_CONTRACT.md)
lifecycle_api.attach(app)


def start_server(port: int = 8888):
    from ishti import lifecycle
    lifecycle._st["port"] = port
    print(f"\n[ISHTI Server] Launching Web UI on http://127.0.0.1:{port}")
    lifecycle.serve(app, port)  # uvicorn with a handle the graceful shutdown can stop


if __name__ == "__main__":
    start_server()
