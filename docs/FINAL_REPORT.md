# FINAL REPORT — Prompt 4 (final verification, testing, validation & improvement)

Date: 2026-09-29. Scope: re-verification of Prompts 1–3 + all pre-existing work with fresh
evidence; fix everything found; document.

## 1. Master requirements summary

| Matrix | VERIFIED | PARTIAL | BLOCKED |
|---|---|---|---|
| Prompt 1 (foundation, switchable arch, context fix) | all rows | — | — |
| Prompt 2 (memory, graph, learning) | all rows except A1/G4 | A1 selectability, G4 train step | — |
| Prompt 3 (skills, multi-agent, integrations, resilience, telemetry) | all rows except I1/I2/I3 | I1 workflow loops/branches | — |
| Prompt 4 (this pass) | 13 rows | 2 (B4 soak, B2 live-contract timing) | 2 (B1b coverage tooling, D2 clean-install) |

Legacy long-tails (honest, unchanged): arch1 per-skill integration-test tail; settings
dashboard UI tab for memory/telemetry; workflow engine loops/parallel branches.

## 2. Blocked items — exact reason + question

1. **pytest-cov (and peft/datasets/bitsandbytes)** — not installed; shared venv was mid
   PyInstaller build, so mutating it unannounced was unsafe. → *OK to batch-install?*
2. **Clean-install rehearsal** — duplicating the multi-GB venv while the box is busy risks
   confusion. → *Schedule when idle?*
3. **qwen3:14b** — not pulled; config falls back to ishti-qwen2.5:14b. → *Pull it?*
4. **Flowise / ScrapeGraphAI** — adapters degrade gracefully (breaker/regex fallback).
   → *Install and bind live?*
5. **Apodex** — no official OSS repo found (commercial product at apodex.com). → *Which
   exact repository, or confirm closed product?*
6. **Pi-Desktop** — GUI app (vastsa/pi-desktop), not an embeddable library. → *Document
   alongside, drive from inside (needs upstream API), or port its permission-prompt design?*

## 3. Test results (fresh, this pass)

- **Offline pytest suite: all passed, exit 0 — twice.** (Was: `INTERNALERROR` at collection,
  0 tests runnable, before this pass.)
- **`ishti.check` live core loop: OK** — routing **329/329 @ 0.2 ms median**; memory
  round-trip; live weather 1.2 s; local LLM 1.4 s (17×23 = 391); compound planner 3.3 s.
- **Live-stack e2e (no mocks): 25/25 assertions** (A–L; F/I documented as owner-presence
  tests; one transient browser timeout under concurrent build CPU saturation, passed on
  retry at 2.4 s).
- **Module self-checks: 9/9 OK** (skills.registry 119 records, resilience, taskmgr, agents,
  flowise_client, leads, telemetry incl. **eval 5/5**, factcheck, core.architectures).
- **STT on real audio: 377.9 ms** (CUDA float16 active), English phrase 504 ms.

## 4. Performance (measured this pass)

Routing 0.2 ms · STT real-audio 0.38 s · live skill 1.2 s · LLM answer 1.4 s ·
compound planner 3.3 s. No stage regressed vs the Prompt-1 baseline; the fix in
`consolidate.py` adds one local file read only on the "what do you remember" branch.

## 5. What was improved (fixes from this verification)

1. Suite unrunnable → collection fixed (tests/conftest.py).
2. Live e2e tests polluting offline runs → `e2e` marker deselected by default.
3. STT latency gate false-failing under GPU contention → device-aware budgets.
4. Wake echo self-test race under load → 60 s verdict wait.
5. **Product bug: "what do you remember about me" didn't include the owner's identity** →
   profile consulted (name/city/role), graceful fallback; live e2e B passes.

## 6. Known limitations / honest risks

- Dedicated stress/soak and the live contract e2e were not run on the busy box; commands
  are documented (TESTING.md) and should be run when idle.
- Coverage % unknown until pytest-cov is approved/installed.
- Concurrent threads were actively editing arch2/VRAM/packaging during this pass; this
  report reflects the tree as verified. Nothing was committed by this thread.

## 7. Recommended next steps (priority order)

1. Approve the batch pip install (pytest-cov + training stack) → add a coverage gate.
2. `ollama pull qwen3:14b`, then install + bind Flowise and ScrapeGraphAI live.
3. Run the idle-box set: `-m e2e` contract tests, 1-hour soak, clean-install rehearsal.
4. Answer the Apodex/Pi-Desktop questions to close the two external-integration rows.
5. Settings-UI tabs for memory/telemetry/skills (endpoints already live).
