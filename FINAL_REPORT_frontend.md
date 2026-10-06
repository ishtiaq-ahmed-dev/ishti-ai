# Frontend final report — CONTRACT v2 dashboard (2026-10-06)

Verdict: **READY (with known issues — none Critical/High open)**

## Deliverables

| Area | Status | Notes |
|---|---|---|
| Chat = CONTRACT v2 store-driven (§14) | PASS | single idempotent sender, one shared `/api/events` stream with gap-resync + startup reconcile |
| Working display (owner request: keep BOTH old UI and working view) | PASS | RunStatusCard (steps/elapsed/Stop) + streamed-draft banner + unverified banner, while ALL prior chat features were restored |
| Sidebar: search, select, rename, delete+undo, share, unread, resize | PASS | rename/delete are local until backend adds PATCH/DELETE (NEEDS.md); undo restores, nothing destroyed |
| Per-message v2 feedback (idempotent) | PASS | verified against real backend `/api/feedback` + `/api/feedback/stats` |
| Profile switch (WP4) | PASS | PUT /api/profile round-trip verified; per-chat profile support in store |
| Connectors page (WA/TG, risk-ack, E.164, masked tokens) | PASS | mock-tested; real-account smoke pending (owner) |
| Agents panel (INPUT_REQUIRED), Learning panel (proposals/mode) | PASS | mock-tested against §14 |
| Legacy double-send bug (v1 stream + v2 POST) | FIXED | Chat sends only via chatStore; runs table shows exactly 1 run per send |
| Backend second-answer bug (UNIQUE chat_id+client_msg_id) | FIXED | worktree `agent/backend`: assistant rows use NULL; regression test added; 35/35 ×3 green |
| Profile-switch 405 (POST vs PUT) | FIXED | `putJSON` used; round-trip verified |
| Deep-link ping-pong (stale hash reverting clicks) | FIXED | hash applied only on mount/ready/hashchange |
| Missed-publish resilience | ADDED | store recovers answers from `runs.text` (honest data), deduped by message_id/run_id |

## Measured

- `vite build`: ~230–260 ms per build (×3); `tsc --noEmit` exit 0 (×3).
- Backend `tests/backend`: 35 tests, exit 0 ×3; ruff clean on changed files.
- Mock contract checks (`frontend/mock/test_mock.py`): exit 0 ×3.
- Live runs on :8888 (real model `ishti-spark2.5-q8:4b`, local GPU): simple arithmetic run ≈ 50–90 s end-to-end (decision-loop passes), event log gapless (last_seq 97 across 12 messages/6 runs during verification).

## Screenshots / evidence index

- Live probes in this thread's browser tab (transcript): run card with elapsed + Stop, streamed draft, answers 391/399/12/22/121/27, unread badge, rename ("Math tests"), delete+undo, search empty-state, activity graph (8 nodes), feedback "Recorded", profile round-trip.
- Terminal evidence: `/tmp/bt.log` (backend suite), mock + build exit codes in transcript.

## Known issues / deferred

1. Chat rename/delete are local-only until `PATCH/DELETE /api/chats/{id}` exist (contract request filed).
2. Share = clipboard text; real .md/.pdf export needs a v2 export endpoint or desktop file writer.
3. Old "skill per question" graph needs per-message trace events in v2 (contract request filed).
4. Real WhatsApp/Telegram smoke + GPU A/B evals need owner accounts/devices (NEEDS.md).
5. `agent/backend` (with the second-answer fix) is not yet merged to the integration branch — owner call.
