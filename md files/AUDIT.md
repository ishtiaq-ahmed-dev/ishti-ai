# ISHTI Audit (Step 0)

Date: 2026-09-26. Scope: every file under `D:\ISHTI`, excluding `venv/` (1.2 GB) and `data/chrome_profile/` (230 MB of Chrome's own cache, about 29k files).
The earlier audit in `docs/AUDIT.md` (2026-09-24) covers the prompt-contamination bugs that were fixed then. This one checks what runs **today**.

## How it was checked

| Check | Command | Result |
|---|---|---|
| Syntax | `python -m compileall orchestrator browser desktop voice tools interface` | all compile |
| Imports | `python -c "import <module>"` for all 21 modules | all import |
| Module self-checks | `python <file>` for persona, response_guard, intent_detector, relevance, weather, tts | all pass (weather fetched a live forecast) |
| Existing E2E | `orchestrator/test_e2e.py` (real Ollama, real web) | **30/31 pass**; the TTS-bytes assertion fails |
| Self-heal test | `orchestrator/test_self_heal.py` | pass |
| Environment | Ollama 0.31.2 with 6 models, RTX 5070 Ti 16 GB, Node 24, gh logged in | OK |
| Packages present | fastapi, uvicorn, openai, chromadb 1.1.1, mcp 1.28.1, faster-whisper, playwright, pyttsx3, sounddevice | OK |
| Packages missing | `torch`, `laya`, `ollama` (py client), `piper-tts`, `pvporcupine` | installing the first four; pvporcupine not needed |
| Model missing | `qwen2.5-coder` (the spec's code model) | pull needed (NEEDS.md) |

**After the fixes:** `orchestrator/test_e2e.py` passes 31/31. The new core's `python -m ishti.check` passes (routing 24/24, memory, live skill, LLM).

## What exists

| Path | Purpose | Status | Verdict |
|---|---|---|---|
| `orchestrator/crew.py` (1098 LOC) | Old monolithic engine: regex intent → 10 hard-coded routes + LLM-planned DAG | runs | **keep as `legacy` skill** behind the new dispatcher |
| `orchestrator/intent_detector.py` | Regex intent + depth classifier | runs, self-check passes | keep (used by crew); new routing is Laya |
| `orchestrator/planner.py` | LLM → JSON task DAG | runs | keep (legacy) |
| `orchestrator/deep_research.py` | quick search + multi-query research reports | runs | **reuse** in the `web_search` skill |
| `orchestrator/model_router.py` | Ollama `/api/generate` wrapper + model benchmark | runs | keep (legacy); new code uses the OpenAI-compatible `/v1` endpoint |
| `orchestrator/persona.py`, `response_guard.py` | System prompt builder, refusal/contamination guard | self-checks pass | **reuse** `response_guard` in the new dispatcher |
| `orchestrator/self_heal.py` | retry/backoff wrapper + reflections log | test passes | keep (legacy) |
| `orchestrator/doctor.py`, `startup.py` | diagnostics, launcher | runs | keep |
| `orchestrator/memory/user_profile.py` | JSON owner profile/prefs/projects/notes | runs | keep for profile + prefs; **exact facts move to SQLite** (`ishti/facts.py`) |
| `orchestrator/memory/conversation.py` | JSON chat sessions | runs | keep (HUD session list uses it) |
| `orchestrator/memory/capability_registry.py` + `capability_escalation.py` (800 LOC) | hand-written capability list + probes | runs | **redundant** with skill manifests; kept for legacy/doctor, flagged for removal later |
| `orchestrator/memory/task_state.py`, `error_logger.py` | task state machine, error log | run | keep |
| `tools/web/*` | search (Bing Lite/Wiki/GitHub/arXiv/Playwright), fetch cascade, relevance, Open-Meteo weather | run, live-verified | **reuse** as skills |
| `tools/video/video_editor.py` | FFmpeg/NVENC trim/merge/vertical | imports | keep (legacy) |
| `browser/browser_agent.py` | Playwright agent, isolated profile | runs | keep (legacy); new browser tool = Playwright **MCP** server |
| `desktop/computer_use.py` | app launch, PC file search, mouse/keyboard, telemetry | runs | keep, **security fixes** below; `search_pc` reused for location search |
| `voice/stt.py` | faster-whisper, CUDA with CPU fallback | imports | **reuse** in the voice channel |
| `voice/tts.py` | SAPI5 via pyttsx3 | speaks | keep as fallback; Piper becomes primary |
| `voice/wake_word.py` | energy-threshold wake word (whisper on every fixed 2 s chunk, so commands got cut) | imports, unused | **removed** in iteration 2: replaced by `ishti/wake.py` (openWakeWord + Silero VAD) |
| `interface/web_ui/*` | FastAPI + WebSocket HUD on :8888 | runs | **keep as the frontend**; `/api/execute` now goes through the new dispatcher |
| `start.bat`, `ishti.bat` | launchers | run | keep, `ishti.bat` injection fixed |
| `docs/*.md` | earlier audit, fix plan, test results | - | keep as history |
| `assets/*` | logos/screenshots | - | keep |

## What's broken (specific errors)

| # | Where | Error / defect | Fix |
|---|---|---|---|
| B1 | `interface/web_ui/server.py` `confirmation_gate_ui` | Calls `asyncio.get_event_loop()` from a worker thread. That raises, and the `except` branch sets `approved = True`. **Every consequential action (send/publish/pay) auto-approves.** | Capture the server loop at startup; timeout or error = **deny** |
| B2 | `orchestrator/crew.py` `_request_user_confirmation` | Non-interactive stdin or any exception = auto-approve | default **deny** |
| B3 | `orchestrator/crew.py` `_execute_system_command` | `NameError: name 'subprocess' is not defined` (never imported), so the route always fails. Also: LLM-planned PowerShell guarded only by a 5-string blocklist, and gated only if the planner happens to flag it | import it, and **always** route `system_command` through the confirmation gate |
| B4 | `desktop/computer_use.py` `launch_app` | Unknown app names run through `shell=True` / `os.system("start ...")`, so text from a request is shell-injected (`open calc & del ...`) | unknown names are refused; URI schemes go through `os.startfile` |
| B5 | `ishti.bat search/find` | `%~2` is pasted inside a `python -c '...'` string, so a query containing `'` breaks out of it | pass the query through `sys.argv` |
| B6 | `orchestrator/test_e2e.py` test I | `pyttsx3.save_to_file` writes a 46-byte empty WAV in this process | Piper becomes primary TTS; the test checks Piper output |
| B7 | `test_capabilities.py`, `doctor.py` tests 16-17 | write "Test Project", "my favorite color is blue" and `test_research_topic_*` into **production** memory (the S3 issue from the old audit came back) | fixed: `user_profile.scratch_memory()` redirects owner-memory + research writes to a temp copy; test_capabilities, test_e2e and doctor 16-17 use it (and test_capabilities also overwrote the owner's role with "Senior AI Engineer" - the old S6 bug's origin) |
| B11 | `interface/web_ui/static/js/ishti.js` mic button | used the browser Web Speech API, which in Chrome streams the owner's audio to Google, breaking "100% local" | replaced with MediaRecorder -> `/api/stt` -> faster-whisper on this PC |
| B12 | `tools/web/search.py` BingLite + Playwright-Bing | found in iteration 3: Bing now answers scraped multi-word queries with pages for the first word only ("current state of X" gave banking sites), so research reports cited irrelevant sources | local SearXNG container is now the primary provider; Bing kept as a last fallback |
| B13 | `tools/web/fetch.py` step 2 | found in iteration 4: the "reader mode" fallback sent every URL ISHTI read to r.jina.ai (a third-party cloud service), taking 10-12 s per page, which alone doubled web-answer latency | removed. HTTP then local headless Chrome; quick_search reads the top 3 pages under a 4 s budget (web answers went from 15-19 s to 8-10 s) |
| B14 | HUD source cards | every source showed a "VERIFIED" badge, including pages never read (the old F9, in the UI) | badge only when the page was actually read |
| B15 | `tools/web/weather.which_day` | "day after tomorrow" contains "tomorrow" and was checked second, so it always returned tomorrow; weekday names and "weekend" fell back to today | fixed order; weekdays and weekend supported (Open-Meteo gives 7 days) |
| B16 | `conversation.new_chat` + tests | every HUD load / new-chat click made another empty session, and test suites wrote chat sessions into the real history | an empty latest session is reused; `scratch_memory()` also redirects sessions |
| B17 | weather trigger | "write a haiku about Peshawar rain" went to the weather skill ("rain" was a hard trigger) | rain/snow only as forecast questions; creative-writing requests go to chat |
| B18 | owner commands | "mute", "read that", "run diagnostics", "what can you do" lived inside crew.py and stopped working once the dispatcher took over (iteration 1) | restored as the `owner` skill (iteration 7) |
| B19 | wake word → HUD (iteration 2) | voice exchanges were broadcast as `task_completed_full`, which the HUD never handled, so they didn't show despite the claim | new `assistant_message` event rendered in the chat feed (voice + reminders), verified in the browser |
| B10 | `crew._handle_owner_command` "forget X" | matches only the internal key (`note_1790343966`), so "forget favorite color" never forgets anything. Doctor test 17 passes anyway because it checks only the route | superseded by the SQLite fact store (`ishti/facts.py`), which forgets by entity name |
| B8 | `orchestrator/memory/skills/learned_*.json` (5 files) | junk "learned skills" from test prompts (`hey_hd`, `hello_ki_hal_chaal_hai`, `new_unique_task_for_learning`, ...) that later prompts match as noise | deleted (listed here) |
| B9 | `orchestrator/memory/research/` | 2 `test_research_topic_*` test artifacts + 14 duplicate "latest AI news" runs | test artifacts deleted; real runs kept, but git-ignored |

## Duplicate / redundant

- Routing exists 3 times: `intent_detector.py` (regex), `planner.py` (LLM), `capability_registry.can_handle` (keywords). The new Laya router replaces all three at the top level; they survive only inside the `legacy` skill.
- Owner facts live in 4 JSON files plus a 554-line capability registry. Exact facts get one SQLite table instead.
- Two Ollama clients (`model_router.generate` on `/api/generate`, and raw `requests.post` calls in older code). New code uses only `ishti/llm.py` (OpenAI-compatible `/v1`).
- Two browser stacks: `browser_agent.py` (legacy) and the new Playwright MCP server.

## Iteration 7: old engine removed

`crew.py`, `planner.py`, `self_heal.py`, `test_self_heal.py`, `memory/task_state.py`, `memory/capability_registry.py`, `memory/capability_escalation.py` and `test_capabilities.py` (about 3,300 LOC) were deleted once every route had its own skill. `test_e2e.py` (31/31), `doctor.py` and `startup.py` were ported to the dispatcher first. `model_router.py` + `persona.py` stay (used by `deep_research` and the HUD status/benchmark), as does `intent_detector.py` (the skills' trigger library).

## Safe to keep vs discard

- **Discarded:** the 5 `learned_*.json` junk skills (B8), the 2 `test_research_topic_*.json` files (B9), and the duplicate "Test Project" row in `projects.json`. Nothing else is deleted.
- **Not committed (git-ignored, left on disk):** `venv/`, `data/` (Chrome profile, sessions, logs, test media), runtime memory JSON (personal data: name, city, notes), `__pycache__/`.
- **Everything else is kept.** Legacy modules keep working through the `legacy` skill until each route is replaced by its own skill. Retirement is tracked in NEEDS.md.
