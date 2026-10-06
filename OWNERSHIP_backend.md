# OWNERSHIP_backend.md — Agent A (backend)

Branch: `agent/backend` (worktree `D:\ishti-backend`). Merge order: backend first to main; frontend rebases after.

## Owned paths (Agent A)
- `ishti/**` — server, skills, channels/connectors, learning, agents, memory glue, runs engine, contract API (`ishti/contract_api.py`, `ishti/runs.py`, `ishti/profiles.py`, `ishti/prompt_compiler.py`)
- `orchestrator/**`, `voice/**`, `remote/**`, `tools/**` (non-QA), `tests/**` (incl. `tests/backend/`)
- `interface/web_ui/server.py` — ONLY the attach/include lines for backend routers (it is the backend server, not UI)
- `requirements.txt`, `pytest.ini`, `docs/API_CONTRACT.md`, `docs/CONTRACT_CHANGES.md`, `docs/API_CONTRACT*.md`
- `PROGRESS.md`, `TEST_RESULTS.md`, `NEEDS.md`, `IMPLEMENTATION_PLAN.md` (backend sections)

## NOT owned (Agent B owns; never edit)
- `interface/app/**` (React UI), `interface/web_ui/static/**`, `desktop/**` (shell), frontend `package.json`

## Frontend requests
Frontend needs -> `docs/contract_requests/frontend.md` -> answered as contract changes in `docs/API_CONTRACT.md` (version bump + line in `docs/CONTRACT_CHANGES.md`).
