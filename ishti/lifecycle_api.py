"""HTTP + WebSocket side of docs/API_CONTRACT.md: /api/ready, /api/vram, /api/shutdown, /ws/events, /api/focus and the
VRAM/Ollama helpers. attach(app) wires it into the FastAPI server (router, in-flight gate, boot at startup)."""
import asyncio
import json
import sys
import time

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

from ishti import events, lifecycle, vram

router = APIRouter()
# still answered while shutting down (everything else gets 503)
ALWAYS = frozenset(("/api/health", "/api/ready", "/api/vram", "/api/shutdown", "/api/focus", "/ws/events", "/api/status"))  # exact paths
GUARDED = ("/api/shutdown", "/api/ollama", "/api/vram", "/api/focus", "/ws/events", "/api/settings", "/api/manage", "/api/privacy", "/api/sessions", "/api/reminders", "/api/notifications", "/api/markets", "/api/skills", "/api/downloads")  # state-changing / sensitive: no foreign websites
LOOPBACK = {"127.0.0.1", "localhost", "::1"}
PROXY_HEADERS = (b"cf-connecting-ip", b"x-forwarded-for", b"forwarded", b"x-real-ip")


async def _off(fn, *a):
    return await asyncio.get_running_loop().run_in_executor(None, fn, *a)


@router.get("/api/ready")
async def ready():
    return lifecycle.ready_snapshot()


_snap = {"t": 0.0, "v": None}
_snap_lock = None


@router.get("/api/vram")
async def vram_snapshot():
    """One snapshot is computed at a time (nvidia-smi + Windows counters + Ollama calls cost ~100-300 ms) and shared by everyone
    who asks within 0.5 s: 150 simultaneous callers cost one computation, not 150 threads all hammering the GPU tools."""
    global _snap_lock
    if _snap_lock is None:
        _snap_lock = asyncio.Lock()
    if _snap["v"] is not None and time.time() - _snap["t"] < 0.5:
        return _snap["v"]
    async with _snap_lock:
        if _snap["v"] is None or time.time() - _snap["t"] >= 0.5:  # whoever waited on the lock finds it fresh
            _snap["v"], _snap["t"] = await _off(vram.snapshot), time.time()
    return _snap["v"]


@router.get("/api/vram/footprints")
async def vram_footprints():
    return {"footprints": await _off(vram.footprints)}


@router.post("/api/vram/admit")
async def vram_admit(req: Request):
    """{model, num_ctx?, role?, job?, strict?, wait_s?} -> the governor makes room and loads it (verified on the GPU)."""
    b = await req.json()
    if not b.get("model"):
        return JSONResponse({"error": "model is required"}, status_code=400)
    res = await _off(lambda: vram.admit(b["model"], b.get("num_ctx"), b.get("role"), b.get("job", ""), bool(b.get("strict")), b.get("wait_s")))
    return res


@router.post("/api/vram/release")
async def vram_release(req: Request):
    b = await req.json()
    return {"released": vram.release(int(b.get("lease", 0)))}


@router.get("/api/ollama/settings")
async def ollama_settings_get():
    s = lifecycle.ollama_settings()
    want, have = lifecycle.ollama_env(s), await _off(lifecycle.ollama_env_active)
    return {"settings": s, "env": want, "active_env": have, "ours": await _off(lifecycle.ollama_ours),
            "active": all(have.get(k) == v for k, v in want.items())}


