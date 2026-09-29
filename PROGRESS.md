# PROGRESS — Master prompt: scan → plan → fix → 24h Chat Digest → verify

Session date: **2026-09-29**. Machine: Windows 11, RTX 5070 Ti 16GB, 64GB RAM.
Working tree at start: clean, `main` @ `4a6dc3e` (rollback point for every phase below).

Legend: [x] done · [~] in progress · [ ] not started · BLOCKED = needs the owner (see NEEDS.md)

## Phase 1 — Discover (read-only)
- [x] Project mapped: 121 skill folders, `ishti/` (209 .py), `interface/` (React UI + 58KB FastAPI server),
      `orchestrator/`, `desktop/`, `browser/`, `voice/`, `tools/`, `tests/` (60 files), `data/` (7.4 GB).
      Evidence: directory listings in this session; `PROJECT_MAP.md` (prior pass) still current.
- [x] Existing docs read: `ISSUES.md` (35 KB, prior audit), `NEEDS.md` (N1–N11), `VERIFICATION_REPORT.md`,
      `MODEL_DECISIONS.md`, `MAC_CHANGES.md`, `docs/PROGRESS.md`.
- [x] Secret scan of the new surface: no credentials in the files touched by this pass (only `data/api_token`,
      already git-ignored; not read into any report).
- [x] Gap confirmed by search, not assumption: **`chat_digest` did not exist** (`search_codebase` → no results),
      and the chat UI has **no GFM table support** (`interface/app/src/components/Markdown.tsx`).
- [x] Export toolchain probed: `python-docx 1.2.0` ✅, `playwright 1.63.0` ✅, `pypdfium2` ✅ (page → PNG),
      Edge + Chrome binaries present ✅, `reportlab` **missing** (fallback only, not required).

## Phase 2 — Plan
- [x] `PLAN.md` written: work packages, ownership, files, proof test, risk, rollback.

## Phase 3 — Execute
- [ ] WP1 `chat_digest` core (store/window/analyze/report)
- [ ] WP2 exports (md/docx/pdf)
- [ ] WP3 chat UI tables (GFM-grade rendering)
- [ ] WP4 tests N1–N12 on the synthetic dataset
- [ ] WP5 re-scan for new problems + docs update
- [ ] WP6 independent verification sub-agent
- [ ] WP7 commit + tag

## Notes for resuming after context loss
1. Everything new lives under `ishti/skills/chat_digest/`, `tests/test_chat_digest.py`, `reports/`.
2. Run: `venv\Scripts\python -m pytest tests/test_chat_digest.py -q` (offline, no Ollama needed).
3. The skill never sends/replies/reacts/deletes anything — T0, read-only; sending stays T2.
