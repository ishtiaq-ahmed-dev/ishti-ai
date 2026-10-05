# ARCHITECTURE: how ISHTI works today

Last targeted verification against the code: 2026-10-04 (branch `feat/repo-integration`). This document describes what the
repository contains now. Where a number appears it is labelled **MEASURED** (with the file that holds the
measurement), **SOURCED** (taken from an existing doc or config that I did not re-measure), or **ASSUMED**.
Anything I could not confirm in code is said so.

Related: [SETUP.md](SETUP.md) (install/run/test), [SKILLS.md](SKILLS.md), [SAFETY.md](SAFETY.md),
[API_CONTRACT.md](API_CONTRACT.md) (HTTP/WS contract), [MODEL_DECISIONS.md](MODEL_DECISIONS.md) (measured model
ladders), [SETTINGS.md](SETTINGS.md), subsystem deep-dives in [arch/](arch/).

## 1. What ISHTI is, in one paragraph

A local-first personal assistant for one Windows PC. A FastAPI backend (`ishti/` + `interface/web_ui/server.py`,
port 8888) takes a request from the UI, the microphone, or a paired remote channel, routes it to one of 124 skills,
and returns a reply. All language-model calls go to a local Ollama server. Search goes through a local SearXNG
instance first. There is no cloud model call in the request path; the web skills reach the internet because
fetching web pages is their job, and every skill's network reach is declared in its manifest (see
[SAFETY.md](SAFETY.md)). A React UI (`interface/app`) is served by the backend and shown in a pywebview window by
the desktop shell (`desktop/`).

## 2. Processes and how they start

```
start.bat                       (no argument)
  -> desktop/launcher.py         run by venv\Scripts\pythonw.exe (no console); ISHTI.exe, if built, only hands off to it
       - singleton check: backend already answering? focus its window and exit
       - spawn  `pythonw -m ishti 8888`   (the backend; detached, no window)
       - spawn  desktop/tray.py           (tray icon: mic on/off, open dashboard, stop)
       - desktop/backend_watch.py         (exit this process if the backend disappears or writes the shutdown flag)
       - desktop/app_window.py            (pywebview window: splash.html, then the dashboard)
  -> python -m ishti 8888        (ishti/__main__.py)
       lifecycle.single_instance (mutex) -> setup_logging -> adopt_job (Windows Job Object) -> server.start_server
  -> ollama serve                started by lifecycle.ensure_ollama if not already running ("ours" => stopped at shutdown)
```

Other entry points, all in `start.bat`: `start.bat cli` and the no-match fallback run `orchestrator/startup.py`
(which starts `python -m ishti.watchdog 8888`: restart on crash/hang plus the global kill hotkey);
`start.bat server [port]` runs `python -m ishti`; `doctor`, `search`, `find`, `rebenchmark`, `settings`, `test` are
developer commands. `stop.bat` runs `desktop/shutdown.py`. Source: `start.bat`, `desktop/launcher.py`,
`ishti/__main__.py`, `orchestrator/startup.py`.

Note the two start paths differ in one safety-relevant way: the desktop launcher starts the backend directly (no
watchdog process), so the kill hotkey is registered in-process by `interface/web_ui/server.py` (works unless the
server hangs); the `startup.py` path puts it in the separate watchdog process.

## 3. The request path

Every input channel ends in one function: `ishti.dispatcher.handle(text, session_id, channel, on_token, on_event)`.
Channels: the UI (`POST /api/chat`, `/api/execute`, `/ws`), voice (`channel="voice"`, from `wake.py` or `/ws/live`),
scheduled jobs (`channel="scheduled"`), and the remote channels in `ishti/channels/` (Telegram; WhatsApp is
implemented but off by default). Voice is only another input; the transcript goes through the same function.

`handle()` does, in order (source: `ishti/dispatcher.py`, `docs/arch/request-lifecycle.md`):

1. Kill-switch generation is bumped (`killswitch.begin`); the request gets a `View` over the session so independent
   requests of one chat run in parallel while follow-ups, yes/no answers and corrections wait for the ones running.
2. `lifecycle.await_brain()`: if the backend is still booting, the request is queued with a plain explanation and
   answered when the brain stages finish (never a failure, never silence).
