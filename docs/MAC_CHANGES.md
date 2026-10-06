# Mac audit handoff

Branch: `mac/audit-20261005`  
Base: `e9d2559`  
API contract: `2.0.0` (unchanged)

| ID | Change | Verified on Mac? | Needs PC test? |
|---|---|---:|---:|
| D01 | Audit map/research/plan (`9064443`) | Yes | No |
| M01 | Dependency discovery requires explicit approved install (`4a66645`) | Yes | No |
| M02 | Vault scratch isolation, corruption fail-closed, delete retention (`06d8d6e`) | Yes | Yes: Windows paths/ACLs |
| M03 | Realpath filesystem scope and keyword/dir-fd guards (`952ae5b`) | Yes | Yes: junctions |
| M04 | Shared governed skill execution for agents and ARCH2 (`30429ca`) | Yes | Yes: PC skill/UI flow |
| M05 | GPU admission fails closed and recovery re-admits (`3b548e1`) | Synthetic only | Yes: Ollama + RTX residency |
| M06 | Exact confirmation for memory/relationship deletion (`5c973a4`) | Yes | No |
| M07 | One-use exact-target vault purge approval and UI (`922213b`) | Tests/build | Yes: browser/API smoke |
| M08 | Adapter evaluation/base model prerequisites fail closed (`7f0cecf`) | Yes | Yes: local training stack |
| M09 | Session-bound temporary lessons and exact attribution (`cb6f8b6`) | Yes | No |
| M10 | Planner JSON envelope/step validation (`8696fe4`) | Yes | No |
| M11 | Offline serialized router initialization (`169f181`) | Synthetic cache tests | Yes: cached Laya/CUDA |
| M12 | Unicode correction normalization and deterministic ties (`32fa55d`) | Yes | No |
| M13 | Client CRM grounding fenced as untrusted data (`0e43075`) | Yes | No |
| M14 | MCP queued cancellation and bounded WebSocket sends (`641e196`, `0e43075`) | Synthetic only | Yes: live transport |
| F01 | Isolated versioned correction replay report (`a0e87d8`) | Yes | No |

## Verification matrix

Run from `D:\ISHTI` in PowerShell:

```powershell
$env:ISHTI_TEST_MODE = "1"
$env:ISHTI_SCRATCH = "1"
& .\venv\Scripts\ruff.exe check ishti tests
& .\venv\Scripts\python.exe -m pytest -p pytest_timeout --timeout=45 tests\test_skill_install_approval.py tests\test_scope_boundaries_mac.py tests\test_llm_admission_safety.py tests\test_media_vault.py tests\test_corrections_unicode.py tests\test_planner_schema_safety.py tests\test_executor_governance.py tests\test_router_cache_safety.py tests\test_mcp_cancellation.py tests\test_correction_eval.py tests\test_vault_api_gate.py tests\test_finetune_safety.py tests\test_memory_delete_gate.py tests\test_learning_session_scope.py tests\test_client_prompt_injection.py tests\test_websocket_send_timeout.py
```

Expected: Ruff clean and all listed tests pass. Repeat the test command three times for safety changes. Roll back any batch with `git revert <commit>`; do not reset or merge the branch.

