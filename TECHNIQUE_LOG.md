# TECHNIQUE_LOG — every optimization measured, kept or reverted (Cycle 1)

Format: one row per attempted change. **KEEP** = measured gain + perf_guard PASS + suite green.
**REVERT** = measured gain ≤ noise, or any regression. No entry without numbers.

Baseline: `OPTIMIZATION_BASELINE.md` (medians from `_work/bench_full_baseline3.json`).
Noise floor: import ~8%, skills_load ~1.5%, route ~20% (CPU scaling), TTS ~13% — per-bench tolerances in `tools/perf_guard.py`.

## Kept

| # | Change | Target | Before (median) | After (median) | Gain | Evidence | Verification |
|---|---|---|---|---|---|---|---|
| K1 | Lazy OpenAI client in `ishti/llm.py` (`_get_client()` created on first use; `from openai import OpenAI` moved inside it) | `skills_load` (boot/import cost paid by 26 skill modules) | 1174.04 ms | 655.12 ms | **−44.2%** | importtime: `openai` = 589 ms cumulative, imported at module top only for client construction; no external consumer of `llm._client` (grep-verified) | `_work/bench_after_lazy_client.json`; perf_guard PASS (all benches within noise, skills_load −44%); full offline suite green (`_work/suite_after_lazyclient.log`, 184 passed); commit `eedb20e` |
| K2 | Lazy MCP hub in `ishti/skills/browser/skill.py` (`get_hub()` now a call-time shim importing `ishti.mcp_client` on first use) | `skills_load` (the `mcp` package = 362 ms cumulative, imported at boot via the module-level import) | 655.12 ms | 358.43 ms | **−45.3%** (cumulative **−69.5%** vs baseline) | importtime post-K1: `mcp` = 362 ms, `ishti.mcp_client` = 403 ms; all `get_hub()` call sites are inside functions; tests patch `skill.get_hub` as a module attribute (shim preserves that) | `_work/bench_after_lazy_mcp.json`; perf_guard PASS; full offline suite green (`_work/suite_after_lazymcp2.log`); commit `c3dbb91` |

## Reverted

| # | Change | Measurement | Reason |
|---|---|---|---|
| (none yet) | | | |

## Rejected before touching code (measured, not worth changing)

| # | Idea | Measurement | Verdict |
|---|---|---|---|
| R1 | Cache manifests across `load_skills()` calls (process-lifetime) | Boot path calls `load_skills()` once per process (dispatcher `SKILLS = load_skills()`); bench measures cold load, which is the real cost | Rejected: caching would only game the benchmark, not the runtime |
| R2 | Lazy-import skill modules (defer `skill.py` import until first route) | `matches(text)` (deterministic triggers) is checked on EVERY route before Laya — needs the module; deferring would move the cost into the first request and complicate `status`/broken-skill handling | Rejected: cost is real boot work (122 skills); deferral shifts it onto the reply path |

## Candidates under measurement (no change yet)

- Remaining ~250–300 ms of `skills_load` is the genuine import of 122 `skill.py` modules — the floor without per-skill refactors (K2's projected ~260 ms nearly reached: 358 ms).
- `voice_tts` (Piper, CPU) 420 ms median with ~13% run-to-run noise — no local lever found (onnxruntime GPU offload for piper-tts is not supported by the installed package); rejected without a code change.

## Session note (2026-10-01)
- Bench→suite interference found and fixed: bench_opt left the planner resident in VRAM; a later arch2 test's live LLM call then downshifted `qwen3:14b → qwen2.5-c8k:3b` and failed its assertion. bench_opt now restores VRAM (quick mode unloads the planner; full mode unloads everything it loaded).
- Same class, second sighting (final gate): the per-skill browser suite's models were still resident (10-min keep-alive) when the full suite started; the arch2 live-LLM test sat in `vram.admit` until pytest's 120 s cap fired. With VRAM cleared first, the suite is green. Rule for live-LLM test sessions on this 16 GB machine: **clear VRAM between suites** (`keep_alive: 0` for everything in /api/ps). Logged as a harness caveat, not a product bug — the governor was correctly refusing to fit an 8.6 GB model next to a resident planner.
- Final Cycle-1 gate: `_work/suite_final_cycle1b.log` — full offline suite green after clearing VRAM; perf_guard PASS.