3. `_handle()`, a cascade in which the cheapest deterministic checks run first:
   - pending yes/no confirmation (the T2 gate) answers before anything is routed;
   - an unresolved ambiguous reference ("the cheapest one") is completed by the owner's answer;
   - `reflex.answer` (pure regex: time, date, greetings, stop talking, volume);
   - microphone commands, memory commands (`arch2/consolidate.handle_command`), architecture switch commands;
   - language-lock commands; Urdu/Pashto/Roman-Urdu input is translated to English for routing and the answer is
     translated back;
   - an explicit correction ("that should have been web search") re-runs the last request with that skill and
     records it as a labelled example;
   - **active architecture** (section 4): arch2, the multi-agent profile, or the Flowise profile can take the turn
     here; otherwise the arch1 path continues;
   - `contextualize` rewrites follow-ups into standalone requests using the Conversation State
     (`ishti/core/state.py`, `resolver.py`; deterministic first, LLM rewriter only when state cannot resolve it,
     one clarifying question when the reference is ambiguous);
   - `planner.candidate/plan/run`: a clause split, and an LLM plan only when two or more skills match; independent
     steps run in parallel, `{prev}` chains steps, any pending confirmation stops the plan.
4. **Routing** (`ishti/router.py::route`), in this order:
   1. `corrections.learned` (what the owner corrected before wins);
   2. skill `match()` triggers from the manifests, ordered by manifest `priority` (deterministic, no model);
   3. Laya "typed-decisions" router over the skills whose manifest has `laya: true` (12 of 124; MEASURED by
      reading the manifests), accepted if confidence >= `router.min_confidence` (0.2 in `ishti/config.json`);
   4. otherwise the default skill, `chat`, which is the LLM-backed fallback.
   There is no separate "LLM router" pass: the LLM fallback is the `chat` skill, and the only other LLM call before
   a skill runs is the planner's split for multi-skill requests and the follow-up rewriter. Statuses `broken`,
   `draft`, `disabled`, `retired` and skills the owner disabled in Settings are never picked.
5. `_run`: tier gate (T3 refused unless `allow_t3`, T2 asks first, test mode blocks T2 side effects), then
   `skill.run(text, session)` inside `audit.context(...)` and `scope` enforcement. A skill that returns `None` means
   "not mine" and the next-best skill is tried. If a web skill's reply admits it fell short, one escalation hop
   runs (browser -> web_search -> deep_research, `ESCALATE` map, at most two hops).
6. `_finish`: sources filtered to the cited ones, secrets masked everywhere they could be stored, runtime history trimmed to
   the last 200 messages while older persisted turns remain searchable, chat persisted; then only background threads run (semantic remember, fine-tune log,
   arch2 consolidation, owner-fact learning, learning-turn scoring, monitor). Nothing in step 6 sits on the reply path.

Latency numbers (MEASURED, `docs/build-history/optimization/OPTIMIZATION_BASELINE.md`, `PERF_LOG.md`): the rule
route is about 0.24 ms, Laya about 35 ms at fp16, reflex about 0.003 ms. They depend on CPU frequency (the baseline
notes ~20% run-to-run drift).

## 4. Architectures that exist

| Name | What it is | How it is selected | Status |
|---|---|---|---|
| **arch1** (default) | The dispatcher cascade in section 3: reflex, rules, Laya, 124 skills, planner for multi-skill requests. | `ishti/config.json` -> `arch.active = "arch1"` | The default and the most-tested path. |
| **arch2** | Two-pass brain (`ishti/arch2/`): intent router shortlists 5-20 skills (rules, then Laya scores), the "main" model thinks/plans/calls tools, a validation pass judges each tool run, the "main" model writes the final answer. Adds the four-layer memory in `arch2/memory.py`. | `arch.active = "arch2"` in `ishti/config.json`, the dashboard's Architecture card (`GET /api/arch`, `POST /api/arch/switch`), or the spoken/typed command "use arch 2" (`ishti/arch2/registry.py` owns this state) | Implemented and switchable. Needs a live Ollama to run its LLM passes. |
| **Architecture profiles** (`ishti/core/architectures.py`) | Six declared profiles with component lists, required dependencies and health checks: `simple_voice`, `router_single_agent` (default), `memory_augmented`, `multi_agent_orchestrator`, `flowise_flow`, `custom`. Switching is health-check gated and hot. | `arch.active_profile` in `ishti/config.json`, or the typed/spoken command "switch to multi agent mode" (asks yes/no first). I found no REST route and no UI card for the profile registry. | See the limits below. |

