# ISHTI: local-first personal AI assistant

Runs 100% on this PC after setup: Ollama LLMs, Laya routing, SQLite + ChromaDB memory, faster-whisper STT, Piper TTS and the Playwright MCP browser. No cloud API calls at runtime.

```
request (text or voice)
   │
   ▼
dispatcher (FastAPI, ishti/dispatcher.py)
   ├─ pending confirmation?  → back to the skill that asked
   ├─ skill triggers (regex)  ─┐
   ├─ Laya typed decisions    ─┼→ skill  →  reply (+ Piper audio)
   └─ default: chat           ─┘
          skills: memory · weather · web_search · browser (MCP) · chat · legacy
          memory: exact facts (SQLite)  >  semantic recall (ChromaDB)
```

## Run

```bat
start.bat                 :: health checks, then HUD at http://127.0.0.1:8888
python -m ishti [port]    :: server only
python -m ishti.check     :: end-to-end check: routing accuracy + memory + live skill + LLM
```

First-time model setup (the `/v1` API can't set context size per request, so 12k-context tags are created once):

```bat
ollama pull qwen2.5:14b
ollama pull qwen2.5-coder:14b
ollama pull nomic-embed-text
ollama create ishti-qwen2.5:14b -f ishti\planner.Modelfile
ollama create ishti-qwen2.5-coder:14b -f ishti\coder.Modelfile
venv\Scripts\python -m piper.download_voices en_US-lessac-medium --data-dir data\piper
```

The Python packages are in `venv` (`torch` cu128, `laya`, `chromadb`, `mcp`, `faster-whisper`, `piper-tts`, `openai`, `fastapi`). The browser MCP server runs through `npx @playwright/mcp` (Node 24).

## Layout

| Path | What |
|---|---|
| `ishti/config.json` | Models per role (planner/coder/fast), memory paths, search roots, router, MCP servers, voice. **Swap models here, no code changes.** |
| `ishti/dispatcher.py` | `handle(text, session_id, channel)`, plus `/api/chat`, `/api/voice`, `/api/stt`, `/api/skills`, `/api/facts` |
| `ishti/router.py` | Skill triggers → Laya `typed-decisions` (fp16, ~35 ms): skill choice + per-skill yes/no + `router_questions()` → default |
| `ishti/llm.py` | The only LLM client (Ollama OpenAI-compatible `/v1`) |
| `ishti/facts.py` | Exact fact store: one row per (entity, attribute), updated in place, previous value kept; routing log |
| `ishti/semantic.py` | ChromaDB + `nomic-embed-text` for conversation context |
| `ishti/mcp_client.py` | Thin MCP client: persistent stdio sessions per configured server |
| `ishti/voice.py` | faster-whisper `large-v3` (CUDA, int8_float16) + Piper |
| `ishti/wake.py` | Hands-free: say **"hey jarvis, …"** (openWakeWord) → record until silence (Silero VAD) → dispatcher → spoken reply |
| `ishti/skills/<name>/` | `manifest.json` + `skill.py` per skill |
| `interface/web_ui/` | HUD (FastAPI + WebSocket); `/api/execute` goes through the dispatcher |
| `orchestrator/`, `tools/`, `browser/`, `desktop/`, `voice/` | Pre-dispatcher code, reused by skills (weather, web search, legacy routes) |
| `AUDIT.md` / `NEEDS.md` | What was found broken and fixed / what's blocked and why |

## Memory

- **Exact facts win.** "Project Falcon is on D: at D:\work\falcon" stores that path. "Where is falcon?" answers straight from SQLite, with no searching.
- **Changes update the same record.** Examples: "I moved falcon to D:\archive\falcon", "I finished falcon", "this is now working" ("this" means the last project mentioned).
- **Unknown locations:** ISHTI searches D:, then Desktop, then Downloads (breadth-first, depth 4, 10 s per root), lists what it found, and saves your pick.
- **"open falcon"** (or "open falcon in vs code" / "in terminal") opens the saved location.
- **Wrong skill?** Say "that should have been web search" (or "use the browser for that"). The last request re-runs with that skill, and the correction is logged as training data for Laya.
- **Semantic memory** (ChromaDB) holds past conversation. Recalls that mention an entity with an exact fact are dropped.

## Skills

Each skill is a folder with a manifest:

```json
{"name": "weather", "version": "1.0.0", "description": "...",
 "route": "weather, temperature, rain or forecast for a place and day",   // Laya's criterion
 "priority": 20,                                                          // trigger order
 "needs": {"python": ["requests"], "models": [], "mcp": [], "apis": ["open-meteo.com"]},
 "status": "working"}                                  // working | needs-upgrade | broken
```

`skill.py` exposes `run(text, session) -> {"reply", "sources"} | None`. `None` means "not mine", and the dispatcher tries the next choice. It can also expose `match(text) -> bool` as a deterministic trigger. If a skill fails to import or is missing a dependency, it is marked `broken` and skipped; nothing else breaks. Upgrading a skill means replacing its folder.

| Skill | Does | Built on |
|---|---|---|
| memory | locations, statuses, remembered facts | SQLite, file search |
| weather | real forecast | Open-Meteo (free, no key) |
| web_search | live answers with citations | Bing Lite / Wikipedia / GitHub / arXiv + page reader |
| browser | open sites, read, click, fill forms | Playwright MCP + local LLM tool calls |
| chat | everything else; code questions use the coder model | Ollama |
| legacy | PC file search, app launch, video edit, telemetry, research reports, URL reading | `orchestrator/crew.py` (being split up, NEEDS.md #4) |

**Adding a skill:** first look for an open-source library or repo that already does it, and wrap it (as `weather` wraps Open-Meteo and `browser` wraps Playwright MCP). If only a commercial product does it, write `ishti/skills/<name>/SPEC.md` describing how it works (from public docs/demos) and how to build it locally, then log it in NEEDS.md for a later build session.

## Safety

- Consequential actions (buy, pay, send, submit, post, delete, log in, Enter on a submit form) stop and wait for an explicit "yes". A timeout or error means **no**.
- Unknown app names are refused rather than passed to a shell. Shell commands from the legacy planner always need confirmation.
- The browser runs in its own profile (`data/mcp_browser_profile`), never your daily Chrome profile.
- The HUD mic records locally and is transcribed by whisper on this PC (Chrome's Web Speech API, which sends audio to Google, was removed).
