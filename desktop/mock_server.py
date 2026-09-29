"""ISHTI Minimal Mock Contract Server.
Adheres strictly to docs/API_CONTRACT.md for isolated testing of the Desktop Shell,
Animated Splash Screen, Model Switch Toast, VRAM Chip, and Graceful Shutdown.
"""

import asyncio
import json
import os
import sys
import time
from typing import List
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import JSONResponse
import uvicorn

app = FastAPI(title="ISHTI Contract Mock Server")

RAW_STAGES = [
    ("backend_up", "Starting ISHTI"),
    ("ollama_ready", "Waking up the AI engine"),
    ("memory_loaded", "Loading your memory"),
    ("skills_loaded", "Loading skills"),
    ("planner_warm", "Warming up the brain"),
    ("voice_ready", "Getting the voice ready"),
    ("ready", "Ready"),
]

_boot_state = {
    "ready": False,
    "stage": RAW_STAGES[0][0],
    "stages": [{"id": i, "label": lab, "done": False} for i, lab in RAW_STAGES],
    "progress": 0,
    "error": None,
    "state": "starting",
    "log_path": os.path.expandvars(r"%LOCALAPPDATA%\ISHTI\logs\ishti.log"),
    "elapsed_s": 0.0,
    "start_time": time.time(),
    "simulate_fail_at": os.environ.get("ISHTI_MOCK_FAIL_STAGE", None),
}

_active_websockets: List[WebSocket] = []


async def _broadcast(event_type: str, data: dict):
    msg = json.dumps({"type": event_type, "data": data, "ts": time.time()}, ensure_ascii=False)
    for ws in list(_active_websockets):
        try:
            await ws.send_text(msg)
        except Exception:
            if ws in _active_websockets:
                _active_websockets.remove(ws)


async def _simulate_boot_sequence():
    """Simulates realistic startup progressing through all stages matching ishti/lifecycle.py."""
    await asyncio.sleep(0.4)
    for i, (sid, label) in enumerate(RAW_STAGES[:-1]):
        _boot_state["stage"] = sid
        _boot_state["stages"][i]["done"] = True
        pct = round(100 * (i + 1) / len(RAW_STAGES))
        _boot_state["progress"] = pct
        _boot_state["elapsed_s"] = round(time.time() - _boot_state["start_time"], 1)

        # Check if intentional failure simulation is requested
        if _boot_state["simulate_fail_at"] and (_boot_state["simulate_fail_at"] in sid or _boot_state["simulate_fail_at"] in label):
            _boot_state["error"] = f"Failed to load component during: {label}"
            await _broadcast("error", {
                "code": f"boot_{sid}",
                "stage": sid,
                "message": _boot_state["error"],
                "log_path": _boot_state["log_path"]
            })
            return

        await _broadcast("boot_stage", {
            "id": sid,
            "label": label,
            "done": True,
            "stage": sid,
            "progress": pct,
            "error": None
        })
        await asyncio.sleep(0.35)

    _boot_state["stage"] = "ready"
    _boot_state["stages"][-1]["done"] = True
    _boot_state["progress"] = 100
    _boot_state["ready"] = True
    _boot_state["state"] = "ready"
    await _broadcast("boot_stage", {
        "id": "ready",
        "label": "Ready",
        "done": True,
        "stage": "ready",
        "progress": 100,
        "error": None
    })


async def _vram_broadcaster():
    """Simulates live RTX 5070 Ti VRAM updates."""
    while True:
        try:
            await asyncio.sleep(3.0)
            if _active_websockets:
                used = 11180.0 + (time.time() % 10) * 12.0
                total = 16303.0
                pct = round((used / total) * 100, 1)
                await _broadcast("vram_status", {
                    "gpu": {
                        "name": "NVIDIA GeForce RTX 5070 Ti",
                        "total_mb": total,
                        "used_mb": round(used, 1),
                        "free_mb": round(total - used, 1),
                        "used_pct": pct,
                    },
                    "used_mb": round(used, 1),
                    "total_mb": total,
                    "percent": pct,
                    "device": "NVIDIA GeForce RTX 5070 Ti"
                })
        except asyncio.CancelledError:
            break
        except Exception:
            pass


