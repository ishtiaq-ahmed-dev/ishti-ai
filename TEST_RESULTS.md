# TEST_RESULTS.md — Agent A backend (Contract v2 + WP1), 2026-10-06

| Check | Command (ISHTI_TEST_MODE=1) | Result |
|---|---|---|
| Run engine self-check | `python -m ishti.runs` | PASS (durable chats/messages/runs, gapless seq log, replay, idempotency, cancel, recovery, feedback, learning mode, proposals) |
| Prompt compiler self-check | `python -m ishti.prompt_compiler` | PASS (byte-stable core prefix, profile layers, bounded skills) |
| Contract v2 API self-check | `python -m ishti.contract_api` | PASS (all §14 endpoints reachable over fresh ASGI) |
| Backend suite | `python -m pytest tests/backend -q` | PASS 21/21, 3 consecutive green runs (exit 0 x3) |
| Repo hygiene (silent-except ratchet) | `pytest tests/test_repo_hygiene.py` | PASS |
| Regression sample (channels, api_sessions, notifications, dispatcher-blank) | `pytest tests/test_channels.py tests/test_api_sessions.py tests/test_notifications.py tests/test_dispatcher_blank_answer.py` | PASS 33/33 |
| Ruff on changed files | `ruff check ishti/runs.py ishti/contract_api.py ishti/run_executor.py ishti/profiles.py ishti/prompt_compiler.py ishti/lifecycle_api.py ishti/events.py tests/backend` | PASS (0 findings) |
| Real dispatch smoke | `python -m ishti.run_executor` (self-check; requires Ollama) | PASS (state=done) when GPU free; skipped in offline suite (stubbed) |

Not run (needs live services / owner): real WhatsApp/Telegram smoke, GPU A/B evals (WP2-WP6), full default suite. Listed in NEEDS.md.

- Two history-hydration pytest cases and the FastAPI upload endpoint tests cannot import project dependencies: the checked-in virtual environment targets a missing Python 3.11 installation, and the available Python 3.12 cannot load its CPython 3.11 `pydantic_core` extension. This is a runtime mismatch, not a reported product-test pass.
- `tests/test_whatsapp_context.py` contains a live browser call at module import. It was not run to avoid touching a real account. Added pure safety cases are in a separate offline test module.
- No OCR/STT/media accuracy, latency, VRAM, selector drift, delivery verification, CRM isolation, lifecycle, remote channel parity, or full-suite results are claimed.
- No Vite production build was run because it rewrites generated `dist` files that already had user changes in this worktree. TypeScript typechecking passed without touching `dist`.

## CONTRACT v2 frontend + chat integration (2026-10-06)

| Check | Result | Evidence |
|---|---|---|
| Backend contract suite `tests/backend` (worktree, agent/backend) | PASS ×3 (35 tests incl. new regression) | `ISHTI_TEST_MODE=1 D:/ISHTI/venv/Scripts/python.exe -m pytest tests/backend -q` exit 0, three consecutive runs |
| Backend ruff on changed files | PASS | `ruff check ishti/runs.py tests/backend/test_runs_engine.py` — "All checks passed!" |
| `ishti.runs` self-check | PASS | "[self-check] runs OK - durable chats/messages/runs, seq log, replay, idempotency, cancel, feedback, learning mode, proposals, agent bus hooks" |
| Mock contract checks `frontend/mock/test_mock.py` | PASS ×3 | exit 0 three consecutive runs (idempotent send, run lifecycle, replay, cancel, connectors, learning) |
| Frontend typecheck + build | PASS ×3 | `tsc.cmd --noEmit` exit 0; `vite build` ✓ (~230-260 ms per build) |
| Live: send → RunStatusCard → final answer (real backend :8888, same-origin, real model) | PASS | user bubble + "Working…" card (0:52 elapsed, Stop button, step "thinking with ishti-spark2.5-q8:4b") + streamed draft + final `message_created`+`run_finished`; runs table shows exactly 1 run per send (double-send eliminated) |
| Live: multi-turn in one chat | PASS | 3 consecutive sends all published message_created+run_finished after the `client_msg_id` NULL fix; chat holds 8+ server rows |
| Live: section switch → return | PASS | conversation + unread state preserved via app-level store; verified for Overview→Chat and reload |
| Live: reload persistence + lost-answer recovery | PASS | full history restored from replay; pre-fix runs' answers recovered from `runs.text` (synth rows keyed `run-{id}`, deduped against server rows) |
| Live: feedback | PASS | thumbs-up → POST /api/feedback → `/api/feedback/stats` `{"total":1,"pos":1}`; idempotent retry per contract |
| Live: profile switch | PASS | Business→Personal round-trip persisted via `PUT /api/profile` (POST previously returned 405) |
| Live: sidebar search / rename / delete+undo / select+share / unread badges / activity graph / draft restore | PASS | in-browser probes: filter empty-state, title override persisted, hide+undo restores, clipboard share, per-chat unread count clears on open, run-step graph renders 8 nodes, composer draft restored after reload |
| Same-origin enforcement | PASS (by design) | cross-origin POST /api/chats from vite preview :4174 → 403 "foreign origin"; UI served from the backend origin (production path) |
| NOT RUN: real WhatsApp/Telegram smoke, GPU A/B evals, full 841-test suite | — | need owner accounts / devices / live services (NEEDS.md) |

## Profile separation + hydrate fix (2026-10-06, round 2)

| Check | Result | Evidence |
|---|---|---|
| Per-chat profile pinning | PASS | `chatStore.newChat()` posts the current profile; backend keeps the chat's profile forever (`runs.py new_chat` + `start_run` uses `chat_profile(chat_id)`) |
| Namespace isolation (no merge/conflict) | PASS | live: business chat runs kept `profile=business` after the global switch back to personal (`/api/runs` shows per-run profile); backend test `test_profile_isolation_and_prompt_layers` asserts distinct memory namespaces and byte-stable per-profile prompts |
| Sidebar shows namespaces apart | PASS | PERSONAL / BUSINESS group headers with Heart/Briefcase icons + business badge on the row; verified in-browser |
| "This chat" graph tab restored | PASS | ConversationGraph renders per-chat turns beside the per-answer run-step graph |
| Hydration data loss (regression found by switch-cycle test) | FIXED | old hydrate rebuilt the list from pending/synth rows only, deleting already-known server rows (12→2, 4→0 observed); new logic: server rows authoritative + local-only rows kept + acked optimistic rows dropped |
| Replay determinism | FIXED | `chatStore.start()` resets `lastSeq`/`reconciled` — every fresh context replays the full log idempotently (observed stale-seq skip live) |
| Switch cycle stability | PASS | business(4 rows)→personal(12)→business(4) with UI answers correct at every step |
| Mock contract / backend suite / tsc+build | PASS | exit 0 after the fix (mock ×4, backend ×4 overall this session) |
| Backend suite incl. WP2/WP3/WP7 | `python -m pytest tests/backend -q` | PASS 34/34, 3 consecutive green runs (exit 0 x3, 2026-10-06) |
| Decision loop self-check | `python -m ishti.decision_loop` | PASS |
| Agent bus self-check | `python -m ishti.agent_bus` | PASS |
| Profile separation + hydrate merge fix (this checkout, post-merge re-verification below) | — | see section above |

