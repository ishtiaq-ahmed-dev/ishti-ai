# OPTIMIZATION_BASELINE — measured before any optimization (Cycle 1)

**Rule of this whole exercise: measure → change → measure → keep or revert.** Every number below was
measured on this machine on **2026-10-01** with the `pre-optimization` tag checked out state (commit
`28270e0`). Rollback: `git checkout pre-optimization` (code) + `backups/pre-optimization_20261001/` (data).

## Environment (ENVIRONMENT.md summary; full file has the evidence)

| Item | Value |
|---|---|
| OS | Windows 11, build 26100 |
| Python | 3.11.9 (venv) |
| GPU | RTX 5070 Ti 16 GB (nvidia-smi live) |
| Ollama | UP, owned by owner session (pid 13764); `qwen2.5-c8k:7b` resident at session start |
| Key versions | fastapi 0.141.1 · uvicorn 0.53.0 · httpx 0.28.1 · pydantic 2.12.5 · pytest 9.1.1 · chromadb 1.1.1 · faster-whisper 1.2.1 · torch 2.11.0+cu128 · pywin32 312 · laya 0.3.20 · mcp 1.28.1 · piper-tts 1.8.0 · openwakeword 0.6.0 · onnxruntime 1.30.0 · openai 2.54.0 · psutil 7.2.2 · Node 24.16.0 (MCP browser) |
| Models configured | planner `ishti-spark2.5:4b` · coder `qwen3-coder:30b-a3b` (not pulled → fallback `ishti-qwen2.5-coder:14b`) · fast `laya` · translator `gemma3:4b` · fallback `ishti-qwen2.5:14b` |

## Full offline test suite (the E4 one-command suite; default pytest run)

| Run | Result | Wall | Evidence |
|---|---|---|---|
| 1 | **184 passed, 12 deselected, 0 failed** (`EXIT=0`) | ~240 s | `_work/baseline_suite_run1.log` |

(The 12 deselected are the `e2e`-marked live-backend contract tests, run explicitly per pytest.ini.)
Canonical baseline bench JSON = `_work/bench_full_baseline3.json` (all benchmarks green incl. STT; runs 1–2 are kept for the noise table).

## Micro-benchmarks — `tools/bench_opt.py` (new harness, medians)

| Benchmark | Baseline median | Notes |
|---|---|---|
| `import_ishti` | **10.11 ms** | cold `import ishti`, fresh interpreter, 5 runs |
| `skills_load` | **1174.04 ms** | `load_skills()`: 122 skills, manifests + every `skill.py` import, fresh interpreter |
| `route_rule` | **0.2904 ms** | rule-matched route over the full 122-skill table, 300 runs |
| `reflex` | **0.0027 ms** | instant-command table lookup, 2000 runs |
| `llm_gen` (planner, warm) | **85.09 tok/s** · wall 0.82 s | `ishti-spark2.5:4b`, 64 tok, temp 0, 5 runs, fully on GPU (8165 MiB, `size_vram == size`) |
| `llm_translate` (gemma3:4b, warm) | **≈186 tok/s** · wall ≈0.23 s | 32 tok; model-swap bench |
| `voice_stt` (whisper turbo, warm) | **166.99 ms** | 2 s clip, heard text verified correct, 5 runs |
| `voice_tts` (Piper) | **420.6 ms** | one sentence, 5 runs (noisy: see below) |

Raw JSON: `_work/bench_full_baseline3.json` (canonical), `bench_full_baseline.json`/`2` (earlier runs kept for the noise table below), `_work/bench_quick_baseline.json`.

### Measured run-to-run noise (runs 1 → 2 → 3, same tree)

| Bench | Runs | Spread |
|---|---|---|
| import_ishti | 9.29 / 9.74 / 10.11 ms | ~8% |
| skills_load | 1163.6 / 1156.5 / 1174.0 ms | ~1.5% |
| route_rule | 0.288 / 0.290 / 0.290 ms | ~1% |
| llm tok/s | 83.9 / 85.3 / 85.1 | ~1.6% |
| voice_stt | 167 ms (one clean run) | — |
| voice_tts | 373.6 / 426.0 / 420.6 ms | **~13%** — treat TTS deltas <15% as noise, confirm with extra runs |

perf_guard tolerance is 15%, above the measured noise for every bench except TTS (borderline).

## Known costs measured earlier (repo docs, not re-derived here)

- Laya route decision ≈ 35 ms (fp16, GPU) — `ishti/router.py` docstring.
- Self-model block: ~91 ms cold, ~1 ms cached — `ishti/llm.py` style().
- Whisper turbo warm decode ≈ 0.3 s; cold load = seconds — `ishti/voice.py` docstring.
- Batched STT wins from >8 s audio (BATCH_OVER_S measured) — same file.

## Baseline candidate targets (hypotheses only — nothing changes without a measurement)

1. `skills_load` 1.16 s: every `skill.py` is imported at boot. Any wasted import in the hot path shows here.
2. Planner prompt build (`llm.style()` + self-model): measured 91 ms cold / cached ~1 ms — verify cached path is the common one.
3. Route log + learning hooks per turn run as daemon threads — verify no thread-creation cost is on the reply path.
4. VRAM governor: verify the planner never partially offloads under the benchmark load (`ollama_loaded.full == true`).

## What is BLOCKED at baseline

- Nothing: Ollama is UP (owner started it), so GPU/voice measurements are possible this session. If it
  goes down mid-loop, GPU-dependent measurements are recorded as BLOCKED (N24) and never guessed.