@app.on_event("startup")
async def on_startup():
    asyncio.create_task(_simulate_boot_sequence())
    asyncio.create_task(_vram_broadcaster())


@app.get("/api/health")
async def health():
    return {
        "ok": True,
        "timestamp": time.time(),
        "status": "healthy",
        "mock": True
    }


@app.get("/api/ready")
async def ready():
    return JSONResponse(_boot_state)


@app.get("/api/vram")
async def get_vram():
    used = 11180.0
    total = 16303.0
    return {
        "ts": time.time(),
        "gpu": {
            "name": "NVIDIA GeForce RTX 5070 Ti",
            "total_mb": total,
            "used_mb": used,
            "free_mb": total - used,
            "used_pct": round((used / total) * 100, 1),
            "budget_pct": 80,
            "budget_mb": 13042
        },
        "models": [{"name": "ishti-qwen2.5:14b", "size_mb": 9922, "size_vram_mb": 9922, "fully_on_gpu": True, "role": "planner"}],
        "roles": {},
        "violations": [],
        "jobs": {"active": [], "queued": []}
    }


@app.post("/api/ready/retry")
async def retry_boot():
    """Allows UI retry button to clear error and restart boot sequence."""
    _boot_state["error"] = None
    _boot_state["simulate_fail_at"] = None
    _boot_state["ready"] = False
    _boot_state["stage"] = RAW_STAGES[0][0]
    _boot_state["progress"] = 0
    _boot_state["state"] = "starting"
    for s in _boot_state["stages"]:
        s["done"] = False
    asyncio.create_task(_simulate_boot_sequence())
    return {"status": "retrying"}


@app.post("/api/focus")
async def focus():
    """Second instance focus trigger: broadcasts show_window event."""
    await _broadcast("show_window", {"reason": "second launch"})
    return {"ok": True, "listeners": len(_active_websockets)}


# Mirrors ishti/lifecycle.py SHUTDOWN_STEPS - plain-people labels (owner request 2026-09-29).
SHUTDOWN_STEPS = [
    ("stop_intake", "Wrapping things up"),
    ("cancel_tasks", "Finishing what ISHTI is doing"),
    ("flush_data", "Saving your conversations and files"),
    ("stop_voice", "Turning off the microphone"),
    ("unload_models", "Resting the AI"),
    ("close_browsers", "Closing the browser"),
    ("release_ports", "Signing off"),
    ("exit", "All done. Goodbye!"),
]


@app.post("/api/shutdown")
async def shutdown(req: Request):
    try:
        body = await req.json()
    except Exception:
        body = {}
    force = bool(body.get("force", False))

    async def _emit_shutdown():
        _boot_state["state"] = "shutting_down"
        for i, (sid, label) in enumerate(SHUTDOWN_STEPS):
            await _broadcast("shutdown_progress", {
                "step": sid,
                "label": label,
                "index": i + 1,
                "total": len(SHUTDOWN_STEPS),
                "done": True,
                "error": None
            })
            await asyncio.sleep(0.15 if force else 0.3)
        _boot_state["state"] = "stopped"

    asyncio.create_task(_emit_shutdown())
    return JSONResponse({"state": "shutting_down", "message": "ISHTI is shutting down."}, status_code=202)


@app.websocket("/ws/events")
@app.websocket("/ws")
async def ws_events(websocket: WebSocket):
    await websocket.accept()
    _active_websockets.append(websocket)
    try:
        # Send initial status snapshot upon connection
        await websocket.send_text(json.dumps({
            "type": "boot_stage",
            "data": {"stage": _boot_state["stage"], "progress": _boot_state["progress"]}
        }))
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        if websocket in _active_websockets:
            _active_websockets.remove(websocket)


def run_mock(port: int = 8888):
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8888
    run_mock(port)