@router.post("/api/ollama/settings")
async def ollama_settings_set(req: Request):
    """Save settings (no restart). Body: any of num_parallel, max_loaded_models, keep_alive, kv_cache_type, flash_attention."""
    try:
        s = await _off(lifecycle.save_ollama_settings, await req.json())
    except (ValueError, TypeError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    have = await _off(lifecycle.ollama_env_active)
    return {"saved": True, "settings": s, "needs_restart": not all(have.get(k) == v for k, v in lifecycle.ollama_env(s).items())}


@router.post("/api/ollama/apply")
async def ollama_apply(req: Request):
    """Restart Ollama with the saved settings. {take_over: true} = the person confirmed restarting an Ollama ISHTI didn't start."""
    try:
        b = await req.json()
    except Exception:
        b = {}
    try:
        return await _off(lambda: lifecycle.apply_ollama_settings(None, take_over=bool(b.get("take_over"))))
    except Exception as exc:
        return JSONResponse({"error": f"{type(exc).__name__}: {exc}"}, status_code=500)


@router.post("/api/focus")
async def focus():
    """A second copy of ISHTI was launched: tell every listening window to show itself."""
    events.publish("show_window", {"reason": "second launch"})
    return {"ok": True, "listeners": len(events._subs)}


@router.post("/api/shutdown")
async def shutdown(req: Request):
    try:
        force = bool((await req.json()).get("force"))
    except Exception:
        force = False
    return JSONResponse(lifecycle.begin_shutdown(force), status_code=202)


@router.websocket("/ws/events")
async def ws_events(ws: WebSocket):
    srv = sys.modules.get("interface.web_ui.server")
    if srv is not None and not srv.authorized(ws):
        await ws.close(code=1008)
        return
    await ws.accept()
    loop = asyncio.get_running_loop()
    q = asyncio.Queue(maxsize=500)

    def _put(m):
        if q.full():  # a stalled client must never grow memory: drop its oldest event
            q.get_nowait()
        q.put_nowait(m)

    def push(m):
        loop.call_soon_threadsafe(_put, m)
    seq = 0

    async def send(m):
        nonlocal seq
        seq += 1
        await ws.send_text(json.dumps({**m, "seq": seq}, ensure_ascii=False, default=str))

    events.subscribe(push)
    try:
        replayed = events.replay()
        for m in replayed:
            await send(m)
        if not any(m["type"] == "boot_stage" for m in replayed):  # nothing published yet: send the current picture
            snap = lifecycle.ready_snapshot()
            cur = next((s for s in snap["stages"] if not s["done"]), snap["stages"][-1])
            await send({"type": "boot_stage", "ts": snap["elapsed_s"], "data": {"id": cur["id"], "label": cur["label"], "done": cur["done"],
                                                                                   "stage": snap["stage"], "progress": snap["progress"], "error": snap["error"]}})
        recv = asyncio.ensure_future(ws.receive_text())  # a client message or disconnect ends the loop
        while True:
            get = asyncio.ensure_future(q.get())
            done, _ = await asyncio.wait({get, recv}, return_when=asyncio.FIRST_COMPLETED)
            if recv in done:
                get.cancel()
                recv.result()  # raises WebSocketDisconnect on close; a text message is ignored
                recv = asyncio.ensure_future(ws.receive_text())
            if get in done:
                await send(get.result())
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        events.unsubscribe(push)
        for fut in (locals().get("recv"), locals().get("get")):
            if fut is not None and not fut.done():
                fut.cancel()


def foreign_web_page(scope):
    """True for a request that comes from ANOTHER website open in the owner's browser (or a DNS-rebinding page): the browser adds
    an Origin (and the Host is the attacker's name). Direct callers (the desktop shell, the launcher, tests) send neither, the app
    itself sends a loopback Origin. Remote/tunnel requests are authenticated by the token middleware instead, so they are skipped."""
    client = (scope.get("client") or ("", 0))[0]
    headers = dict(scope.get("headers") or [])
    if client not in LOOPBACK or any(h in headers for h in PROXY_HEADERS):
        return False
    from urllib.parse import urlsplit
    origin = headers.get(b"origin", b"").decode("latin-1")
    host = headers.get(b"host", b"").decode("latin-1").rsplit(":", 1)[0].strip("[]")
    return bool(origin and urlsplit(origin).hostname not in LOOPBACK) or bool(host and host not in LOOPBACK)


class Gate:
    """Pure-ASGI middleware: counts in-flight requests (shutdown waits for them) and turns away new work while shutting down."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)
        path = scope.get("path", "")
        if path.startswith(GUARDED) and (scope["type"] == "websocket" or scope.get("method") != "GET") and foreign_web_page(scope):
            if scope["type"] == "http":
                body = json.dumps({"error": "foreign origin"}).encode()
                await send({"type": "http.response.start", "status": 403, "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
                return await send({"type": "http.response.body", "body": body})
            return await send({"type": "websocket.close", "code": 1008})
        if not lifecycle.accepting() and path not in ALWAYS:
            if scope["type"] == "http":
                body = json.dumps({"error": "shutting_down"}).encode()
                await send({"type": "http.response.start", "status": 503, "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
                return await send({"type": "http.response.body", "body": body})
            return await send({"type": "websocket.close", "code": 1013})
        tracked = scope["type"] == "http" and path not in ALWAYS
        if tracked:
            lifecycle._inflight["n"] += 1
        try:
            await self.app(scope, receive, send)
        finally:
            if tracked:
                lifecycle._inflight["n"] -= 1


def attach(app):
    """server.py calls this once: routes, gate, and the boot sequence at startup."""
    app.include_router(router)
    app.add_middleware(Gate)

    async def _boot():
        lifecycle.start(lifecycle._st["port"])
    app.on_event("startup")(_boot)
