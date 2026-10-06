# IMPLEMENTATION_PLAN.md — Agent A backend (Contract v2 + WP1-WP8)

Order: contract first (stubs + tests, commit) so Agent B can build against it.

| WP | Deliverable | Files | Status |
|---|---|---|---|
| C2 | Contract v2: §14 in API_CONTRACT, CONTRACT_CHANGES, stubs + contract tests | docs/API_CONTRACT.md, docs/CONTRACT_CHANGES.md, ishti/contract_api.py, ishti/runs.py, tests/backend/ | IN PROGRESS |
| WP1 | Durable run engine: persist msg before gen, run server-side, seq event log, since_seq replay, idempotency, cancel, restart recovery | ishti/runs.py, ishti/contract_api.py | IN PROGRESS |
| WP2 | Deep research, never-early-answer, escalation ladder, exhaustion honesty | orchestrator/deep_research.py (extend) | PENDING |
| WP3 | Plan-act-verify critic loop, calibrated confidence, A/B | ishti/core/pipeline.py + orchestrator | PENDING |
| WP4 | Personal/Business profiles, memory namespaces, per-chat binding | ishti/profiles.py | PENDING |
| WP5 | Layered prompt compiler, byte-stable prefix, GET /api/prompt/layers | ishti/prompt_compiler.py | PENDING |
| WP6 | Feedback audit+fix, RL harness, auto skill creation with T0 gate | ishti/learning/, skill lifecycle | PENDING |
| WP7 | Multi-agent bus + blackboard + A2A (127.0.0.1, token auth, loop prevention) | ishti/agents.py, ishti/a2a.py | PENDING |
| WP8 | Connector registry (WhatsApp/Telegram QR+pairing, control numbers, step-up, self-chat) | ishti/connectors.py, ishti/channels/ | PENDING |

Tests: tests/backend/test_runs_engine.py (replay/dup/restart), tests/backend/test_contract_v2.py. Log in TEST_RESULTS.md. 3 consecutive green runs before Done.
