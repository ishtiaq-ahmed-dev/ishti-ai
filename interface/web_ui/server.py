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
from typing import Dict, Any, List
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

ROOT_DIR = r"D:\ISHTI"
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from orchestrator.model_router import ModelRouter
from orchestrator.planner import Planner
from orchestrator.crew import ExecutionEngine
from orchestrator.doctor import IshtiDoctor
from orchestrator.memory.conversation import get_conversation_manager
from orchestrator.memory.error_logger import ErrorLogger
from voice.tts import speak, speakable
from orchestrator.memory.user_profile import get_user_memory
from ishti.dispatcher import api as ishti_api, handle as ishti_handle, HOOKS as ISHTI_HOOKS

app = FastAPI(title="ISHTI Personal AI Assistant")
app.include_router(ishti_api)  # /api/chat, /api/skills, /api/facts (the dispatcher)

STATIC_DIR = os.path.join(ROOT_DIR, "interface", "web_ui", "static")
TEMPLATES_DIR = os.path.join(ROOT_DIR, "interface", "web_ui", "templates")
SKILLS_DIR = os.path.join(ROOT_DIR, "orchestrator", "memory", "skills")
REFLECTIONS_PATH = os.path.join(ROOT_DIR, "orchestrator", "memory", "reflections.md")
RESEARCH_MEM_DIR = os.path.join(ROOT_DIR, "orchestrator", "memory", "research")

os.makedirs(STATIC_DIR, exist_ok=True)
os.makedirs(TEMPLATES_DIR, exist_ok=True)
os.makedirs(RESEARCH_MEM_DIR, exist_ok=True)

# Mount static files
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# Shared state
model_router = ModelRouter()
planner = Planner()
active_websockets: List[WebSocket] = []
pending_confirmations: Dict[str, asyncio.Event] = {}
confirmation_decisions: Dict[str, bool] = {}
last_execution_state: Dict[str, Any] = {}
last_research_state: Dict[str, Any] = {}
MAIN_LOOP: asyncio.AbstractEventLoop = None  # set at startup; worker threads have no loop of their own


@app.on_event("startup")
async def _capture_loop():
    global MAIN_LOOP
    MAIN_LOOP = asyncio.get_running_loop()


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


def confirmation_gate_ui(task: Dict[str, Any]) -> bool:
    """Blocks until user clicks Approve or Reject on the Web UI modal, or times out."""
    task_id = task.get("id", "step_gate")
    event = asyncio.Event()
    pending_confirmations[task_id] = event

    # Broadcast to UI
    sync_event_callback("consequential_modal_open", {
        "task_id": task_id,
        "name": task.get("name", "Consequential Action"),
        "details": task.get("consequential_details", ""),
    })

    # Speak alert
    speak("Confirmation required for consequential action. Please review the draft on your screen.", wait=False)

    # No answer, timeout or error = deny. A consequential action must never self-approve.
    try:
        asyncio.run_coroutine_threadsafe(event.wait(), MAIN_LOOP).result(timeout=60)
        approved = confirmation_decisions.get(task_id, False)
    except Exception:
        approved = False

    pending_confirmations.pop(task_id, None)
    confirmation_decisions.pop(task_id, None)
    return approved


engine = ExecutionEngine(
    confirmation_callback=confirmation_gate_ui,
    event_callback=sync_event_callback,
)
ISHTI_HOOKS.update(confirm=confirmation_gate_ui, emit=sync_event_callback, engine=engine)


@app.get("/", response_class=HTMLResponse)
async def index():
    html_file = os.path.join(TEMPLATES_DIR, "index.html")
    if os.path.exists(html_file):
        with open(html_file, "r", encoding="utf-8") as f:
            return HTMLResponse(f.read())
    return HTMLResponse("<h1>ISHTI Interface Loading...</h1>")


@app.get("/api/status")
async def get_status():
    installed = model_router.get_installed_models()
    primary = model_router.get_model("primary")
    fast = model_router.get_model("fast")
    fallback = model_router.get_model("fallback")

    skills = []
    if os.path.exists(SKILLS_DIR):
        for fname in os.listdir(SKILLS_DIR):
            if fname.endswith(".json"):
                try:
                    with open(os.path.join(SKILLS_DIR, fname), "r", encoding="utf-8") as f:
                        skills.append(json.load(f))
                except Exception:
                    pass

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
    global last_execution_state, last_research_state
    body = await req.json()
    prompt = body.get("prompt", "").strip()
    session_id = body.get("session_id", "").strip() or None
    if not prompt:
        return JSONResponse({"error": "Empty prompt"}, status_code=400)

    loop = asyncio.get_event_loop()

    def run_sync():
        res = ishti_handle(prompt, session_id=session_id)
        # Speak the whole answer, not just its first line, when audio output is enabled.
        answer = res.get("final_answer", "")
        if answer and get_user_memory().get_preferences().audio_output:
            from ishti.voice import speak as piper_speak
            piper_speak(answer)
            res["spoken"] = True
        return res

    try:
        result = await loop.run_in_executor(None, run_sync)
    except Exception as exc:
        ErrorLogger.log_error(subsystem="SERVER", error=exc, user_prompt=prompt, context="execute_task endpoint")
        result = {"error": str(exc), "final_answer": f"Server error: {str(exc)[:200]}"}

    last_execution_state = result
    if result.get("mode") == "DEEP_RESEARCH":
        last_research_state = result

    # Broadcast stream_end with full payload for synchronized UI completion
    await broadcast_event("task_completed_full", result)

    return JSONResponse(result)


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


@app.post("/api/confirm_action")
async def confirm_action(req: Request):
    body = await req.json()
    task_id = body.get("task_id")
    approved = body.get("approved", False)

    if task_id in pending_confirmations:
        confirmation_decisions[task_id] = approved
        pending_confirmations[task_id].set()
        return JSONResponse({"status": "resolved", "task_id": task_id, "approved": approved})
    return JSONResponse({"status": "not_found", "task_id": task_id}, status_code=404)


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
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
