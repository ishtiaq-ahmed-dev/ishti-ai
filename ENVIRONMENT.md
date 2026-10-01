# ENVIRONMENT — ISHTI optimization session, 2026-10-01

Every fact measured live this session (re-run the commands to re-derive).

## Host

| Item | Value | Evidence |
|---|---|---|
| OS | Windows 11 build 26100 | `winreg HKLM\...\CurrentVersion\CurrentBuildNumber` (selfmodel probe_os) |
| GPU | RTX 5070 Ti 16 GB | `nvidia-smi --query-gpu=name,memory.total` |
| RAM | 64 GB | psutil via selfmodel; BASELINE cross-check passes |
| Project | `D:\ISHTI`, branch `main` | `git status` |
| Rollback point | tag `pre-optimization` → commit `28270e0` | `git tag -l pre-*` |
| Data backup | `backups/pre-optimization_20261001/` — 1059 files, 35 SQLite DBs via `sqlite3.backup()` (consistent snapshots), 0 errors, 46 MB | `backups/.../MANIFEST.json` |

## Python venv (measured `pip freeze`, curated)

| Package | Version | Package | Version |
|---|---|---|---|
| Python | 3.11.9 | pytest / pytest-timeout | 9.1.1 / 2.4.0 |
| fastapi | 0.141.1 | chromadb | 1.1.1 |
| uvicorn | 0.53.0 | faster-whisper | 1.2.1 |
| httpx | 0.28.1 | torch (cu128) | 2.11.0 |
| pydantic | 2.12.5 | pywin32 | 312 |
| openai | 2.54.0 | psutil | 7.2.2 |
| laya | 0.3.20 | piper-tts | 1.8.0 |
| mcp | 1.28.1 | openwakeword | 0.6.0 |
| onnxruntime | 1.30.0 | numpy | 2.4.6 |
| requests | 2.34.2 | langchain-ollama | 1.1.0 |
| ollama (client) | 0.6.2 | pydantic-settings | 2.15.0 |

No telegram libs (channels use raw Bot API over httpx — RESEARCH.md R1). Node 24.16.0 (MCP browser via npx @playwright/mcp).

## Ollama state this session

- Started BY THE OWNER before the session (never started un-supervised by an agent — rule). UP at 127.0.0.1:11434 (pid 13764).
- Resident at session start: `qwen2.5-c8k:7b` (5134 MiB, fully on GPU). After benches: `ishti-spark2.5:4b` (8165 MiB, `size_vram == size`, no partial offload).
- Installed: 25 models (see `curl 127.0.0.1:11434/api/tags`); planner `ishti-spark2.5:4b`, fallbacks `ishti-qwen2.5:14b`, coder fallback `ishti-qwen2.5-coder:14b`, translator `gemma3:4b`.
- Config roles (`ishti/config.json` → `llm.models`): planner `ishti-spark2.5:4b`, coder `qwen3-coder:30b-a3b` (NOT pulled → llm.py falls back), fast `laya`, translator `gemma3:4b`.
- Voice config: whisper `large-v3-turbo` local dir, cuda/float16, idle-unload 600 s; Piper TTS; wake `hey_jarvis`.

## Repo shape (measured)

- 727 in-scope `.py` files (excl. venv/data/scratch/backups/md files), **242,255 lines**; 201 without a module docstring (mostly skills' thin wrappers).
- 122 loaded skills; 33 top-level test files; full offline suite = 184 passed / 12 e2e-deselected.
- Tools added this session: `tools/bench_opt.py` (measured benchmarks), `tools/perf_guard.py` (regression gate).

## Blocked / notes

- N22 (real Telegram bot), N23 (WhatsApp enable), N25 (cua-driver install), N26 (cua-bench py3.12) — unchanged, owner decisions, not needed for the optimization loop.
- N24 (Ollama down) LIFTED this session: owner started Ollama, so GPU/voice measurements are possible. If it goes down mid-loop, record GPU metrics as BLOCKED — never guess.
