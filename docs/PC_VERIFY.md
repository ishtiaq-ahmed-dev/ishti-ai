# PC verification — mac/audit-20261005 (verified on the target PC, 2026-10-06)

Machine: Windows 11, RTX 5070 Ti 16 GB, Python 3.11 venv, Ollama (32 models, `ishti-spark2.5-q8:4b` present).
Branch under test: `feat/repo-integration` merged with `origin/main` (mac work via PR #3). Merge commit `06a6439`.
All pytest runs with `ISHTI_TEST_MODE=1 ISHTI_SCRATCH=1`; live backend on 127.0.0.1:8888 with the same scratch env.

## Verification matrix (per docs/MAC_CHANGES.md)

| ID | Change | Result | Evidence (PC) | Action |
|---|---|---|---|---|
| D01 | Audit map/research/plan docs | PASS | docs/AUDIT.md, RESEARCH.md, PLAN.md, PROJECT_MAP.md present and read | none |
| M01 | Install requires explicit approval | PASS | Unit: test_skill_install_approval.py green ×3. Live: "install the yt-dlp python package" → no pip run, asks for exact app, stays owner-gated | none |
| M02 | Vault isolation, fail-closed, delete retention | PASS | test_media_vault.py green ×3; 2 PC fixes applied (see below); scratch vault untouched owner data | improved logging |
| M03 | Realpath scope, junction guards | PASS | Unit: test_scope_boundaries_mac.py green ×3. Live junction probe: junction inside allowed scope → target outside → ScopeError; in-scope write OK (probe outside %TEMP% because temp is ALWAYS_WRITABLE by design) | none |
| M04 | Governed skill execution | PASS | test_executor_governance.py green ×3; live /api/execute routes through governed dispatcher (125/125 skills) | none |
| M05 | GPU admission fails closed, re-admits | PASS | test_llm_admission_safety.py green ×3. Live: real model run ("17×23" → 391); /api/vram size 4488 MB == size_vram 4488 MB, fully_on_gpu=true before/during/after — no CPU spill | none |
| M06 | Exact confirmation for memory/relationship delete | PASS | test_memory_delete_gate.py green ×3 after merge-resolution fix. Live: "forget Ali" with no matching record → honest no-op, no mutation | none |
| M07 | One-use exact-target vault purge approval | PASS | test_vault_api_gate.py green ×3. Live API: prepare → purge OK (`purged:0` scratch) → second purge with same token rejected ("approval required or expired") | none |
| M08 | Adapter/base-model prerequisites fail closed | PARTIAL | test_finetune_safety.py green ×3 (synthetic). No local training base configured on this PC — staged eval not exercised on real GPU | owner: set ISHTI_TRAINING_BASE_MODEL to verify live (NEEDS.md) |
| M09 | Session-bound temporary lessons | PASS | test_learning_session_scope.py + test_learning_wiring.py green ×3 | none |
| M10 | Planner JSON envelope validation | PASS | test_planner_schema_safety.py green ×3; live planner runs throughout smoke | none |
| M11 | Offline serialized router init | PARTIAL | test_router_cache_safety.py green ×3 (synthetic offline). Live router worked with network up; offline refusal not exercised live (would require cutting the PC's network) | owner-decided offline drill (NEEDS.md) |
| M12 | Unicode correction normalization | PASS | test_corrections_unicode.py green ×3 | none |
| M13 | Client CRM grounding fenced | PASS | test_client_prompt_injection.py green ×3 | none |
| M14 | MCP queued cancel + bounded WS sends | PARTIAL | test_mcp_cancellation.py + test_websocket_send_timeout.py green ×3 (synthetic slow socket). Live: /ws/events connects and streams (boot_stage, vram_status). Slow-client disconnect not reproducible from the agent side | owner: leave a stalled tab open during a session (NEEDS.md) |
| M14b | ARCH2 retrieval hook compatibility | PASS | test_executor_governance.py green ×3 | none |
| F01 | Versioned correction replay report | PASS | test_correction_eval.py green ×3 after PC fix; tool CLI loads (`tools/correction_eval.py` present) | PC fix: encoding |

## PC fixes made during verification (all improvements, nothing deleted)

1. `ishti/skills/relationships/skill.py` — merge of mac's M06 confirm-gate with local honesty checks: `get_node` lookup now tolerant of graph test doubles (`hasattr` guard); both mac's ask-first gate and the local unknown-target refusal retained.
2. `tests/test_correction_eval.py` — read report with `encoding="utf-8"` (Windows cp1252 default broke UTF-8 reports; the tool already wrote UTF-8).
3. `ishti/security/media_vault.py` — two silent `except: pass` (mac M02) now log via `ishti.core.logutil.swallowed` (repo hygiene sentinel test; behavior unchanged).

## Suite results (exit codes captured)

- Mac matrix (16 test files): pass 1 → 2 failures (above), fixed; passes 2, 3 → exit 0 each. Ruff `ishti tests tools` → clean.
- Full suite: run 1 → exit 1 (1 failure: repo-hygiene sentinel, fixed); run 2 → **exit 0** (no failures).
- Live backend: /api/health ok (ollama 32 models, gpu 10338 MB free, skills 125/125, browser connected); /api/ready all stages done; /ws/events streams. `search` check degraded: local SearxNG (127.0.0.1:8890) not running — optional service, not a regression.

## Live behavior proofs

- Start/Stop: `python -m ishti 8899` up → POST /api/shutdown → process gone, port free (only client TIME_WAIT remains).
- Kill switch: POST /api/kill → `{"stopped":["global mic OFF"]}`, process exits; crash watchdog restarts the service with scratch isolation intact (session store identical before/after).
- T0 skill smoke: time → reflex ("11:20 AM"); folders → real Downloads listing (255 files, 1.7 GB); weather → live open-meteo (28°C/20°C, thunderstorms); memory save/retrieve consistent.
- VRAM invariant: no model partially offloaded (`size_vram_mb == size_mb`, fully_on_gpu=true).

## Not testable here (owner-dependent)

Real WhatsApp/Telegram accounts, Google OAuth, Chrome Profile 9, tunnel, live audio voice loop (STT→TTS with a human speaker; the voice stack initializes — /api/ready `voice_ready` done), GPU fine-tune staged eval (no training base set), true-offline router drill, slow-WS client observation, and the contract-v2 chat UI end-to-end (the v2 backend lives on unmerged branch `agent/backend`; this tree serves the v2 UI build at `/` but chat needs that merge — owner call, tracked in NEEDS.md).

## Blockers

None Critical/High. `docs/PC_BLOCKERS.md` not needed this round.