| ID | Files touched | Why / exact PC verification | Expected result | Risk / rollback |
|---|---|---|---|---|
| D01 | `docs/PROJECT_MAP.md`, `docs/AUDIT.md`, `docs/RESEARCH.md`, `docs/PLAN.md` | Documentation only; inspect with `Get-Content docs\*.md` | Evidence, ranked findings, cited research and ordered plan are present | No runtime risk; `git revert 9064443` |
| M01 | `ishti/skills/__init__.py`, `tests/test_skill_install_approval.py` | `& .\venv\Scripts\python.exe -m pytest tests\test_skill_install_approval.py -q` | Discovery reports missing dependency and never runs pip without owner approval | Optional dependency remains unavailable until explicit install; `git revert 4a66645` |
| M02 | `ishti/security/media_vault.py`, `tests/test_media_vault.py` | `& .\venv\Scripts\python.exe -m pytest tests\test_media_vault.py -q`; inspect `$env:ISHTI_SCRATCH` index/key | Corrupt vault refuses writes; failed unlink remains indexed; tests do not touch owner vault | Existing owner index/key must be backed up before PC run; `git revert 06d8d6e` |
| M03 | `ishti/scope.py`, `tests/test_scope_boundaries_mac.py` | `& .\venv\Scripts\python.exe -m pytest tests\test_scope_boundaries_mac.py -q`; add a Windows junction inside allowed root and exercise a fake skill | Symlink/junction target outside scope, keyword path and dir-fd mutation are denied; normal in-scope write works | Python hooks are not an OS sandbox; `git revert 952ae5b` |
| M04 | `ishti/skill_execution.py`, `ishti/agents.py`, `ishti/arch2/pipeline.py`, `tests/test_executor_governance.py` | `& .\venv\Scripts\python.exe -m pytest tests\test_executor_governance.py -q`; run an ARCH2 T2 request in the UI and answer `no`, then `yes` | Disabled/T3/test-mode calls refuse; allowed calls have audit/scope/kill context; T2 asks once and executes only after exact approval | Skill behavior changes are centrally governed; `git revert 30429ca` |
| M05 | `ishti/llm.py`, `tests/test_llm_admission_safety.py` | `& .\venv\Scripts\python.exe -m pytest tests\test_llm_admission_safety.py -q`; with Ollama running, issue concurrent model requests and inspect VRAM | Admission failure produces a refusal, no generation; recovery starts at most once and rechecks residency; no CPU spill | Requires RTX/Ollama; `git revert 3b548e1` |
| M06 | `ishti/skills/memory/skill.py`, `ishti/skills/relationships/skill.py`, `ishti/arch2/consolidate.py`, `ishti/dispatcher.py`, `tests/test_memory_delete_gate.py` | `& .\venv\Scripts\python.exe -m pytest tests\test_memory_delete_gate.py -q`; issue an exact delete, verify no mutation before `yes`, then verify one mutation | Reads remain available; destructive fact/person/relationship deletes require exact confirmation and cannot replay | Pending confirmation state is session-bound; `git revert 5c973a4` |
| M07 | `interface/web_ui/server.py`, `interface/app/src/components/PrivacyPanel.tsx`, `tests/test_vault_api_gate.py` | `& .\venv\Scripts\python.exe -m pytest tests\test_vault_api_gate.py -q`; `cd interface\app; npm ci; npm run build`; call prepare then purge twice | Missing target is rejected; exact target token expires and is single-use; UI displays `purged` and build succeeds | Process-memory token expires on restart; `git revert 922213b` |
| M08 | `orchestrator/finetune.py`, `tests/test_finetune_safety.py` | `& .\venv\Scripts\python.exe -m pytest tests\test_finetune_safety.py -q`; set a real local `ISHTI_TRAINING_BASE_MODEL`, run staged eval and inspect adapter promotion | Unknown base/eval-unavailable adapter refuses; current adapter remains unchanged; valid local training uses explicit base | Unsloth/TRL/GPU are PC-only; `git revert 7f0cecf` |
| M09 | `ishti/learning/policy.py`, `ishti/learning/lessons.py`, `ishti/learning/feedback.py`, `ishti/skills/chat/skill.py`, `ishti/core/assemble.py`, `ishti/arch2/{retrieval,pipeline}.py`, `ishti/dispatcher.py`, `tests/test_learning_session_scope.py` | `& .\venv\Scripts\python.exe -m pytest tests\test_learning_session_scope.py tests\test_learning_wiring.py -q` | Temporary lesson from session A is invisible to B/unbound callers; ratings credit only persisted applied lesson IDs once | Legacy empty decision callers use compatibility path; `git revert cb6f8b6` |
| M10 | `ishti/planner.py`, `tests/test_planner_schema_safety.py` | `& .\venv\Scripts\python.exe -m pytest tests\test_planner_schema_safety.py -q` | Malformed/non-object/oversized plans produce safe fallback; valid bounded plans still execute | Strict parser may refuse previously tolerated malformed output; `git revert 8696fe4` |
| M11 | `ishti/router.py`, `tests/test_router_cache_safety.py` | `& .\venv\Scripts\python.exe -m pytest tests\test_router_cache_safety.py -q`; disconnect the PC network and construct the configured router concurrently | Cache miss refuses without download/env mutation; concurrent constructors share one initialized local cache | Real HuggingFace cache/CUDA remains unverified; `git revert 169f181` |
| M12 | `ishti/corrections.py`, `tests/test_corrections_unicode.py` | `& .\venv\Scripts\python.exe -m pytest tests\test_corrections_unicode.py -q`; add English, Urdu and Pashto corrections and inspect export | Unicode-normalized corrections persist and deterministic ties abstain; punctuation-only input is ignored | Existing legacy rows are normalized on read; `git revert 32fa55d` |
| M13 | `ishti/skills/clients/skill.py`, `tests/test_client_prompt_injection.py` | `& .\venv\Scripts\python.exe -m pytest tests\test_client_prompt_injection.py -q`; create a hostile client event and capture the draft model prompt | Client grounding is fenced and injection-like lines are removed; only the selected client context is supplied | Model still receives owner request separately; `git revert 0e43075` |
| M14 | `ishti/mcp_client.py`, `interface/web_ui/server.py`, `tests/test_mcp_cancellation.py`, `tests/test_websocket_send_timeout.py` | `& .\venv\Scripts\python.exe -m pytest tests\test_mcp_cancellation.py tests\test_websocket_send_timeout.py -q`; stream a live UI session and unplug/slow the browser connection | Queued MCP timeout cancels before start; started calls report unknown outcome without retry; slow WS send times out and disconnects cleanly | Active remote MCP cancellation cannot be guaranteed; `git revert 641e196` and/or `git revert 0e43075` |
| F01 | `tools/correction_eval.py`, `tests/test_correction_eval.py` | `& .\venv\Scripts\python.exe -m pytest tests\test_correction_eval.py -q`; run `& .\venv\Scripts\python.exe tools\correction_eval.py --help` and a fixture bundle | Versioned baseline/candidate report includes hashes, holdout and safety regressions; promotion remains unauthorized | Report never writes production routing or weights; `git revert a0e87d8` |

## Not verified on this Mac

RTX residency/Ollama, Windows junction semantics, CUDA/Unsloth/TRL training, Piper/faster-whisper, Chrome Profile 9, WhatsApp/Telegram accounts, OAuth, tunnel behavior, and live browser/WebSocket transport require the target Windows PC. No claim of those tests is made here.
