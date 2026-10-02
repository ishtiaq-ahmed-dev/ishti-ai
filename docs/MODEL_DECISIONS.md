# MODEL_DECISIONS — per-role ladders, measured on THIS machine (2026-09-29)

Machine: RTX 5070 Ti 16 GB (16303 MiB), 64 GB RAM, Windows 11, Ollama 0.31.2 (OpenAI-style API on :11434).
Every number below was measured here with `python -m ishti.vram_calibrate` (real loads, `/api/ps`, nvidia-smi, fixed
prompts) and `tests/e2e_vram_lifecycle.py`; raw data: `data/vram_calibration.json`, `data/vram.db` (`model_footprints`).
The policy lives in `models_ladder.yaml`; the code is `ishti/vram.py` (governor) and `ishti/lifecycle.py`.

> **2026-09-30 update — new brain: Spark-X2.5-4B (unquantized F16), measured.** Ollama upgraded to 0.35.0
> (0.31.2 could not run the `spark2_5` architecture; models/settings untouched by the upgrade).
> The planner's rung 0 is now `ishti-spark2.5:4b` (F16 = unquantized, Apache-2.0, source chain and SHA-verified
> local GGUF in `docs/build-history/prompts-1-4/MODEL_ACQUISITION_spark2.5.md`); the 14B drops to rung 1.
> Measured (`python -m ishti.vram_calibrate ishti-spark2.5:4b`, in `data/vram.db`): **8165 MiB fully on GPU at
> 8192 ctx, 84.6 tok/s, 43 ms first token, 3.3 s load** — ~2 GB lighter than the 14B and faster.
> **Thinking model — quality 5/5 after the harness fix (2026-09-30, same day).** The calibrate harness's
> 160-token cap (sized for the non-thinking 14B) truncated Spark's reasoning stream and recorded a misleading
> 3/5. `ishti/vram_calibrate.py` now detects the `thinking` capability via /api/show, grades thinking brains at
> a 2048-token budget, and judges only the text after `</think>` (an unchanged-logic control re-run of the 14B
> reproduced its 5/5). Re-graded (data/vram_calibration.json, `think: true, quality_num_predict: 2048`):
> Spark 5/5 — arithmetic, json, table, urdu, tool_call all ok. Thinking is per-request toggleable
> (`"think": false` → direct answers, big voice-TTFS lever). The planner + Whisper headroom math from the
> 29 Sep tables improves by ~2 GB; voice-session downshift now lands on the 14B (rung 1), not the 7B.
> Everything below the update line describes the pre-Spark state.

## What changed since the 28 Sep version, and why (corrections first)

