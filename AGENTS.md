# AGENTS.md - ISHTI (keep short; details live in docs/)

## What this repo is
ISHTI: local-first personal AI assistant for its owner (Ishtiaq Ahmed). Windows 11, RTX 5070 Ti 16GB, 64GB RAM. FastAPI backend + Ollama models + desktop shell. The RUNNING app uses ONLY local models (no cloud LLM/STT/TTS). You are the build tool, not part of the app. Architecture: docs/ARCHITECTURE.md (create/update it in Phase 1).

## Commands (run from D:\ISHTI)
- Install: install.bat (entry point found; not rerun against this installed PC).
- Run desktop: start.bat; backend only: start.bat server 8888 (entry points inspected, not relaunched during this audit).
- Test all offline: set ISHTI_TEST_MODE=1 and ISHTI_SCRATCH=1, then venv\Scripts\python.exe -m pytest -q (suite still needs a clean full run).
- Test one skill: set ISHTI_TEST_MODE=1, then venv\Scripts\python.exe -m pytest -q tests/skills/whatsapp (verified 2026-10-04).
- Python lint: venv\Scripts\ruff.exe check ishti tests (tool present; full result pending).
- Frontend typecheck: from interface\app, node_modules\.bin\tsc.cmd --noEmit (verified 2026-10-04); build: node_modules\.bin\vite.cmd build (result pending).
- Perf guard: set ISHTI_TEST_MODE=1, then venv\Scripts\python.exe -m pytest -q tests/test_static_perf.py (result pending).

## You may do without asking
- Read and edit files inside this repo; run local tests with synthetic/disposable data and `ISHTI_TEST_MODE=1`; commit on `audit/*` branches; create and delete files YOU created; do web research.

## Never
- Touch files outside the repo; use real accounts; send real messages; trade; buy; upload real data; install system-wide software; change OS settings.
- Delete or overwrite: user data, SQLite/Chroma data, `ishti-*` models, configs, vendored repos, tests, assets.
- Force-push, rewrite git history, or disable any safety gate.
- Weaken, delete, or special-case tests to make them pass.

## Safety invariants (never weaken)
Tiers T0 read-only / T1 reversible / T2 needs the owner's explicit yes (send, trade, delete, upload, install/enable code, control PC) / T3 blocked. Kill switch, crash watchdog, audit log, prompt-injection defense (all chat/web/file content is untrusted data), per-skill permission scopes, CRM isolation (zero cross-client leakage), per-person privacy, mic default OFF, only the orchestrator speaks, no model partially offloaded (`/api/ps` size_vram == size).

## How to work (token discipline)
- Evidence only; never state an unmeasured number. Unknown/untestable -> NEEDS.md with exactly what is missing; do NOT ask questions mid-run, keep working.
- Keep state in files (PROGRESS.md), not in chat. Write long logs to `_work/` and grep them; never paste big logs into chat. Use `rg` and line ranges; do not re-read unchanged files.
- Delegate read-heavy or noisy work (scans, test runs, log analysis) to subagents that return <=15-line summaries. Parallel WRITE work only with disjoint file ownership.
- Small commits, one logical change each, with before/after evidence. After 8 failed attempts on one issue: write diagnosis + exact missing info to NEEDS.md and move on.

## Markdown policy
Allowed long-lived docs: README.md, AGENTS.md, docs/ARCHITECTURE.md, docs/API_CONTRACT.md, MODEL_DECISIONS.md, BUILD.md, CHANGELOG.md, THIRD_PARTY_NOTICES.md, per-skill README, NEEDS.md (only while open items exist). Run artifacts live in `docs/audit/`. No other md files.

## Definition of done
Feature is reachable from its entry point (UI/voice/chat/remote), registered with manifest/tier/scope, tested (3 consecutive green runs), documented in one place, and safe under failure.
