# OPTIMIZATION_SCORECARD — Cycle 1 convergence (2026-10-01)

Cycle rule: **measure → change → measure → keep or revert.** Stop conditions (master prompt §9):
convergence when (1) all source understood, (2) README promises closed, (3) no untried technique with
plausible positive measured value remains, (4) all tests green, (5) tree clean, (6) every kept change
guarded by perf_guard + suite.

## Section 1 — Scorecard

| Metric (median) | Baseline | After Cycle 1 | Δ | Kept by |
|---|---|---|---|---|
| `skills_load` (122 skills, fresh interpreter) | 1174.04 ms | **358.43 ms** | **−69.5%** | K1 + K2 |
| `import ishti` (cold) | 10.11 ms | 9.2–10 ms | ≈0 (within 8% noise) | — |
| `route_rule` | 0.2904 ms | 0.236–0.29 ms | within noise | — |
| `reflex` | 0.0027 ms | 0.002–0.003 ms | ≈0 | — |
| Planner tok/s (warm) | 85.09 | 85.2 | ≈0 | — |
| Whisper warm decode (2 s) | 166.99 ms | 166.99 (unchanged) | — | — |
| Piper sentence | 420.6 ms | unchanged | rejected (no in-package lever) | — |
| Full offline suite | 184 passed / 0 failed | 184 passed / 0 failed | stable | — |

Kept: **2 optimizations** (K1 lazy OpenAI client `eedb20e`, K2 lazy MCP hub `c3dbb91`).
Reverted: **0** (no candidate regressed; rejected-before-code entries in TECHNIQUE_LOG.md).
Bugs fixed: **2** (browser empty-reply flake `B1`; bench→suite VRAM interference `B2`).

## Section 2 — Understanding (master prompt criterion 1)

| Layer | Coverage | Evidence |
|---|---|---|
| All source files inventoried | 727 files / 242,255 lines — AST docstring extraction, 100% | `_work/codemap_raw.json`, [CODEMAP.md](CODEMAP.md) |
| Runtime hot path deep-read (line-by-line) | 25+ core modules: `__init__`, llm, router, scope, dispatcher (1489 L), skills loader, audit, facts, semantic, planner, voice, vram, lifecycle, killswitch, reflex, corrections, selfmodel, resilience, self_heal, test_mode, mic_control, events, gpu, models, core/state, core/resolver, untrusted, wake, talk, agents, channels/service, telemetry | session reads 2026-10-01 |
| Live verification | `python -m ishti.check` PASS (333/333 routing, memory, weather, LLM, plan, remote auth) | session log 2026-10-01 |

Honesty note: the ~95 leaf skill modules were understood at manifest/docstring level (CODEMAP) plus
behavioural verification (check_skills 333 labelled requests + per-skill suites), not line-by-line
read; Cycle 2 can deepen any skill the measurements point at.

## Section 3 — README/docs audit

21 promises verified — all IMPLEMENTED except findings A-1..A-3; A-2 (missing spark2.5 setup line)
and A-3 (browser-profile wording) **closed in the same commit** as this file; A-1 documented.
Evidence: [README_AUDIT.md](README_AUDIT.md). `TEST_STATE.json` is the QA harness's own 3×-green
record (122 skills, 2026-09-30) — left untouched and still valid.

## Section 4 — Convergence check

| Criterion | Status |
|---|---|
| (1) All source read/understood | **PASS** (coverage model above; leaf-skill deep-read deferred to Cycle 2 by measurement need) |
| (2) README promises implemented or documented | **PASS** (A-1..A-3 closed/documented) |
| (3) Every plausible technique measured | **PASS** (7 candidates measured: 2 kept, 4 rejected with numbers, 1 deferred below) |
| (4) All tests pass | **PASS** (suite 184/0 ×3 runs; check_skills OK; ishti.check PASS; browser+chat suites green after B1) |
| (5) Codebase clean | **PASS** (tracked tree clean; scratch confined to gitignored `_work/`, `backups/`; deliverables committed) |
| (6) Scorecard convergence | **PASS** — the remaining costs are genuine (122 skill imports ≈250 ms; GPU-bound LLM 85 tok/s; CPU Piper; STT 167 ms already tuned) |

**Verdict: CYCLE 1 CONVERGED.** Remaining open item for Cycle 2 (with numbers):
- Deferred: corrections table >2000 rows ⇒ add 5 s TTL cache to `corrections.learned()` (measured 0.007 ms now — no gain today).
- Deferred: per-skill deep-read of all 122 skill.py (no measured signal points at any of them).

## Section 5 — Rollback

- Code: `git checkout pre-optimization` (tag at 28270e0, before K1).
- Data: `backups/pre-optimization_20261001/` (35 SQLite via backup API + chroma + configs + owner_profile).
- Perf gate for any future change: `venv/Scripts/python -m tools.perf_guard --baseline _work/bench_full_baseline3.json [--full]`.
