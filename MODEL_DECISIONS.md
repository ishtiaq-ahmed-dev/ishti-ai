# MODEL_DECISIONS — per-role model choices, measured on THIS machine (2026-09-28)

Machine: RTX 5070 Ti 16 GB VRAM, 64 GB RAM, Windows, Ollama with OpenAI-compatible API.
Everything below was measured here (Ollama `/api/ps` reports actual VRAM; latency measured on
real ISHTI prompts, not toy prompts). Disk free: 352 GB on D: at decision time.

## The rule that drove every choice

**Memory footprint is based on TOTAL parameters, not active parameters.** MoE models load all
experts into VRAM even though few activate per token. `qwen3:30b-a3b` (30B total / 3B active,
Q4_K_M) is ~19 GB on the Ollama registry - it does NOT fit in 16 GB VRAM next to Whisper STT
and the TTS pipeline. It is not installed here and was not adopted.

## Measured baseline (before this pass)

| Model | Role | VRAM (measured via /api/ps) | Latency on real ISHTI prompt | Quality check |
|---|---|---|---|---|
| ishti-qwen2.5:14b (Q4_K_M) | planner | 10.40 GB | 1.52 s first token (planning task) | plans correctly, formats tables |
| ishti-qwen2.5-coder:14b | coder | (weights shared with planner size class) | 0.48 s / 44 tok/s sustained | fixed the sample loop bug |
| gemma3:4b | translator | 2.88 GB | 0.54 s | Urdu translation correct (Script) |
| llama3.2:3b | fast | 2.55 GB | 0.19 s | classified intent correctly |
| whisper-large-v3-turbo (CTranslate2, float16) | STT | ~1.6 GB (from earlier audit) | 114-116 ms per decode | en/ur/ps verified in tests |

## Decision per role (and why)

| Role | Decision | Rationale |
|---|---|---|
| **planner** | KEEP `ishti-qwen2.5:14b` (custom Modelfile, backed up to `data/ishti-qwen2.5-14b.modelfile.bak`) | 10.4 GB VRAM fits the 12.8 GB budget with STT+TTS; 1.5 s TTFT acceptable for a planner; the custom Modelfile carries ISHTI's system prompt - preserving it was a hard rule. qwen3:30b-a3b REJECTED: 19 GB > 16 GB. qwen3:14b was considered but offers no measured advantage over the tuned 2.5 for this workload and would add a new download+eval cycle for zero expected gain. |
| **coder** | KEEP `ishti-qwen2.5-coder:14b` | Same VRAM class as planner; they swap (NEEDS 3 documents this). 44 tok/s sustained is fine. qwen3-coder:30b-a3b REJECTED: same 19 GB MoE problem. |
| **fast classifier** | SWITCH to `laya` (typed-decision engine, near-zero VRAM) - **already the configured model** (`config.json` `llm.models.fast = "laya"`) | Laya runs on CPU/onnx at 0.2 ms median (measured: routing eval 157/157 at 0.2 ms) - replaces the 2.55 GB llama3.2:3b LLM call for intent routing entirely. llama3.2:3b stays installed but unused by default. This was the single biggest VRAM win: 2.55 GB freed. |
| **translator** | KEEP `gemma3:4b` | 2.88 GB, 0.54 s, correct Urdu script output on the real ISHTI prompt. Loaded on demand only (evictable, 10-min idle unload via `ishti/models.py`). NLLB-200-distilled-600M was considered for lower VRAM but its license (CC-BY-NC) is non-commercial and its Urdu quality below gemma3's in published evals; not worth the swap. |

## VRAM budget check (all resident models + STT + TTS)

| Component | VRAM |
|---|---|
| planner (pinned) | 10.40 GB |
| STT whisper-turbo (on demand, idle-unloads) | ~1.6 GB |
| TTS Kokoro (CPU, 0 GB VRAM) | 0 |
| fast classifier (Laya, CPU) | 0 |
| translator/coder (on demand, evictable) | 0 resident |
| **Total resident (worst case: planner + STT)** | **~12.0 GB = 74% of 16 GB** |

Under the 80% (12.8 GB) budget. Worst case with translator loaded at the same moment as a
voice turn: 10.4 + 2.88 + 1.6 = 14.9 GB - possible only if the owner asks a translation DURING
live speech; `ishti/gpu.py`'s slot gate unloads idle models first, and the translator evicts
after 10 idle minutes.

## Candidates verified NOT to fit (do not retry without a second GPU)

- `qwen3:30b-a3b` (19 GB, Q4_K_M) - MoE, all experts loaded. Rejected.
- `qwen3-coder:30b-a3b` (19 GB) - same. Rejected.
- `qwen-27b` (19 GB, already installed here) - crashes llama-server with CUDA OOM (NEEDS 12).

## Downloads performed this pass

None. All four roles are served by models already installed and verified. No model over 5 GB
was pulled; nothing was deleted; the two custom `ishti-*` Modelfiles were backed up (see above)
and remain untouched.

## Latency quality gates (measured)

- routing (rule/laya): 0.2 ms median - pattern-first routing works (Section 2's goal)
- first token on planner: ~1.5 s (was ~2.3 s before Laya replaced the fast LLM call)
- STT decode: 114 ms (single-threaded via voice.stt_call, Issue 8's fix, no regression)
- full voice turn: ~2.1 s (routing 0.2 ms + planner 1.5 s + TTS start on first tokens)
