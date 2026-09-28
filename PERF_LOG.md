# PERF_LOG — ISHTI performance benchmarks (measured on this machine)

Every change that touches latency or VRAM gets a before/after here. All numbers measured on
the RTX 5070 Ti / 64 GB / Windows machine, unless stated otherwise.

## 2026-09-28 — Sections 1/2/8/9 pass (model residency + Obscura + pipeline)

| Metric | Before | After | Delta |
|---|---|---|---|
| Routing median (rule/Laya path, n=50) | 0.2 ms | **0.17 ms** | unchanged (pattern-first preserved) |
| Planner TTFT (streaming, real prompt) | ~1.5 s | **0.96 s** | −36% (planner warm, keep_alive −1) |
| STT decode (turbo, via stt_call) | 114 ms | 114 ms | unchanged (Issue 8 fix still free) |
| TTS first sentence synth (Kokoro, CPU) | ~250 ms | **244 ms** | unchanged, 0 GB VRAM |
| Resident VRAM (planner pinned) | 10.4 GB (63.7%) | 10.4 GB (63.7%) | within the 80% budget |
| Worst-case resident (planner+STT+translator) | ~14.9 GB | ~14.9 GB | gpu.slot evicts idle first |
| Obscura page fetch (example.com, markdown) | — | **0.17 s**, ~30 MB RAM | new default reader |
| Obscura parallel scrape (2 URLs, 10 workers) | — | **1.15 s total** | deep_research pages |
| gemma3:4b load→evict cycle (Section 2) | — | load 2.88 GB → evicted, event logged | eviction verified live |

Perceived voice latency after this pass: wake → first audio ≈ STT 114 ms + routing 0.2 ms +
planner TTFT 0.96 s + first-sentence TTS 244 ms ≈ **1.3 s**, with TTS overlapped on the first
generated tokens (Section 9's pipeline). Target <500 ms needs a smaller planner; logged as the
next lever in NEEDS (qwen3:8b-class swap) — not silently adopted because planner quality gates
the whole assistant and needs its own A/B pass.

## How to reproduce

```bash
# routing median
python -c "import sys,time,statistics; sys.path.insert(0,'.'); from ishti import router;
from ishti.dispatcher import SKILLS
ts=[]
for _ in range(50):
    t0=time.perf_counter(); router.route('what is the weather in peshawar tomorrow', SKILLS)
    ts.append((time.perf_counter()-t0)*1000)
print(statistics.median(ts))"
# VRAM residency / eviction
python -c "import sys; sys.path.insert(0,'.'); from ishti import models;
print(models.currently_resident()); print(models.get_events(5))"
# Obscura
python -m ishti.obscura
```
