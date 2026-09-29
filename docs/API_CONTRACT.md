# ISHTI Desktop & Backend API Contract

**Version**: 2.0.0  
**Scope**: Formal Unified Contract between Frontend / Desktop Shell (Prompt 2) and Backend Governor / Orchestrator (Prompt 1)  
**Target Environment**: Windows 11, NVIDIA GeForce RTX 5070 Ti (16GB GDDR7), 64GB RAM  
**Base HTTP URL**: `http://127.0.0.1:8888`  
**WebSocket URL**: `ws://127.0.0.1:8888/ws/events` (the older `/ws` socket is the legacy HUD's own stream and does NOT carry these events)  
**Implementation Source of Truth**:
- Backend Event Bus: [`ishti/events.py`](file:///d:/ISHTI/ishti/events.py)
- Backend Lifecycle & Boot Engine: [`ishti/lifecycle.py`](file:///d:/ISHTI/ishti/lifecycle.py)
- Backend HTTP/WS Router & Gate: [`ishti/lifecycle_api.py`](file:///d:/ISHTI/ishti/lifecycle_api.py)
- Backend VRAM Governor: [`ishti/vram.py`](file:///d:/ISHTI/ishti/vram.py)
- Desktop Clean Shutdown Routine: [`desktop/shutdown.py`](file:///d:/ISHTI/desktop/shutdown.py)

---

## 1. Overview & Architectural Boundaries

ISHTI runs as an integrated desktop application powered by a local FastAPI backend, an NVIDIA GPU governor, and a native Microsoft Edge WebView2 shell.
- **Backend Governor (Prompt 1)**: Manages model loading, GPU memory allocation, Ollama lifecycle, state flushes, and background event publishing via `ishti.events`.
- **Frontend / Desktop Shell (Prompt 2)**: Renders the animated splash screen, full desktop workspace, VRAM monitor chip, model downshift toasts, and controls window close/tray actions without displaying terminal or console windows.
- **Non-Interference Rule**: Prompt 2 never modifies backend logic in `ishti/` or server wiring in `interface/web_ui/server.py`. Prompt 1 never modifies React components in `interface/app/` or desktop UI files in `desktop/`.

---

## 2. Readiness & Startup Probe

### 2.1 Endpoint: `GET /api/ready`
Polled by the animated splash screen and launcher to track real boot progression.

- **Response Status**: `200 OK`
- **Response Format** (`application/json`):
  ```json
  {
    "ready": false,
    "stage": "ollama_ready",
    "stages": [
      { "id": "backend_up", "label": "Starting ISHTI", "done": true },
      { "id": "ollama_ready", "label": "Waking up the AI engine", "done": false },
      { "id": "memory_loaded", "label": "Loading your memory", "done": false },
      { "id": "skills_loaded", "label": "Loading skills", "done": false },
      { "id": "planner_warm", "label": "Warming up the brain", "done": false },
      { "id": "voice_ready", "label": "Getting the voice ready", "done": false },
      { "id": "ready", "label": "Ready", "done": false }
    ],
    "progress": 14,
    "error": null,
    "state": "starting",
    "log_path": "C:\\Users\\PC\\AppData\\Local\\ISHTI\\logs\\ishti.log",
    "elapsed_s": 3.2
  }
  ```

### 2.2 Field Definitions
- `ready` (`boolean`): `true` when all startup tasks have finished (`state == "ready"` and `error == null`).
- `stage` (`string`): The identifier of the active boot stage (or the failing stage if in error).
- `stages` (`array`): Ordered list of startup stages with human-readable labels and completion status.
- `progress` (`integer`): Integer from `0` to `100` representing real completion percentage (`round(100 * done_n / len(STAGES))`).
- `error` (`string | null`): Plain-language error description if startup failed or stalled; `null` during normal boot.
- `state` (`string`): Overall lifecycle state: `"starting"`, `"ready"`, `"shutting_down"`, or `"stopped"`.
- `warnings` (`array of string`, optional): non-fatal problems (e.g. the voice engine didn't start; ISHTI is still `ready`).
- `log_path` (`string`): Absolute path to the active log file in `%LOCALAPPDATA%\ISHTI\logs\ishti.log`.
- `elapsed_s` (`number`): Elapsed boot time in seconds.

### 2.3 Boot Stages Reference
| Stage ID | Human-Readable Label | Timeout | Fatal? | Description |
|---|---|---|---|---|
| `backend_up` | Starting ISHTI | 60s | Yes | FastAPI server is listening and answering health checks. |
| `ollama_ready` | Waking up the AI engine | 60s | Yes | Ollama service is verified online; VRAM sampler started. |
| `memory_loaded` | Loading your memory | 90s | Yes | SQLite facts and Chroma vector memory stores opened. |
| `skills_loaded` | Loading skills | 180s | Yes | Skill inventory imported and intent router verified. |
| `planner_warm` | Warming up the brain | 300s | Yes | Planner model admitted onto GPU VRAM and verified. |
| `voice_ready` | Getting the voice ready | 90s | No | Kokoro/Whisper voice engine initialized (non-blocking). |
| `ready` | Ready | — | — | Boot complete; desktop window reveals. |

---

## 3. WebSocket Event Stream

### 3.1 Endpoint: `WS /ws/events`
Single-connection real-time event pipeline for UI notifications, monitors, and overlays.

- **Message Structure**:
  ```json
  {
    "type": "<event_type>",
    "data": { ... },
    "ts": 1759132800.123,
    "seq": 42
  }
  ```
- **Late Replay**: Backed by `ishti.events.replay()`. Clients connecting after boot immediately receive the latest `boot_stage`, `vram_status`, and `error` events (if no `boot_stage` was ever published, the current stage is synthesized).
- `seq` counts up per connection; a stalled client loses its oldest queued events (bounded queue of 500), never memory.
- `vram_status` is coalesced: at most one per second, only when something changed, plus a 5 s heartbeat.
- Example values in this document (versions, pids, timestamps) are illustrative; field names and types are exact and checked by `tests/test_lifecycle_contract.py` against §9.

### 3.2 Event Payloads

#### A. `boot_stage`
Emitted as stages advance or change:
```json
{
  "type": "boot_stage",
  "data": {
    "id": "skills_loaded",
    "label": "Loading skills",
    "done": true,
    "stage": "planner_warm",
    "progress": 57,
    "error": null
  },
  "ts": 1759132805.0
}
```

#### B. `vram_status`
Emitted ~1/sec on change (5s heartbeat) by `ishti.vram.start_sampler()`:
```json
{
  "type": "vram_status",
  "data": {
    "ts": 1759132806.0,
    "gpu": {
      "name": "NVIDIA GeForce RTX 5070 Ti",
      "total_mb": 16303,
      "used_mb": 11180,
      "free_mb": 5123,
      "used_pct": 68.6,
      "budget_pct": 80,
      "budget_mb": 13042
    },
    "models": [
      {
        "name": "ishti-qwen2.5:14b",
        "size_mb": 9922,
        "size_vram_mb": 9922,
        "fully_on_gpu": true,
        "context_length": 8192,
        "expires_at": "",
        "role": "planner"
      }
    ],
    "processes": [],
    "other_mb": 1258,
    "roles": {
      "planner": {
        "model": "ishti-qwen2.5:14b",
        "rung": 0,
        "rungs": 3,
        "downshifted": false,
        "pinned": true,
        "num_ctx": 8192
      }
    },
    "violations": [],
    "jobs": { "active": [], "queued": [] },
    "ollama": { "version": "0.5.12", "url": "http://127.0.0.1:11434", "ours": true, "env": {} }
  },
  "ts": 1759132806.0
}
```

#### C. `model_switch`
Emitted when the VRAM governor downshifts or upshifts a model role:
```json
{
  "type": "model_switch",
  "data": {
    "role": "planner",
    "from": "ishti-qwen2.5:14b",
    "to": "qwen2.5:7b",
    "reason": "Switched to a lighter model to stay fast",
    "at": 1759132810.5
  },
  "ts": 1759132810.5
}
```

#### D. `shutdown_progress`
Emitted sequentially during graceful shutdown to drive the full-screen desktop overlay:
```json
{
  "type": "shutdown_progress",
  "data": {
    "step": "unload_models",
    "label": "Releasing the graphics card",
    "index": 5,
    "total": 8,
    "done": true,
    "error": null
  },
  "ts": 1759132820.0
}
```

#### E. `error`
Emitted if a critical background process or boot phase fails:
```json
{
  "type": "error",
  "data": {
    "code": "boot_ollama_ready",
    "message": "The AI engine (Ollama) didn't start. Make sure Ollama is installed, then try again.",
    "stage": "ollama_ready",
    "log_path": "C:\\Users\\PC\\AppData\\Local\\ISHTI\\logs\\ishti.log"
  },
  "ts": 1759132812.0
}
```

#### F. `show_window`
Emitted when a second desktop instance is launched to bring the existing window to the front:
```json
{
  "type": "show_window",
  "data": {
    "reason": "second launch"
  },
  "ts": 1759132815.0
}
```

---

## 4. Graceful Shutdown & Gate Middleware

### 4.1 Endpoint: `POST /api/shutdown`
Initiates orderly, graceful termination of backend services, child processes, and VRAM allocations.

- **Request Body** (`application/json`, optional):
  ```json
  { "force": false }
  ```
- **Response Status**: `202 Accepted`
- **Response Format**:
  ```json
  {
    "state": "shutting_down",
    "message": "ISHTI is shutting down."
  }
  ```

### 4.2 Gate ASGI Middleware
When shutdown begins, the `Gate` middleware in `ishti.lifecycle_api`:
- Closes intake: Any non-critical request receives HTTP `503 Service Unavailable` with `{"error": "shutting_down"}`.
- Closes WebSockets with close code `1013` (Try Again Later).
- Whitelist (`ALWAYS`): `/api/health`, `/api/ready`, `/api/vram`, `/api/shutdown`, `/api/focus`, `/ws/events`, `/api/status` continue to answer until the server closes.

### 4.3 Shutdown Steps Reference (`SHUTDOWN_STEPS`)
1. `stop_intake`: Not accepting new requests
2. `cancel_tasks`: Finishing or cancelling running tasks (bounded wait)
3. `flush_data`: Saving your data (SQLite WAL checkpoint truncate & integrity check)
4. `stop_voice`: Stopping the voice and microphone (force mic OFF)
5. `unload_models`: Releasing the graphics card (unloads Ollama models + stops our Ollama if started by ISHTI)
6. `close_browsers`: Closing ISHTI's browser tools (MCP/Playwright sessions; never user Chrome)
7. `release_ports`: Closing the server (Uvicorn `should_exit = True`, release port 8888)
8. `exit`: Goodbye (process tree termination)

Events 7 and 8 are sent just *before* the port really closes (closing the listener also closes every WebSocket), so a
client that receives `exit` should expect the connection to drop within a second. A step that fails is reported
(`error` set) and skipped; the remaining steps still run. Anything still alive after 40 s is force-killed (whole tree).
An Ollama that ISHTI did not start is never stopped (its models are unloaded); the user's own Chrome is never touched.

---

## 5. VRAM Governor & Allocation (§5)

### 5.1 Snapshot: `GET /api/vram`
Returns complete GPU, model, and ladder residency statistics from `ishti.vram.snapshot()` (see Section 3.2.B for response schema).

### 5.2 Footprints: `GET /api/vram/footprints`
Returns historical measured VRAM footprints stored in SQLite (`vram.db`). `size_vram_mb` is what Ollama reports; `gpu_delta_mb` is how much the whole GPU really grew when the model loaded (larger for vision models, whose projector Ollama does not count) - the governor plans with the larger of the two.
```json
{
  "footprints": [
    { "model": "ishti-qwen2.5:14b", "num_ctx": 8192, "size_mb": 9922, "size_vram_mb": 9922, "fully_on_gpu": true,
      "tps": 79.5, "kv_type": "f16", "measured_at": 1759132800.0, "gpu_delta_mb": 10151 }
  ]
}
```

### 5.3 Admission: `POST /api/vram/admit`
Requests VRAM space for a model before execution. The governor automatically unloads idle models or downshifts pinned roles if necessary.
- **Request Body**:
  ```json
  {
    "model": "qwen2.5-coder:14b",
    "num_ctx": 8192,
    "role": "coder",
    "job": "code completion",
    "strict": true,
    "wait_s": 15.0
  }
  ```
- **Response Format**:
  ```json
  {
    "ok": true,
    "model": "qwen2.5-coder:7b",
    "num_ctx": 8192,
    "role": "coder",
    "actions": [ { "do": "downshift", "role": "planner", "to": 1, "model": "qwen2.5:7b", "why": "make room for qwen2.5-coder:14b" } ],
    "waited_s": 3.2,
    "lease": 101
  }
  ```
  `model` is the model that was actually loaded (the governor may pick a lighter rung of the same role - compare it with
  what you asked for). If nothing fits within `wait_s`, `ok` is `false`, `lease` is `null` and `queued` holds the plain
  reason (never a partial offload). Send the `lease` to `/api/vram/release` when the job is finished.

### 5.4 Release: `POST /api/vram/release`
Releases an admitted job lease:
- **Request Body**: `{"lease": 101}`
- **Response Format**: `{"released": true}`

---

## 6. Ollama Control & Settings

### 6.1 Get Settings: `GET /api/ollama/settings`
- **Response Format**:
  ```json
  {
    "settings": {
      "num_parallel": 1,
      "max_loaded_models": 4,
      "keep_alive": "10m",
      "kv_cache_type": "f16",
      "flash_attention": null
    },
    "env": {
      "OLLAMA_NUM_PARALLEL": "1",
      "OLLAMA_MAX_LOADED_MODELS": "4",
      "OLLAMA_KEEP_ALIVE": "10m"
    },
    "active_env": { ... },
    "ours": true,
    "active": true
  }
  ```

### 6.2 Save Settings: `POST /api/ollama/settings`
- **Request Body**: Partial or full settings dictionary.
- **Response Format**: `{"saved": true, "settings": { ... }, "needs_restart": boolean}`

### 6.3 Apply & Restart: `POST /api/ollama/apply`
- **Request Body**: `{"take_over": false}`
- **Response Format**: `{"applied": true, "restarted": true, "needs_restart": false, "settings": { ... }}`

---

## 7. Single-Instance Focus Protocol

### 7.1 Mechanism
- Windows Named Mutex: `Local\ISHTI_Backend_{port}` (and `ISHTI_DESKTOP_SINGLE_INSTANCE_MUTEX` for pywebview shell).
- When a user launches ISHTI a second time:
  1. The launcher detects the existing mutex.
  2. The launcher calls `POST http://127.0.0.1:8888/api/focus`.
  3. The backend receives the call and broadcasts `show_window` on `/ws/events`.
  4. The desktop shell brings the existing window to the front (`window.restore()`, `window.show()`).
  5. The second process exits cleanly with code 0 without ever creating a second window or starting a second server.

### 7.2 Endpoint: `POST /api/focus`
- **Response Format**: `{"ok": true, "listeners": 1}`

---

## 8. Liveness & Process Tree Registry

### 8.1 Endpoint: `GET /api/health`
Liveness probe answering at all times unless hung:
```json
{
  "ok": true,
  "state": "ready",
  "status": "ok",
  "at": 1759132800.0,
  "checks": { "ollama": { "ok": true, "detail": "10 models" } }
}
```

### 8.2 Registry File: `%LOCALAPPDATA%\ISHTI\run\ishti.json`
Stores PIDs for clean process tracking:
```json
{
  "server_pid": 14208,
  "port": 8888,
  "started": 1759132790.0,
  "watchdog_pid": null,
  "ollama_pid": 18452,
  "ollama_url": "http://127.0.0.1:11434"
}
```

### 8.3 Process Tree Cleanliness
All child processes spawned by ISHTI are assigned to a Windows Job Object (`JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`). Even in an unexpected crash, no orphaned Ollama, Node MCP, or browser processes remain running.

---

## 9. Machine-checked shapes

`tests/test_lifecycle_contract.py` starts a real backend and checks the live responses against these blocks. Type words:
`string`, `number`, `bool`, `null`, `array`, `object`; `a|b` = either; a key ending in `?` is optional; extra keys are allowed
(clients must ignore keys they don't know). If you change a response, change this section in the same commit.

<!-- shape:health -->
```json
{"ok": "bool", "state": "string", "status?": "string", "at?": "number", "checks?": "object"}
```

<!-- shape:ready -->
```json
{"ready": "bool", "stage": "string", "stages": "array", "progress": "number", "error": "null|string",
 "state": "string", "log_path": "string", "elapsed_s": "number", "warnings?": "array"}
```

<!-- shape:stage -->
```json
{"id": "string", "label": "string", "done": "bool"}
```

<!-- shape:vram -->
```json
{"ts": "number", "gpu": "object", "models": "array", "processes": "array", "other_mb": "number", "roles": "object",
 "violations": "array", "jobs": "object", "ollama": "object"}
```

<!-- shape:vram.gpu -->
```json
{"name": "string", "total_mb": "number", "used_mb": "number", "free_mb": "number", "used_pct": "number", "budget_pct": "number", "budget_mb": "number"}
```

<!-- shape:vram.model -->
```json
{"name": "string", "size_mb": "number", "size_vram_mb": "number", "fully_on_gpu": "bool", "context_length": "number|null", "expires_at": "string", "role": "string|null"}
```

<!-- shape:vram.role -->
```json
{"model": "string", "rung": "number", "rungs": "number", "downshifted": "bool", "pinned": "bool", "num_ctx": "number"}
```

<!-- shape:vram.ollama -->
```json
{"version": "string", "url": "string", "ours": "bool", "env": "object"}
```

<!-- shape:event -->
```json
{"type": "string", "data": "object", "seq": "number", "ts": "number"}
```

<!-- shape:model_switch -->
```json
{"role": "string", "from": "string", "to": "string", "reason": "string", "at": "number"}
```

<!-- shape:shutdown_progress -->
```json
{"step": "string", "label": "string", "index": "number", "total": "number", "done": "bool", "error": "null|string"}
```

<!-- shape:shutdown_ack -->
```json
{"state": "string", "message": "string"}
```

<!-- shape:footprint -->
```json
{"model": "string", "num_ctx": "number", "size_mb": "number", "size_vram_mb": "number", "fully_on_gpu": "bool", "tps": "number|null", "kv_type": "string", "measured_at": "number", "gpu_delta_mb?": "number|null"}
```

<!-- shape:admit -->
```json
{"ok": "bool", "model": "string", "num_ctx": "number|null", "role": "string|null", "actions": "array", "waited_s": "number", "lease": "number|null", "queued?": "string"}
```

<!-- shape:ollama_settings -->
```json
{"settings": "object", "env": "object", "active_env": "object", "ours": "bool", "active": "bool"}
```
