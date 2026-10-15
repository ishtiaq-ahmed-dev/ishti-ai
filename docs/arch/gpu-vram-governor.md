# GPU / VRAM governor (ishti/vram.py)

Hard rule of the whole subsystem: **a model is never left partially offloaded** — the CPU/GPU split
of `ollama ps` is several times slower than full residency, so `size_vram < size` is a violation the
governor measures, plans and verifies away.

## The loop

1. **Measure** (all cached with short TTLs):
   - `gpu()` — nvidia-smi totals (0.4 s TTL) — the ground truth every GPU user counts.
   - `processes()` — Windows "GPU Process Memory" counters per pid (WDDM hides per-process from nvidia-smi).
   - `ps()` — Ollama's own view (`size`, `size_vram`, ctx). A FAILED read never counts as "nothing
     loaded" (that bug double-counted a resident planner): decision paths reuse the last good answer
     ≤30 s old, else raise; display paths get [].
   - `info()` — per-model default ctx + KV bytes/token from /api/show (1 h TTL).
   - Footprints DB (`data/vram.db`): measured size/size_vram/tps/gpu_delta per (model, ctx). The
     gpu_delta is the honest number (vision models carry ~1.2 GB Ollama doesn't report) and is
     tracked as the MIN over repeated loads (concurrent allocations only ever inflate it).
2. **Plan/admit** (`admit(model, role, job)`): fits in budget (80% of 16 GB)? else make room
   cheapest-first: unload idle on-demand models → planner one ladder rung lighter → shrink the new
   model's ctx (alias `name-c<k>k:tag`, same weights, created on demand) → lighter rung of the new
   model's ladder → more planner rungs → queue with a plain-language reason (`GpuBusy`).
3. **Verify**: after load, `size_vram == size` or the model is unloaded and the next rung is tried.
   The planner downshift (`_eff` map) is persistent until the heavy job ends AND VRAM stayed free
   for `hysteresis_s`, and never during a live voice turn (`touch_voice()` depth counter).

## Callers

- `llm._create` — busy-marks the model BEFORE the governor looks (a just-admitted model must never
  be picked as idle), admits, releases the lease when the last token streams out.
- `gpu.slot(need_mb, what)` — one heavy job at a time (semaphore), reserves memory, learns the job's
  real footprint (`learn_job`), never runs "anyway" (that's what split models before).
- `models.py` — the pinned planner model gets `keep_alive=-1`; others 10 min idle eviction;
  every load/unload logged to `data/model_usage.db`.
- `voice.stt_model` — whisper loads under `gpu.slot(3000, …, for_voice=True)`: voice has priority
  over the planner rung.

## Measured behaviours (this session)

- Warm `ishti-spark2.5:4b`: 8165 MiB, `full=true`, 85 tok/s eval, prompt eval 500–860 tok/s.
- `qwen3:14b` (8.6 GB arch2 role) downshifts to `qwen2.5-c8k:7b` (4.4 GB) when the planner is
  resident — the intended ladder behaviour, seen live during `python -m ishti.check`.
- Bench→test interference (fixed in tools/bench_opt.py): a bench leaving the planner resident
  downshifts later live tests' models; the bench now restores VRAM.
