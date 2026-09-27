# ISHTI Project Map & Architecture Audit

## 1. System Overview & Runtime Hierarchy
ISHTI is an offline-first, low-latency, zero-cost personal AI assistant configured for Windows 10/11 running on an NVIDIA RTX 5070 Ti (16 GB VRAM) and 64 GB host RAM.

### Core Architecture Layers:
1. **Perception & Interaction Layer**
   - **HUD Client (`interface/app`)**: React + TypeScript + Vite + Tailwind CSS dashboard providing real-time streaming tokens, session tree, thinking trace graph, memory inspector, and human-in-the-loop approvals. Builds cleanly to static assets (`dist/`).
   - **Web UI Gateway (`interface/web_ui/server.py`)**: FastAPI server on port 8000 handling WebSocket telemetry (`/ws/events`), static HUD assets, authentication tokens, and mounting `/api` endpoints.
   - **Voice Engine (`ishti/talk.py`, `ishti/voice.py`, `ishti/wake.py`)**:
     - STT: Faster-Whisper (`Systran/faster-whisper-small.en` / `Systran/faster-whisper-base`) via CTranslate2.
     - Wake Word Detection: OpenWakeWord (`hey jarvis` / `alexa` models) running with 80 ms audio frames.
     - TTS: Piper neural synthesis (`en_US-lessac-medium`) streaming to PyAudio with ducking logic.

2. **Orchestration & Dispatch Layer (`ishti/dispatcher.py`)**
   - **Multi-Route Resolution**:
     - Step 0: Active confirmations / pending HITL questions (e.g. file deletions, code execution).
     - Step 1: Multi-step request planner (`ishti/planner.py`) detecting compound queries.
     - Step 2: Deterministic regex intent matching across 40 skill manifests (`match(text)`).
     - Step 3: Semantic zero-cost classifier (`laya` router / fp16 model).
     - Step 4: Default conversation fallback (`ishti/skills/chat/skill.py`).
   - **Execution Sandboxing & Guardrails (`ishti/scope.py`, `ishti/killswitch.py`, `ishti/untrusted.py`)**:
     - Every skill operates under fine-grained declared capability scopes (file writes, network access, app execution).
     - Protected OS roots (`C:\Windows`, `C:\Program Files`, `D:\ISHTI`) are enforced against unauthorized writes.
     - Killswitch abort mechanism interrupts in-flight sub-agent actions on user request or abort token.

3. **Skill Ecosystem (`ishti/skills/` - 40 Modules)**
   - All 40 skills implement the unified contract: `run(text, session) -> {"reply": ..., "sources": [...]}`.
   - Manifests (`manifest.json`) define capabilities, route priorities, scopes, and external dependencies.
   - Modules include: `apps`, `automation`, `browser`, `calendar`, `chat`, `contacts`, `deep_research`, `desktop`, `dev`, `digest`, `docs`, `email`, `extract`, `fileops`, `files`, `folders`, `gemini`, `ledger`, `lifelog`, `markets`, `media`, `memory`, `meta`, `online_fallback`, `owner`, `planners`, `reminders`, `security`, `shopping`, `skillsmith`, `system`, `travel`, `utilities`, `video`, `weather`, `web_fetch`, `web_search`, `webtools`, `whatsapp`, `workflows`.

4. **Model & Inference Layer (`ishti/llm.py`, `orchestrator/model_router.py`)**
   - Local Ollama daemon (`http://127.0.0.1:11434/v1`) using OpenAI-compatible SDK bindings.
   - Configured roles (`ishti/config.json`):
     - `planner`: `ishti-qwen2.5:14b` (14.8B parameters, Q4_K_M)
     - `coder`: `ishti-qwen2.5-coder:14b`
     - `fast`: `llama3.2:3b`
     - `fallback`: `qwen2.5:14b`

5. **Search & Memory Systems**
   - **Web Search (`tools/web/search.py`)**: Local SearXNG Docker container on `http://127.0.0.1:8890` with fallback to Bing Lite, GitHub Releases, Hacker News, Wikipedia, and ArXiv APIs.
   - **Browser Automation (`ishti/mcp_client.py`, `browser/browser_agent.py`)**:
     - Headless & visible browser contexts driven by Playwright MCP and persistent user profile (`D:\ISHTI\data\chrome_profile`).
   - **Dual Memory Engine**:
     - Exact Fact Store (`ishti/facts.py`): SQLite (`data/ishti.db`) with UPSERT semantics.
     - Semantic Memory (`ishti/semantic.py`): ChromaDB vector store (`data/chroma`) with 180+ embedded knowledge points.
     - User Profile Memory (`orchestrator/memory/user_profile.py`): Persistent owner traits (Ishtiaq Ahmed, Peshawar, Pakistan).

---

## 2. Component Dependency Graph
```
[HUD Web Interface] (React / Vite on :5173 or static dist)
        | WebSocket / HTTP
        v
[FastAPI Bridge] (interface/web_ui/server.py on :8000)
        |
        +----------------------+----------------------+
        v                      v                      v
[ishti.dispatcher]     [ModelRouter]          [Voice Pipeline]
        |                      |               (Piper / Whisper)
   +----+--------------------------+                  |
   v                               v                  v
[Skills Engine (40)]       [Ollama Local LLMs]  [PyAudio Out]
   | (Manifests + Scopes)   (14B / 3B / Coder)
   +- deep_research
   +- browser (Playwright MCP)
   +- search (SearXNG :8890)
   +- memory (SQLite + ChromaDB)
```