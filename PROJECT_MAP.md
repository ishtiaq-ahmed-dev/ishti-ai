# ISHTI Project Map & Architecture Audit

## 1. System Overview & Runtime Hierarchy
ISHTI is an offline-first, low-latency, zero-cost personal AI assistant configured for Windows 10/11 running on an NVIDIA RTX 5070 Ti (16 GB VRAM) and 64 GB host RAM.

### Core Architecture Layers:
1. **Perception & Interaction Layer**
   - **HUD Client (`interface/app`)**: React + TypeScript + Vite + Tailwind CSS dashboard providing real-time streaming tokens, session tree, thinking trace graph, memory inspector, and human-in-the-loop approvals. Builds cleanly to static assets (`dist/`).
   - **Web UI Gateway (`interface/web_ui/server.py`)**: FastAPI server on port 8000 handling WebSocket telemetry (`/ws/events`), static HUD assets, authentication tokens, and mounting `/api` endpoints.
   - **Voice Engine (`ishti/talk.py`, `ishti/voice.py`, `ishti/wake.py`)**:
     - STT: faster-whisper large-v3-turbo (local CTranslate2 build in `data/models/whisper-large-v3-turbo`, float16 on
       CUDA; HF-cache `large-v3` as fallback), languages constrained to en/ur/ps with hi->ur and fa->ps folding.
       **One thread decodes it** (`voice.stt_call`): CTranslate2's CUDA state belongs to the decoding thread, and
       decoding the same model from two threads made the process fail fast (0xC0000409) at exit - see ISSUES.md
       Issue 8. Partials, the final decode, the language pick, the warm-ups and the idle unload all run on that one
       worker, which also serialises the GPU.
     - Wake Word Detection: OpenWakeWord (`hey jarvis` model, CPU) + a whisper-tiny wake-phrase spotter; Silero VAD
       ends the turn, 120 ms of sustained owner speech drives barge-in with a self-echo filter.
     - TTS: Piper/Kokoro roster (3 male + 1 female) streamed sentence by sentence through `talk.Talker` with ducking;
       Fish Audio S2 plugs into the same roster (blocked on CUDA Toolkit, NEEDS.md 13).
     - Three interaction modes: text/text (`/api/execute`, no audio), voice-in/text-out (`wake.start` ->
       `channel="voice"` replies spoken on this PC), Live (`/ws/live` -> `RemoteTalker`, audio streamed to the
       browser, no wake word, one session at a time).

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

3. **Skill Ecosystem (`ishti/skills/` - 47 Modules)**
   - All 47 skills implement the unified contract: `run(text, session) -> {"reply": ..., "sources": [...]}`.
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
   +- relationships (rgraph.db: temporal relationship graph)
   +- clients (CRM, per-client isolated memory)
   +- braintrain (fine-tune pipeline front)