Limits of the profile layer (checked by grep on 2026-10-02):

- The registry is reachable only from the dispatcher's command handler and from `arch.active_profile` in the config
  file. `docs/ARCHITECTURE`-era claims of `GET/POST /api/arch/profiles` and `/api/settings/architecture` do not match
  the code: neither route exists in `interface/web_ui/server.py` or any other module (grep for `/api/arch`,
  `/api/settings`). The dashboard's Architecture card calls `/api/arch` and `/api/arch/switch`, which switch
  arch1/arch2 only.
- Only two profiles change the dispatcher path: `multi_agent_orchestrator` hands the turn to `ishti/agents.py`
  (supervisor + specialist agents built over the same skills), and `flowise_flow` hands it to
  `ishti/flowise_client.py` (needs a Flowise server; the health check fails cleanly when none is configured).
- `simple_voice`, `router_single_agent`, `memory_augmented` and `custom` have no branch of their own: they run the
  normal arch1 path. Their per-profile `settings` dictionaries (`llm_role`, `tools`, `memory`, `shortlist_*`) are
  declared, and I found no code outside `architectures.py` that reads them. Treat those four as configuration
  labels today, not distinct pipelines.
- A legacy `arch.active = "arch2"` with no `active_profile` maps to the `memory_augmented` profile label
  (`_load_active`), but the arch2 pipeline itself is selected through `arch2/registry.py`.
- arch2's "main" and "validation" roles (config names `qwen3:14b`) run on whatever model the planner role currently
  runs on, not on a separately loaded 14B. `ishti/llm.py::model_for` does this on purpose because asking the governor
  for a model outside every ladder made the planner swap down after each reply (comment cites a 2026-10-02
  measurement). So switching to arch2 does not by itself load another model.

### Not implemented: voice_lite, four_role, agent_team, flow_hybrid

A task brief used four architecture-profile names: `voice_lite`, `four_role`, `agent_team` and `flow_hybrid`. They do
not exist in this repository. A case-insensitive search of the working tree (excluding `venv`, `node_modules`, `.git`,
`vendor`, `data`, `models`) finds zero occurrences, in code, config, tests or docs. The profiles that exist are the
six listed above. Nothing in this document, and nothing in the code, implements the four names; they should be
treated as unbuilt requirements, not features.

## 5. Skills

