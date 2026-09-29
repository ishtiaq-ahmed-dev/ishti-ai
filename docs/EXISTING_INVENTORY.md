# EXISTING_INVENTORY — audit of the project as found (2026-09-29, before Prompt-1 changes)

Status legend: **OK** = works, verified this pass · **PARTIAL** = exists, gaps listed ·
**BROKEN** = fails · **DEAD** = present but never called · **UNTESTED** = no test covers it.

## 1. What this project is

ISHTI — a local-first personal assistant for one owner's Windows 11 PC (RTX 5070 Ti 16 GB,
64 GB RAM). Everything runs locally: Ollama LLMs, faster-whisper STT, Kokoro/Piper TTS,
SQLite + ChromaDB memory, SearXNG search (Docker), Playwright-MCP browser. No cloud APIs at
runtime. The repo root is `D:\ISHTI`; Python venv at `venv\`.

Two generations of architecture coexist by design:
- **arch1** — the proven dispatcher (`ishti/dispatcher.py`): deterministic regex triggers →
  Laya typed-decision router (0.2 ms local model) → 80+ skills. Default.
- **arch2** — the two-pass pipeline (`ishti/arch2/`): shortlist skills → Qwen3-class model
  thinks/plans/calls tools → validation pass → final answer. Switchable at runtime via
  `ishti/arch2/registry.py` (hot switch, persisted to `ishti/config.json`, voice command
  "use arch 2", dashboard Settings → Architecture card).

## 2. Folder structure & entry points

| Path | Role |
|---|---|
| `ishti/__init__.py` | UTF-8 console fix, `ROOT`, loads `ishti/config.json` into `CONFIG` |
| `ishti/__main__.py` | `python -m ishti [port]` — FastAPI server (dispatcher API routes) |
| `ishti/dispatcher.py` | **The heart**: `handle(text, session_id, channel)` → route → skill → reply; `/api/chat`, `/api/voice`, `/api/stt`… |
| `ishti/arch2/` | Two-pass pipeline: `pipeline.py`, `registry.py` (active-arch owner), `memory.py` (5-table store), `retrieval.py`, `consolidate.py`, `jsonio.py` |
| `ishti/skills/<name>/` | 80+ skills, each `manifest.json` + `skill.py` (`run(text, session) -> {reply, sources}`) |
| `ishti/voice.py` / `talk.py` / `wake.py` / `mic_control.py` | faster-whisper STT (one decode thread), Kokoro/Piper TTS streamed per sentence, openWakeWord + Silero VAD barge-in, global mic state |
| `ishti/llm.py` | The only LLM client (Ollama `/v1`, model-per-role from config, fallbacks, junk-line cleaner, degraded-start of Ollama) |
| `ishti/facts.py`, `semantic.py`, `rgraph.py` | Exact facts (SQLite UPSERT), Chroma semantic memory, relationship graph |
| `ishti/router.py`, `planner.py`, `reflex.py`, `scope.py`, `killswitch.py`, `audit.py`, `untrusted.py` | Laya routing, multi-step planner, instant reflexes, per-skill scopes, kill switch, audit log, prompt-injection fences |
| `ishti/learning/`, `ftlog.py`, `monitor.py`, `corrections.py`, `skill_lifecycle.py` | Behavior-data → lessons → QLoRA pipeline (never changes weights at runtime) |
| `interface/web_ui/server.py` | HUD gateway FastAPI :8000 — 37+ REST routes, WebSocket telemetry, serves React `dist/` + legacy static |
| `interface/app/` | React+TS+Vite HUD (chat, thinking graph, sessions, research view) |
| `orchestrator/` | `memory/conversation.py` (chat session store), `deep_research.py`, `doctor.py`, `startup.py`, `finetune.py`, `model_router.py` |
| `tools/`, `desktop/`, `browser/`, `voice/` | Shared tooling: web search/fetch, FFmpeg/capcut, computer-use, browser agent, tray |
| `tests/` | 17 test files (see §4) |
| `docs/` | AUDIT.md, FIX_PLAN.md, LEARNING_INTERFACE.md, TEST_RESULTS.md, UX_MOTION.md |
| `start.bat` / `stop.bat` | Windows start (desktop window / `cli` / `server` modes) and clean stop |

App start: `start.bat` → venv → singleton check (`desktop/focus_ishti.py`) → `python -m ishti`
(FastAPI on :8000, HUD served) + optional wake-word listener when mic was left ON.

## 3. Pipeline parts (mapped to the spec's stages)

| Spec stage | Exists as | Status |
|---|---|---|
| STT | `ishti/voice.py` faster-whisper large-v3-turbo (CUDA), languages en/ur/ps, forced-language lock, idle unload; `Stream` class for mic/VAD; `/api/stt` | OK (tested: `test_stt_threads`, `measure_stt`) |
| Push-to-talk / VAD | `ishti/wake.py` (wake phrase → record till silence, Silero VAD), `ishti/voice.Stream`, barge-in with echo filter | OK, hardware-dependent |
| Text-input fallback | `/api/execute` (no audio), CLI REPL (`start.bat cli`) | OK |
| Intent routing | `ishti/router.py` (deterministic `match()` per skill → Laya typed-decisions scores) | OK — routing eval 329/329 (this pass) |
| Embeddings shortlist | arch2 `pipeline.shortlist()` (Laya scores rank skills, top 5–20 window) | OK (offline parts tested) |
| LLM | `ishti/llm.py` (roles planner/coder/fast/translator; arch2 roles main/validation from `config.arch.arch2.models`) | OK — verified live this pass |
| Tool calling | arch1: skill dispatch in dispatcher; arch2: JSON tool calls parsed by `arch2/jsonio.py`, unknown-tool recovery | OK |
| Tool validation | arch2 validation pass (JSON-constrained, safe default); dispatcher `fell_short` + escalation chain; web_search's own per-page checks | OK |
| Policy / confirmation | T2 gates: `ask_first` yes/no pending slot, repeat-guard, scopes, T3 block, audit, killswitch | OK (tested in `ishti.check`) |
| Final answer | arch1 chat skill / arch2 pass-2 with evidence block | OK |
| TTS | Kokoro-82M live voice roster (6 voices) + Piper fallback, sentence-streamed (`talk.Talker`), ducking, barge-in stop | OK |
| Multi-step planning | `ishti/planner.py` (compound requests → steps, `{prev}` carries answers) | OK (tested) |
| Conversation memory | `orchestrator/memory/conversation.py` (session JSON store), in-memory `SESSIONS` history (last 20 msgs), Chroma recall | PARTIAL — see §6 |
| Observability | trace events (Thinking panel + WS), audit log, health loop, `PERF_LOG.md`, model-usage DB | OK (OpenTelemetry export = future) |

## 4. Tests (A2 — run this pass)

`venv\Scripts\python -m pytest tests/` → **35 passed in 59.4 s** (all 17 files collected;
`test_stt_accuracy.py` skipped-by-marker where it needs live audio). Self-check gates beyond
pytest: `python -m ishti.check` (core loop), `tools/check_skills.py`, `tools/verify_all.py`
(115 self-checks), `tools/smoke_reverify.py` (live-fire routing).

## 5. End-to-end run (A3 — this pass, text mode)

`python -m ishti.check` on the live machine: **OK** —
- skills load clean; routing eval **329/329 @ 0.2 ms median**
- memory round-trip ("Project ISHTI is on D:…" → "where is the ishti project?" → answered from facts)
- live skill (weather via open-meteo) — 1.3 s
- local LLM (`ishti-qwen2.5:14b` answered 17×23 = 391) — 2.1 s
- compound request through the planner (RAM status **and then** weather) — 2.4 s, both steps ran
- Ollama running with: gemma3:4b, ishti-qwen2.5(-coder):14b, qwen2.5(-coder):14b, llama3.2:3b,
  nomic-embed-text, deepseek-r1:8b (+2 local customs). **qwen3:14b is NOT pulled** — config
  falls back to ishti-qwen2.5:14b until the owner pulls it.

## 6. The critical bug — assistant forgets what it was researching in the same chat

Verified root causes in the code as found:

1. **Only explicit follow-up phrasings are resolved.** `dispatcher.contextualize()` rewrites the
   message only when `FOLLOW_UP` regex matches ("search now", "why didn't you…"). "go deep
   research the internet and find the cheapest price" matches nothing → routed verbatim →
   `deep_research`/`web_search` search the raw words with **no topic**.
2. **No session topic state.** `SESSIONS[sid]` keeps only `history`, `pending`, `last_entity`,
   `last_t2`, `tasks`. Nothing stores "we are researching Sony WH-1000XM5".
3. **Searches are not recorded.** `web_search`/`deep_research` never persist their queries or
   result URLs anywhere in session state (only HUD trace events).
4. **History truncation.** arch2 pass-1 uses `history[-8:]`; dispatcher keeps `[-20:]`; no
   summarization of older turns; tool outputs can be megabytes in the *research view* but are
   not summarized into history.
5. **No entity/constraint extraction.** budget/country/currency/brand mentioned once are lost.
6. arch2 retrieval (`assemble_context`) recalls long-term memory by keywords but is not given
   the chat's active topic as an anchor.

## 7. Duplicated / dead / untested code noticed

- `arch2.registry` and the new needs (profile registry, health checks) overlap — Prompt 1
  composes them instead of duplicating (arch1/arch2 stay; profiles map onto them).
- `dispatcher.FELL_SHORT` + `fell_short()` LLM judge + web_search's own confidence: three
  overlapping validation layers (kept — each covers a different skill class; now also fed by
  the resolved request).
- `interface/web_ui/static/js/ishti.js` — legacy HUD JS still served but the React app is
  primary; settings UI added in Prompt 1 goes to a standalone `/settings` page (no rebuild).
- `orchestrator/test_e2e.py` exists but is not wired into pytest.
- No `requirements.txt`/`pyproject.toml` at repo root — dependencies live in `venv\`
  (documented in README: torch cu128, laya, chromadb, mcp, faster-whisper, piper-tts,
  openai, fastapi; plus pydantic 2.12.5, pyyaml present). Versions verifiable via
  `pip list` (see §8).

## 8. Key dependency versions (venv, this pass)

pydantic 2.12.5 · fastapi (bundled starlette, httpx2-deprecation warning present) · openai
client (Ollama-compatible) · faster-whisper (CTranslate2 CUDA) · piper-tts · chromadb ·
pytest · pyyaml. Node 24 for `npx @playwright/mcp`. Docker for SearXNG.

## 9. Settings/config handling as found

- `ishti/config.json` — single JSON config (models per role, arch/arch2, memory paths,
  router, MCP servers, browser profiles, voice roster). Edited by hand or by
  `arch2.registry.set_active()` (the only programmatic writer).
- `orchestrator/memory/user_profile.py` — owner profile/preferences/projects JSON files.
- Learning runtime overrides in a DB settings table (`/api/learning/settings`).
- **No unified typed settings schema, no validation, no import/export, no secrets store** —
  Prompt 1 adds `ishti/core/settings.py` (pydantic) + `/api/settings*` routes.
