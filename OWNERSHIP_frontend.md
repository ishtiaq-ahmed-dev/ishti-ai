# OWNERSHIP_frontend.md — Agent B (frontend)

Working directory: main checkout `D:\ISHTI` on `feat/repo-integration` (no worktree per owner).

## Owned (Agent B)
- `interface/app/**` (React UI), `interface/web_ui/static/**` (built assets), `desktop/**` shell configs
- `frontend/mock/**` (local CONTRACT v2 mock server), UI tests under `tests/frontend/`
- `OWNERSHIP_frontend.md`, `IMPLEMENTATION_PLAN.md`, `TEST_RESULTS.md`, `NEEDS.md`, `FINAL_REPORT_frontend.md` (frontend sections)

## NOT owned (Agent A owns; never edit)
- `ishti/**`, `orchestrator/**`, `voice/**`, `remote/**`, `tools/**` (backend), python requirements, `docs/API_CONTRACT*.md`
- `interface/web_ui/server.py` (backend server) — needs go via `docs/contract_requests/frontend.md`

## Backend contract requests
Filed in `docs/contract_requests/frontend.md`; built against `frontend/mock/` meanwhile.
