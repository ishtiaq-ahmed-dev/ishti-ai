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

| Backend suite incl. WP2/WP3/WP7 | `python -m pytest tests/backend -q` | PASS 34/34, 3 consecutive green runs (exit 0 x3, 2026-10-06) |
| Decision loop self-check | `python -m ishti.decision_loop` | PASS |
| Agent bus self-check | `python -m ishti.agent_bus` | PASS |