124 skill folders have a `manifest.json` (MEASURED: counted on 2026-10-02); 121 are `working` and 3 are `disabled`
(`desktop_control`, `internet_reach`, `security_scan`; they ship disabled by design and need the owner's yes).
`ishti/skills/legacy` and `_drafts` have no manifest and are not skills in this count. Each skill is a folder:

- `manifest.json`: `name`, `version`, `description`, `route` (the sentence Laya compares against), `priority`
  (trigger order), `laya`, `needs` (python packages, models, MCP servers, APIs), `status`, `tier`, `scope`,
  `category`, `test_file`.
- `skill.py`: `run(text, session) -> {"reply", "sources"} | None`, optional `match(text) -> bool`, and a
  `__main__` self-check.

`ishti/skills/__init__.py::load_skills` imports every skill, marks a skill `broken` if import or dependencies fail
(nothing else breaks), and `ishti/skills/registry.py` carries the owner's enable/disable flags (the `skills.disabled` setting in `ishti/core/settings.py`). Tiers T0-T3 and the
scope block are explained in [SAFETY.md](SAFETY.md); the catalog is in [SKILLS.md](SKILLS.md). Skills can be added
by hand, by the `skillsmith` skill (install from a repository, or generate), or by `skill_creator` (research, design,
build in a sandbox, vet, then a T2 install gate).

## 6. Memory

| Layer | Where | What it holds | Notes |
|---|---|---|---|
| Exact facts | `ishti/facts.py`, SQLite `data/ishti.db` | One row per (entity, attribute); an update replaces the value and keeps `previous`. Also the route log. | Always wins over semantic recall (the chat skill injects exact facts first and drops recalled text that duplicates one). |
| Semantic | `ishti/semantic.py`, ChromaDB `data/chroma`, embeddings from `nomic-embed-text` via Ollama | Fuzzy recall of past conversation | Recalls above cosine distance 0.45 are dropped (`config.json`/`NEEDS.md` #9: calibrated on few samples). |
| Conversation state | `ishti/core/state.py`, SQLite `data/convo_state.db` | Per session: active topic, task stack, entities, constraints, last searches | Plain JSON-able data; survives architecture switches. |
| arch2 memory | `ishti/arch2/memory.py`, SQLite `data/arch2_memory.db` | Semantic, episodic, procedural memories and observations, as unified memory objects with confidence and status; contradictions supersede rather than overwrite; decay only marks `needs_confirmation`, it never deletes | Written by every exchange via `arch2/consolidate.py` even when arch1 answers. A memory never authorizes an action. |
| Relationship graph | `ishti/rgraph.py`, SQLite `data/rgraph.db` | People, places, preferences and typed edges with first-seen and last-confirmed times; contradictions open a pending question | Private nodes are never exported to client context or training data. |
| Learning | `ishti/learning/`, `data/learning.db` | Feedback -> lessons -> policy hints and an offline dataset | Model weights are never changed at runtime; LoRA/QLoRA is offline in `orchestrator/finetune.py` behind an eval gate. Contract: [LEARNING_INTERFACE.md](LEARNING_INTERFACE.md). |
| Audit | `ishti/audit.py`, `data/audit.db` | Every action with who initiated it | See [SAFETY.md](SAFETY.md). |

Incognito mode (`ishti/core/incognito.py`) makes a conversation write nothing to memory.
`ishti/core/vault.py` encrypts sensitive fields of the arch2 memory DB and the relationship graph at rest (Fernet key in
the OS keychain via `keyring`, or a key file under `data/keys/`; no SQLCipher). The learning DB and conversation state
are plaintext. `ishti/core/migrations.py` applies versioned schema migrations to those three stores.
`data/` is git-ignored: all of this is personal runtime data, not part of the repository.

## 7. Models and the VRAM governor

Roles are named in `ishti/config.json` (`llm.models`): `planner` (the always-resident brain), `coder`, `fast`
(Laya, not an Ollama model), `translator`; plus `llm.fallback`. Every model call goes through `ishti/llm.py`
(Ollama's OpenAI-compatible `/v1` endpoint). `ishti/llm.py::model_for(role)` resolves a role to a model that is
actually installed and that the governor currently allows.

Configured at the time of writing (SOURCED, `ishti/config.json`): planner `ishti-spark2.5:4b`, coder
`qwen3-coder:30b-a3b`, translator `gemma3:4b`, fallback `ishti-qwen2.5:14b`. `NEEDS.md` N23 records that the
configured coder name was not installed when last checked; I did not re-check installed models, because that needs
a running Ollama.

The governor (`ishti/vram.py`, policy in `models_ladder.yaml`, docs in [arch/gpu-vram-governor.md](arch/gpu-vram-governor.md)):

- Hard rule: a model is never left partially offloaded (`size_vram < size` in Ollama's `/api/ps`). After every load it
  verifies `size_vram == size`; if not, it unloads and tries one rung lighter. `num_gpu` is never set.
- `measure`: total/used VRAM from `nvidia-smi` (ground truth), per-process numbers from Windows GPU counters, Ollama's
  own view from `/api/ps`; footprints are recorded in `data/vram.db`.
- `ladders`: per role, an ordered list of rungs, best first, only installed models, never re-quantized and never
  downloaded at runtime. A different context length is a variant alias `<name>-c<k>k:<tag>` with the same weights.
- `admit()`: before a heavy load, fits within `budget_pct` (80) of VRAM or, cheapest first: unload idle on-demand
  models, move the planner one rung lighter, reduce context, use a lighter rung of the new model's ladder, then
  queue with a plain reason. A downshifted role moves back up only after `hysteresis_s` (90 s) of free VRAM and never
  during a live voice turn.
- `ishti/models.py`: the planner and Laya are pinned; the coder, translator and vision models load on demand and
  unload after 10 minutes idle; each load/unload is logged to `data/model_usage.db`. `ishti/gpu.py::slot` lets one
  heavy non-Ollama job (upscaling, etc.) run at a time.
- Parameters (SOURCED, `models_ladder.yaml`): budget 80%, Ollama headroom 1536 MB, safety margin 128 MB, planner
  `min_ctx` 8192.

Measured footprints on the owner's machine (RTX 5070 Ti, 16303 MiB; MEASURED, [MODEL_DECISIONS.md](MODEL_DECISIONS.md)):
`ishti-spark2.5:4b` 8165 MiB fully on GPU at 8192 context, 84.6 tok/s; the 14B planner rung 10151 MiB; Whisper turbo
adds about 3047 MiB; Laya about 1.3-1.5 GB in the ISHTI process with a 3.8 GB peak while loading. These are one
machine's numbers, not a general claim.

## 8. Voice

Wake word (`ishti/wake.py`): openWakeWord ("hey jarvis" model on CPU) plus a whisper-tiny spotter for the other
configured phrases; Silero VAD ends the turn; barge-in pauses speech after about 120 ms of sustained voice and
compares a quick transcript with what ISHTI is saying (echo filter). STT (`ishti/voice.py`): faster-whisper
`large-v3-turbo` at float16 on CUDA, one decode per utterance, exactly one decoding thread (CTranslate2 is not
thread-safe; the crash that prompted this is `ISSUES.md` Issue 8). TTS (`ishti/talk.py`): the live voice is Kokoro
(CPU, no VRAM) with Piper voices as the classic fallback, spoken sentence by sentence while the model is still
writing. The global microphone switch defaults to OFF (`ishti/mic_control.py`). Details:
[arch/voice-pipeline.md](arch/voice-pipeline.md). Live microphone input has not been verified on this machine
(`NEEDS.md` N7, N39).

## 9. Lifecycle: boot, shutdown, process ownership

`ishti/lifecycle.py` is one state machine (`starting -> ready -> shutting_down -> stopped`). Contract:
[API_CONTRACT.md](API_CONTRACT.md) sections 2 and 4.

- **Boot stages** (`STAGES`): `backend_up`, `ollama_ready`, `memory_loaded`, `skills_loaded`, `planner_warm`,
  `voice_ready`, `ready`. Stages that do not wait for each other run concurrently in three chains:
  `ollama_ready -> planner_warm`, `memory_loaded`, `skills_loaded -> voice_ready`. Each stage is verified against
  reality (for `planner_warm`: the model is fully on the GPU and answered a one-word prompt), has a timeout, and is
  retried on failure, so a late Ollama start heals the boot. `voice_ready` is non-fatal: ISHTI reaches `ready`
  without a voice and lists the problem under `warnings`. Progress is published as `boot_stage` events on `/ws/events`
  and as `GET /api/ready`; timings are appended to `data/boot_times.json`.
- **Single instance**: a named mutex plus the registry file `%LOCALAPPDATA%\ISHTI\run\ishti.json`; a second launch
  asks the first to show its window and exits.
- **Job Object**: `adopt_job()` puts the process in a Windows Job Object so every child dies with it. Apps ISHTI opens
  *for the owner* are launched outside it (`install_launch_patches`) so they survive an ISHTI stop.
- **Ollama ownership**: if ISHTI had to start Ollama it stops it at shutdown; an Ollama that was already running is
  left running and only ISHTI's models are unloaded from it.
- **Graceful shutdown** (`POST /api/shutdown`, tray Stop, `stop.bat`): stop intake, cancel or finish tasks (bounded),
  flush SQLite/Chroma, stop voice and mic, unload models, close the MCP/browser servers ISHTI opened (never the
  owner's Chrome), release the port, exit; anything alive after `SHUTDOWN_HARD_S` (40 s) is force-killed as a tree.
  A shutdown flag file lets the desktop shell close its window without relying on the JS bridge.
- **Logs**: `%LOCALAPPDATA%\ISHTI\logs\ishti.log` (rotating).

## 10. Desktop shell

`desktop/launcher.py` (supervisor, singleton, OS shutdown signals handled by a detached shutdown child) and
`desktop/app_window.py` (pywebview 6, native Edge WebView2). The window opens on `desktop/splash.html` immediately;
`_open_dashboard` polls `GET /api/health` every 100 ms and switches the window to `http://127.0.0.1:8888` the moment
the HTTP server answers, while the models keep loading (the dashboard shows boot progress). `desktop/window_state.py`
sizes the first window to 92% x 94% of the primary monitor's work area, centred, then remembers size, position and
maximized state in `%LOCALAPPDATA%\ISHTI\window.json`, clamped to the current monitor layout. `desktop/timeline.py`
records launch milestones (launcher start, backend spawned, window shown, backend answering, dashboard loaded) for
`tools/launch_probe.py`. The `ISHTI.exe` built from `ISHTI.spec` is a tiny stdlib-only bootstrap that hands off to
`desktop/launcher.py` under the project venv; it is not a frozen copy of the app (rationale in the `.spec` header and
`desktop/exe_bootstrap.py`). The shell decision record is archived at
[build-history/decisions/DESKTOP_DECISION.md](build-history/decisions/DESKTOP_DECISION.md); its Tauri discussion no
longer matches what shipped.

## 11. HTTP and WebSocket surface

`interface/web_ui/server.py` is a FastAPI app. It mounts the dispatcher router (`/api/chat`, `/api/skills`,
`/api/facts`, `/api/stt`, `/api/voice`, ...), the lifecycle router (`/api/ready`, `/api/health`, `/api/shutdown`,
`/api/vram`, `/api/ollama/*`, `/ws/events`), the learning/memory router, the skills/agents/tasks/metrics router, and
the remote-channel router (`/api/remote/*`). Websockets: `/ws` (chat), `/ws/live` (Live voice session),
`/ws/events` (boot, VRAM, errors, traces). The built React app in `interface/app/dist` is served at `/`; if it is not
built the older templated HUD at `/hud` is served instead. Static assets are served through `CachedStaticFiles`
(`interface/web_ui/static_perf.py`: gzip once per file, immutable caching of hashed names). The authoritative,
machine-checked description is [API_CONTRACT.md](API_CONTRACT.md); `tests/test_lifecycle_contract.py` parses it.
Remote or tunnelled requests need the token in `data/api_token` (setup: `remote/tunnel_setup.md`).

## 12. Other subsystems worth knowing

- **`ishti/core/`** (the Prompt-1 core layer that sits under both arch1 and arch2): `state.py` and `resolver.py`
  (conversation state and query rewriting, section 3), `context.py` (pins the state summary at the top of the history,
  recent turns verbatim, older turns summarized once, `<think>` blocks stripped), `assemble.py` (one ordered context
  template with per-section character budgets, used by arch2), `pipeline.py` (STT/router/LLM/TTS stage Protocols and
  a Tracer that emits `{stage, status, input, output, error, ms}` events), `tools.py` (`ToolDispatcher` with timeouts
  and structured results, `ConfirmationPolicy`, `ValidationStage`; used by arch2 and tests), `settings.py` (pydantic
  schema with a secret split; see the note on its missing REST routes in section 14), `logutil.py` (JSON logs with
  PII/secret redaction). Regression tests that protect the context fix: `tests/test_context_regression.py` and
  `tests/test_dispatcher_state_flow.py` (offline).
- **Concurrency**: one chat, many requests (`View` + `INFLIGHT`); GPU work serialized by the governor and
  `gpu.slot`; the browser skill serializes pages per profile; threads started inside a request inherit the kill
  generation and the audit/scope context (`killswitch.propagate`).
- **Search and browsing**: local SearXNG (`http://127.0.0.1:8890`, Docker) first, with Wikipedia/GitHub/arXiv/Bing
  fallbacks; the Obscura headless reader (`ishti/obscura.py`) for page fetches; Playwright MCP servers configured in
  `config.json` (`mcp_servers`, `browser_profiles`) for interactive browsing.
- **Self-knowledge and repair**: `ishti/selfmodel.py` (live self-description), `ishti/health.py` (periodic checks,
  gaps logged to `NEEDS.md`), `ishti/self_heal.py` (off by default, manual trigger), `ishti/monitor.py` (passive).
- **Behavior data**: `ishti/ftlog.py` logs exchanges (secrets/phones/emails redacted, corrections upgrade quality);
  `braintrain` and `orchestrator/finetune.py` export and train offline.
- **Overlay and dictation** (5-repo integration): `desktop/overlay/`, `ishti/overlay_pipe.py`,
  `ishti/hooks_relay.py`, `ishti/dictation.py`. Status of each is in `REPO_FEATURE_MATRIX.md`.

## 13. Source map

File-level descriptions live in the generated, now-dated snapshot
[build-history/optimization/CODEMAP.md](build-history/optimization/CODEMAP.md) (2026-10-01; regenerate before
relying on it for new files). Per-skill detail: `ishti/skills/<name>/manifest.json` and the generated
[SKILL_CATALOG.md](SKILL_CATALOG.md).

## 14. What this document does not cover or could not verify

- It does not re-measure latency or VRAM; numbers are cited from the files named beside them.
- A targeted 2026-10-04 probe found the planner 4B and Gemma 4B models fully resident according to Ollama /api/ps,
  and ran nine synthetic local answer-quality prompts. Live boot timing and microphone behavior remain unconfirmed.
- `docs/SETTINGS.md` documents `/api/settings*` REST routes and a `/settings` page. `ishti/core/settings.py` (the schema
  and service) is real and used by several modules, but I found no `/api/settings` route in any Python file and no
  `/settings` page route; `interface/web_ui/static/settings.html` calls routes that are not mounted. See the status
  note at the top of SETTINGS.md.
- Earlier architecture write-ups are archived in [build-history/superseded/](build-history/superseded/):
  `ARCHITECTURE_root_2026-10-01.md`, `ARCHITECTURE_prompt1_2026-09-29.md`, `PROJECT_MAP_2026-09-28.md`. Their
  unique content is merged above, with corrections (the project map's port 8000, "40/47 skills" and 14B-planner
  statements are out of date).

## 15. New local paths under verification (2026-10-04)

- WhatsApp input: the Inputs page starts ishti.wa_bridge, which launches the local Node bridge in tools/wa_bridge.
  The Python side stores the bootstrap credential with DPAPI; the Node side encrypts linked-device state and
  attachments. The bridge retains only events observed since its start. The WhatsApp skill tries an exact bridge
  recipient and T2 send approval first, with the existing Web path for unresolved contacts and older history.
  The currently linked account does not match the owner-authorized self-test number, so live bridge media checks
  have not run.
- Incoming media: the bridge downloads only an exact observed message. ishti.wa_media_reader runs in a child process
  with a deadline, memory cap and no external network. Synthetic PDF and PNG fixtures pass field extraction;
  image OCR reported a disagreement which is surfaced to the user.
- Answer feedback: the Chat UI sends a saved message ID and positive/negative rating to the session feedback route.
  A dislike runs a text-only local-model revision; rejected answers are removed from the next prompt history.
  Local feedback rows and revisions are durable, but they do not update model weights.
- Visual fallback: screen_control uses screenshot OCR coordinates and one-action T2 approvals. The multi-screen
  planner proposes one structured action from a screenshot, and the executor checks approval, foreground window,
  fresh capture and monitor coordinates before applying it. Live desktop controls remain unverified.

## 16. Audit repairs (2026-10-05)

- General PDF/Office text extraction uses `ishti.document_reader` and a bounded child worker. Editing/conversion
  still needs isolation; resource limits and Python network blocking do not constitute an OS security sandbox.
- Browser refresh no longer requests backend shutdown. Explicit Quit and the native window-close event retain
  shutdown handling. Chat streams, uploads and feedback use generation guards across session changes.
- Upload scanners run in a dedicated pool of two workers; failures, cancelled scans and disconnected streams
  remove staged files. Production upload encryption/retention integration remains incomplete.
- With both test-mode flags enabled, default core stores use a per-process scratch namespace; explicit fixture
  paths are preserved and dispatcher background learning is disabled. This does not isolate direct skill calls.
- Audit directory preparation runs outside skill scoping, while action rows retain their original actor metadata.
  A recursive-record guard prevents scope-denial logging from deadlocking; skill permissions remain unchanged.
