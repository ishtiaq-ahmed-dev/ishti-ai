# Frontend → Backend contract requests

| Date | Request | Status |
|---|---|---|
| 2026-10-06 | `GET /api/runs?chat_id=` list shape documented in §14.2 — confirm filter returns newest-first | answered in API_CONTRACT §14 |
| 2026-10-06 | `run_delta` should carry `{run_id, text}` chunk (not cumulative) — mock assumes chunk | implemented in §14.3 |
| 2026-10-06 | Chat list (`GET /api/chats`) returns `title,profile,created_at,updated_at` | implemented in §14.1 |
| 2026-10-06 | Legacy `/api/sessions*` endpoints stay during migration; UI migrates to v2 progressively | agreed |

## Requests from live integration (2026-10-06)

| Ask | Why | Suggested shape |
|---|---|---|
| `PATCH /api/chats/{id}` | Sidebar rename is local-only (localStorage override) until this exists | body `{title}`; publish `chat_updated` |
| `DELETE /api/chats/{id}` (or `/api/chats/delete` with undo token) | Sidebar delete currently hides locally; nothing server-side | T1 reversible with 8s undo like legacy sessions |
| v2 chat export | Share is clipboard-only; legacy export endpoint is session-bound | `POST /api/chats/export {ids, format}` → path |
| Per-run trace events | "Skill per question" graph needs route/skill/confidence data not in §14 | include routing events in `run_step` payloads or `GET /api/runs/{id}/trace` |
| FIXED backend-side (needs merge): second assistant message of a chat silently dropped `message_created`+`run_finished` | `UNIQUE(chat_id, client_msg_id)` + `client_msg_id=''`; fixed to NULL in worktree `agent/backend` `ishti/runs.py` + regression test `test_second_assistant_message_finishes` | merge `agent/backend` |
