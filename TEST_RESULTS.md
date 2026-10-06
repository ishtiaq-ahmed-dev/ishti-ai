# Verification results

Date: 2026-10-04. No tests used a live WhatsApp account or changed owner data. The two explicitly requested family facts were added separately to the relationship graph after audit (see `FINAL_REPORT.md`); saved chat/session files were not edited.

## Passed

| Check | Result | Evidence |
|---|---|---|
| Compound relationship parser + scratch SQLite persistence | PASS, 2 cases | `tests/test_memory_context_restore.py -k compound`; exact relation names and explicit-user provenance asserted |
| Exact reported family declaration regression | PASS, 1 case | Scratch graph test for “My another brother is irfan and grandfather is akbar khan” |
| Production chat context assembly | PASS, 1 case | `tests/test_memory_context_restore.py -k chat_skill`; older transcript detail and pinned state reach the mocked model, with bounded context |
| Dispatcher hydration logic | PASS, 2 isolated checks | AST-loaded `_restore_session` with fake history verified role mapping/safety filtering and incognito no-read; full module import remains blocked by pydantic-core mismatch |
| Focused WhatsApp parser/action safety regressions | PASS, 7 cases | `tests/test_whatsapp_safety_regressions.py`; transcript mock, 24-hour time, baseline-bound outgoing text/file verification, unsupported group/mutation response, missing relative behavior |
| Python syntax compilation | PASS | Bundled Python 3.12 `compileall` for changed Python sources and tests |
| Frontend typecheck | PASS | `interface/app`: `tsc --noEmit --pretty false` |
| Focused frontend lint | PASS with one warning | `oxlint src/App.tsx src/sections/Chat.tsx`; pre-existing queue effect warning at `Chat.tsx` |
| Patch whitespace | PASS for touched source/test paths | `git diff --check` |
| Upload signature/archive helper | PASS, 7 isolated checks | AST-loaded validator accepted synthetic PDF/PNG/M4A/OOXML and rejected mismatches and traversal |
| Live-account WhatsApp action checks | BLOCKED | No authenticated dedicated test account/tab/number supplied |

## Blocked / not measured

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