| Earlier statement | What measurement shows |
|---|---|
| "Laya runs on CPU/onnx, near-zero VRAM" | **Laya runs on the GPU** (`ishti/router.py`: `Router(device="cuda")`, fp16). The ISHTI process holds **~1.5 GB** of VRAM after warm-up and peaks at **3.8 GB while Laya loads** (fp32 first, then halved). The boot now waits for that to finish before the planner loads. |
| "planner + Whisper worst case ≈ 12.0 GB = 74%" | The planner is 9.9 GiB, the rest of the machine (Windows compositor, browsers, ISHTI's own process incl. Laya) was **2.9 GB** with the server running, and Whisper turbo grew the GPU by **3047 MiB**, not 1.1 GB. Planner + Whisper = **84–86%**: over the 80% budget. So a live voice session takes the planner **one rung down (7B)** - by design (voice has priority). |
| "translator/vision on demand ≈ 2.88 GB" | Ollama reports 2742 MiB for `gemma3:4b`, but the GPU grew by **3857 MiB** (the vision projector is not in Ollama's number). Same for `gemma3:12b`: 7672 reported, **8973** real. The governor plans with the larger figure. |
| `config.json` planner = `qwen3:30b-a3b` | That model is **not installed** and is 19 GB (does not fit); `llm.model_for` silently falls back to `ishti-qwen2.5:14b`. The ladder now names the real chain; the config value is left for the owner (ISSUES.md #21). |

## The ladders (measured)

"GPU MiB" = what the GPU really grew by when the model loaded (Ollama's own `size_vram` in brackets). Quality =
`ishti/vram_calibrate.py` fixed prompts: arithmetic, JSON extraction (`{"contact","message"}`), Markdown table, Urdu
translation (checks for Arabic-script output only), a tool call (`get_weather`), and for vision models reading "PRICE 42"
from an image. These are **my** prompts, not yours - see NEEDS.md ("quality notes need your own prompt set").

| Role | Rung | Model (ctx) | GPU MiB (Ollama) | tok/s | first token | load | Quality | Note |
|---|---|---|---|---|---|---|---|---|
| **planner** (pinned) | 0 | `ishti-qwen2.5:14b` (8192) | 10151 (9922) | 79.5 | 157 ms | 3.2 s | 5/5 | your Modelfile, never touched |
| | 1 | `qwen2.5:7b` @8192 → alias `qwen2.5-c8k:7b` | 5121 (4896) | 143.2 | 149 ms | 2.4 s | 4/5 | **Urdu translation prompt failed** |
| | 2 | `qwen2.5:3b` @8192 → `qwen2.5-c8k:3b` | 2517 (2292) | 240.9 | 146 ms | 2.0 s | 5/5 | last resort |
| **coder** (10 min idle) | 0 | `ishti-qwen2.5-coder:14b` (8192) | 10151 (9922) | 80.9 | 157 ms | 5.0 s | 4/5 | tool-call prompt failed (also on rung 1) |
| | 1 | `qwen2.5-coder:7b` @8192 → `qwen2.5-coder-c8k:7b` | 5121 (4896) | 146.0 | 150 ms | 3.0 s | 4/5 | |
| **fast** | 0 | Laya (CPU-independent router, GPU fp16) | ~1300 in the ISHTI process | ~35 ms/decision | | | - | not an Ollama model |
| | 1 | `llama3.2:3b` (4096) | 2661 (2436) | 245.0 | 181 ms | 2.8 s | 5/5 | emergency classifier only |
| **translator / vision tier 2** | 0 | `gemma3:4b` (4096) | 3857 (2742) | 163.7 | 397 ms | 3.6 s | 5/5 | vision ok (read "PRICE 42") |
| **vision tier 3** | 0 | `gemma3:12b` (4096) | 8973 (7672) | 85.6 | 412 ms | 4.6 s | 5/5 | vision ok |
| | 1 | `gemma3:4b` | 3857 (2742) | | | | | = tier 2 |

Verified against the Ollama library before use (no invented names): `qwen2.5:7b` 4.68 GB, `qwen2.5:3b` 1.93 GB,
`qwen2.5-coder:7b` 4.68 GB, `gemma3:12b` 8.15 GB, `gemma3:4b` 3.34 GB (registry manifests, 29 Sep). Downloaded for the
ladders: `qwen2.5:7b`, `qwen2.5:3b`, `qwen2.5-coder:7b`, `gemma3:12b` (19.5 GB). Approved but **not** pulled (skipped by the
governor, never downloaded at runtime): `qwen2.5-coder:3b`. Alias models (`*-c8k:*`) are created by `POST /api/create`
from the same weights with `PARAMETER num_ctx 8192`; they take no extra disk.

**Why aliases:** Ollama's OpenAI-style endpoint (`/v1`, what `llm.py` uses) ignores per-request `options`, and reloads a model
back to its default context on the next call (measured: 4.5 s reload every time). A different `num_ctx` therefore has to be
a different model name. The planner is never run below 8192 context (`min_ctx`): ISHTI's prompts need it.

## The environment the budget is measured against

| | MiB |
|---|---|
| GPU total | 16303 |
| Budget (80%) | 13042 |
| Windows + browsers + tray apps, no ISHTI (`dwm.exe` alone reports ~4.2 GB, but per-process figures overlap; total-used is the ground truth) | 886–1200 |
| ISHTI server process after warm-up (CUDA context + Laya fp16) | ~1500 |
| Planner 14B | 10151 |
| → planner alone, ISHTI running | ~78.5% (12.8 GB) |
| Whisper turbo float16 (measured) | +3047 |
| → planner + Whisper | ~84–86% → planner goes to 7B during a voice session |

Per-process VRAM: nvidia-smi and NVML show `[N/A]` under WDDM, so per-process attribution comes from Windows'
`\GPU Process Memory(*)\Dedicated Usage` counters (read in-process with pywin32 `win32pdh`, ~100 ms). Those can add up to
more than the adapter total, so **`nvidia-smi` total-used is the ground truth** for every decision.

## Ollama's own fit rule (why the governor keeps 1.5 GB of headroom)

Read from Ollama's debug log while loading `gemma3:12b` next to a 7B: `llama-server model predicted to exceed available
memory, evicting: predicted="9.0 GiB" available="9.9 GiB"`, and llama.cpp's fitter: `will leave 10140 >= 1024 MiB of free
device memory`. So Ollama needs **≥ 1 GiB free after every load** (`LLAMA_ARG_FIT_TARGET`, default 1024 MiB), and its predicted
size ran ~240 MiB above what the GPU really grew. Where it can't make room it evicts an idle model (silently taking the
pinned planner with it) or splits the new model between GPU and CPU. `models_ladder.yaml: ollama_headroom_mb: 1536`
enforces that as a hard rule on top of the 80% budget. Before this rule, a 95%-budget run let a planner and a vision model
"fit" on paper while Ollama evicted the planner; the strict test now requires both resident together.

## How the governor decides (spec §1.3, implemented in `ishti/vram.py`)

Before any heavy load it asks whether the load fits (measured footprint + 128 MiB margin inside the budget, and ≥ 1536 MiB
free afterwards). If not, cheapest first: (1) unload idle on-demand models, (2) planner one rung lighter (never during a live
voice turn - unless the load *is* the voice pipeline), (3) less context for the new model **only if that context was
measured to save memory** (sliding-window models such as Gemma 3 saved 2 MiB going 4k → 2k; the full-attention formula
predicted ~770), (4) a lighter rung of the new model's own ladder (vision tier 3 → 2, coder 14B → 7B) unless the caller
requires the tier, (5) the planner further down, (6) queue with a plain reason. After the load it verifies
`size_vram == size`; on a partial offload the model is unloaded at once and the next lighter step is tried. A model that has
never been measured needs 1.35 × its estimate free, then is measured on its first load. Upshift: one rung at a time, only
when no heavy job is active or queued, no voice turn is running, and the memory has been free for `hysteresis_s` (90 s;
idle on-demand models are evicted first because they reload on demand and the pinned planner has priority).

Measured transitions (RTX 5070 Ti): downshift + heavy load 6–9 s end to end; upshift back to the 14B 34–50 s after the job
(8 s hysteresis in the test, 90 s in production). Planner first-token latency was **154–160 ms idle (median 157–165 ms while a heavy job waited during a voice turn; the worst single
sample 233–420 ms** across runs, partly CPU contention inside the single test process).

## Decision per role

| Role | Decision | Rationale |
|---|---|---|
| planner | `ishti-qwen2.5:14b` → `qwen2.5:7b` → `qwen2.5:3b` | The 14B is the only rung with a perfect score on my prompts; the 7B fails the Urdu prompt, so the UI should say so when it switches (the `model_switch` reason does). `qwen3:30b-a3b`, `qwen3-coder:30b-a3b`, `qwen-27b` remain rejected (19 GB, MoE loads all experts; `qwen-27b` measured: loading it through raw Ollama gives a 13614/19272 MiB **partial offload**). |
| coder | `ishti-qwen2.5-coder:14b` → `qwen2.5-coder:7b` | A coder request while the planner is resident needs the planner to go down (14B+14B can't co-reside). |
| fast | Laya | Unchanged decision; the VRAM cost is now on the record. |
| translator | `gemma3:4b` | No lighter model produced acceptable Urdu in the earlier comparison; single rung. |
| vision | tier 2 `gemma3:4b`, tier 3 `gemma3:12b` | Tier 3 (8973 MiB) can only sit next to a planner **down to the 3B** and only above the default 80% budget (95% in the strict test: 14B → 3B, both resident, 83% peak). At the 80% default the governor gives the job tier 2 and says so. To use tier 3 routinely raise `budget_pct` or accept the 3B planner meanwhile. |

## Ollama settings (verified on 0.31.2; applied by ISHTI to an Ollama it starts)

| Setting | Value | Notes |
|---|---|---|
| `OLLAMA_NUM_PARALLEL` | 1 | parallel requests multiply KV cache; ISHTI serializes model calls anyway |
| `OLLAMA_MAX_LOADED_MODELS` | 4 | planner + one on-demand model + the embedding model (`nomic-embed-text`, 308 MiB) + one spare |
| `OLLAMA_KEEP_ALIVE` | 10m | pinned roles (planner) are loaded with per-request `keep_alive: -1`; the `/v1` endpoint ignores it, so `models.used()` re-posts it |
| `num_gpu` | **never set** | forcing layers off the GPU is exactly the partial offload this project forbids |
| `OLLAMA_KV_CACHE_TYPE` | f16 (default) | see the KV/flash-attention section below |
| `OLLAMA_CONTEXT_LENGTH` | unset | Ollama's VRAM-based default is 4096 at this size (log: `vram-based default context ... default_num_ctx=4096`); the planner's 8192 comes from its Modelfile |

Env vars need an Ollama restart: `POST /api/ollama/settings` saves them, `POST /api/ollama/apply` restarts an Ollama **ISHTI
started** (it stops it first and unloads the models). An Ollama that was already running (the Windows tray app, another
tool) is never restarted without an explicit `{"take_over": true}`; the response says `needs_restart` and how.

## KV cache type and flash attention (measured by `tests/kv_cache_probe.py` on a private Ollama, 29 Sep)

Same model (`ishti-qwen2.5:14b`, 8192 ctx), same prompts; the only difference is the environment the private Ollama was started with.

| Setting | GPU MiB | tok/s | Fixed ISHTI prompts | Long-context recall (a code word hidden at 3 positions in ~6k tokens) |
|---|---|---|---|---|
| `OLLAMA_KV_CACHE_TYPE` unset (f16) | 9922 | 80.9 | 5/5 | 3/3 |
| `OLLAMA_KV_CACHE_TYPE=q8_0` (+ `OLLAMA_FLASH_ATTENTION=1`) | **9202** (-720, -7%) | 77.1 (-5%) | 5/5 | 3/3 |

- **Flash attention is automatic on this version/GPU**: Ollama 0.31.2's log says `llama_context: flash_attn = auto` then
  `Flash Attention was auto, set to enabled` even with `OLLAMA_FLASH_ATTENTION=false` in its (default) config. Nothing to set
  for f16; a quantized KV cache needs it forced on, which ISHTI's env mapping does.
- **q8_0 saves ~720 MiB at 8k context** (it grows with context) and costs ~5% speed here. On my prompts and a 6k-token recall
  test there was **no quality difference**, but the reported problem is with *long* contexts and hard reasoning, which these
  tests only touch lightly (8k window). **Decision: left off (f16).** It is a setting (`kv_cache_type` in
  `POST /api/ollama/settings`) and takes effect at the next Ollama start ISHTI performs. Enable it only after grading on your own
  long prompts (NEEDS.md N2): 720 MiB is the difference between the 7B rung and the 14B fitting next to Whisper only in
  marginal cases.