```

## 3b. Sections 1–9 layer (added 2026-09-28)
- **Model residency** (`ishti/models.py`): planner pinned; everything else evictable, 10-min idle unload;
  `gpu.slot` serialises heavy jobs; every load/unload logged to `data/model_usage.db`. Decisions in
  `MODEL_DECISIONS.md`; benchmarks in `PERF_LOG.md`.
- **Global mic control** (`ishti/mic_control.py`): default OFF, persisted (`data/mic_state.json`), voice
  commands, `/api/mic/*`, tray icon (`desktop/tray.py`), killswitch/shutdown force OFF. Wake listener starts
  only when the owner left it ON.
- **Obscura** (`ishti/obscura.py`): the Rust headless browser (0.2.3, `tools/obscura/`) is the default reader
  for `web_fetch` and `deep_research` — markdown for LLMs, robots obeyed, no stealth, ~30 MB RAM, no GPU.
- **Self-heal** (`ishti/self_heal.py`) manual-only; **monitor** (`ishti/monitor.py`) passive correction store.
- **Skill lifecycle** (`ishti/skill_lifecycle.py`): gap discovery, practice, per-skill `.memory.md`, report
  cards, refine, reversible retire — wraps skillsmith's create/install gates (never auto-runs).
- **Skills**: 54 (incl. new `screen_control`, `screen_understand`, `notes`, `writing`, `study`, `budget`,
  `habits`). Roadmap vs the 35-domain catalogue: `SKILLS_ROADMAP.md`.

## 3. Relationship Graph & CRM & Fine-Tuning (added 2026-09-27)

- **`ishti/rgraph.py`** - SQLite relationship memory graph (`data/rgraph.db`): people/places/preferences as
  nodes, typed kin/preference/introduction edges with first_seen/last_confirmed timestamps (stale facts are
  flagged, not silently trusted). Contradictions open a pending question instead of overwriting; duplicate
  people are detected and merged; kinship edges are private by default and NEVER exported to client-facing
  context or training data. Self-check: `python ishti/rgraph.py`.
- **`relationships` skill** - "this is my brother Ahmed", "Ahmed likes cricket", "what does my father like",
  "who introduced me to X", "what should I get Ahmed for his birthday" (graph traversal, no model call),
  contradiction resolution ("the new one"/"the old one"), merge.
- **`clients` skill** - CRM with the two hard rules in code, not prompts:
  1. ISOLATION: `context_load`/`grounding_for` return ONE client's rows only - verified in the self-check
     that no other client's facts appear in a grounding block.
  2. CONFIRMATION: sending to a client is always the T2 yes/no gate; drafts are grounded in that client's
     logged history only; angry/urgent messages are flagged to the owner and never auto-drafted.
  Ops: new client, log message, context, draft, send (T2), follow-up (reminders row), deal summary,
  invoices (add/mark/status), notes, lead intake, dashboard (Markdown table + attention flags).
## 4. Verification map (what proves what, 2026-09-27)

- **`tools/verify_all.py`** - one pass over every self-check and test, reporting the RAW exit code (a Windows
  fail-fast is a non-zero code even when the self-check printed OK). `python tools/verify_all.py [--only X] [--quick]`.
- **`ishti/check.py`** - core loop end to end: skills load clean, routing eval (157 labelled requests, 157/157;
  incl. the paper-trading cases)
  memory, live weather, local LLM, compound request through the planner, T2 yes/no gate, repeat guard, T3 block,
  remote-auth, situation layer. `python -m ishti.check`.
- **Self-checks** - every core module and every skill ends with `if __name__ == "__main__"` asserts that need no
  network beyond the live services they document (OCR, HIBP, speed test, weather).
- **`tests/`** - `test_stt_threads` (one decode thread + zero-exit subprocess run), `test_multitopic` (4 topics at
  once in one chat: answers, history, task tracker, contained failure, T2 gate per topic), `test_lifecycle`
  (singleton Start, verified Stop, detached OS-shutdown, exit confirmation), `test_browser_tabs`,
  `test_whatsapp_context`, `test_voice_note_generation`, `test_stt_accuracy`, `test_dispatcher_followups`,
  `test_planner_messenger`, `test_capcut_skill`.

## 5. Concurrency model (one conversation)

The dispatcher runs independent requests of one chat **in parallel**: each gets its own `View` (own trace/token
stream) over the session's shared `history`/`pending`, guarded by a per-session `INFLIGHT` condition. Follow-ups,
yes/no answers and corrections wait for the requests already running (they are *about* their answer). Every request
is recorded in the session's task tracker (last 10, `shared["tasks"]`, each with its own result) which answers
"what's the status of my tasks". A skill that raises is contained: its failure is logged (audit + NEEDS.md) and the
answer says what happened while the conversation carries on. Voice adds the pause rule: a background task
(`task_background`) keeps running when the owner moves on, and its answer is announced with its topic at the next
natural pause (`_announce` -> `task_done`).

## 6. Behavior-data pipeline

- **`ishti/ftlog.py` + `braintrain` skill + `orchestrator/finetune.py`** - behavior-data pipeline:
  collection is passive (dispatcher `_finish` logs every exchange with its route; corrections upgrade the
  row's quality to 'corrected'); secrets/phones/emails are redacted before storage; export curates
  (dedup, length floor, 40% per-skill cap) into JSONL chat format; the fine-tune job is QLoRA (LoRA r=16,
  4-bit, idle-hours) with an eval-before-promote regression gate (fixed eval set, never auto-promote a
  worse adapter) and archived adapters for rollback. Degrades gracefully without training deps - status
  via "brain training status".
