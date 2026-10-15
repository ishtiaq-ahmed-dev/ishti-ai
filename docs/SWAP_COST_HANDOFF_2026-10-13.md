# Handoff: the VRAM governor must price its swaps in host RAM

Date: 2026-10-13. Author: the RAM-investigation session. **This is a spec, not a status update** — the
other agent implements it. Every number below was measured on the owner's PC today; nothing is estimated
unless it says so. Related evidence: [RAM_GROWTH_FINDINGS_2026-10-13.md](RAM_GROWTH_FINDINGS_2026-10-13.md).

## 0. State of the tree (read this first)

An **uncommitted first attempt already exists** in the working tree (the owner has asked for a report
instead of more code, and it may be kept or discarded — do not assume either):

| file | state | what it contains |
|---|---|---|
| `ishti/vram.py` | modified (+76/−4) | `_host_mb`, `_record_swap`, `swap_cost`, `_headroom_mb`, `_too_costly`, `_resident_mb`; `load()`/`unload()` bill themselves; `switch_role(..., cost_check=False)`; `upshift_tick` passes `cost_check=True`; `snapshot()` gains `swap_cost` |
| `tests/test_vram_swap_cost.py` | new | 5 hermetic tests (billing, round-trip waste, refusal before touching Ollama, the affordable path, floor opt-out) |
| `docs/API_CONTRACT.md` | modified | §5.1 prose, the example JSON and the `<!-- shape:vram -->` block list `swap_cost` |
| `docs/RAM_GROWTH_FINDINGS_2026-10-13.md` | new | the investigation this task came from |

**The attempt has one measured defect** (§5.1) — its host-RAM read enumerates every process and costs
~1 s per call. Either fix that in place or rewrite; do not ship it as is.
To discard it: `git checkout -- ishti/vram.py docs/API_CONTRACT.md && rm tests/test_vram_swap_cost.py`.

## 1. Goal and acceptance criteria

The governor loads and unloads models many times a session. Each load pulls the model's file back into
the Ollama runner's **host** working set — real money on a machine that is already short of RAM. Today
the governor sees only VRAM, so it is blind to that half of the bill.

Done means, all verifiable by running something:

1. **Measured, not guessed** — every model load and unload reports the host-RAM delta it actually caused.
2. **Reported** — reachable from `GET /api/vram` (the governor's documented interface), answering
   `{"history": [...], "round_trips": [...], "waste_mb": N}`.
3. **Acted on** — a swap that costs more than the machine can currently afford is refused *before*
   anything is unloaded or loaded, with a plain-language log line; a swap that avoids a partial offload
   is **never** refused (that is the governor's entire job).
4. **Free** — the instrumentation must not add measurable latency to the governor's 1 Hz sampler path
   (§5.1 is the trap: the naive version adds ~1 s per swap).

## 2. Why (measured today)

| Observation | Number |
|---|---|
| Historical soak drift (2026-09-30, three runs) | **+3,454 / +2,873 / +3,576 MB** per 8 min |
| The same soak today (retention since bounded) | **−652 MB** |
| One Ollama runner reload: runner host RSS | **0.97 GB → 8.7 GB** (≈ the 8.4 GB of weights) |
| System RAM during that single reload | **+8.2 GB once**, then **−469 MB** over the next 5 min of load |
| Live bill, `gemma3:4b` load | **+946 MB host / +3,857 MB VRAM** |
| Live bill, same model unloaded | **−945 MB host** |
| Live bill, reload 4.3 s later | **+945 MB host** → round trip, **945 MB paid twice** |
| Real app boot (`python -m ishti`, `GET /api/vram`) | planner load **+869 MB host / +10,160 MB VRAM**; evictions **−731** and **−943 MB** |

Note the 869 MB vs 8.7 GB spread for the *same kind* of load: the host cost depends on whether the weight
pages are already in Windows' standby cache. **Measure the delta; never use the file size as a proxy.**

## 3. Where swaps actually happen

`ishti/vram.py` (line numbers are from the current tree, which includes the attempt — re-grep, they move):

| anchor | line | role |
|---|---|---|
| `def unload(name, wait_s=20)` | 462 | posts `keep_alive: 0`, waits until `/api/ps` drops it |
| `def load(name, keep_alive)` | 482 | posts, then verifies full offload; also writes `data/vram.db` footprints |
| `def switch_role(role, k, reason, cost_check=False)` | 570 | the real swap: unload/load under the `_gov` RLock |
| `def admit(...)` | 761 | admission chain that can unload idle models / downshift the planner |
| `def upshift_tick()` | 976 | opportunistic upshift after `hysteresis_s`; its `switch_role(...)` call is the only one that should be cost-gated |
| `def check_violations()` | 1024 | unloads idle partial offloads — runs **every second** from the sampler |
| `def snapshot()` | 1036 | builds the `/api/vram` payload |
| `def start_sampler()` | 1077 | 1 Hz: `check_violations()`, `upshift_tick()` every 5th tick, `snapshot()` published as `vram_status` |

`switch_role` callers: inside `_apply()` (heavy-job downshift), `reserve()`, and `upshift_tick()`
(`grep -n "switch_role(" ishti/vram.py`).

Second module, also swaps — **do not forget it**: `ishti/models.py` posts `keep_alive` directly
(`evict()` line 115, `_keep_alive()` line 204) and runs a 10-minute idle-eviction timer per model
(`_schedule_eviction()` line 157). It already logs load/unload events to `data/model_usage.db` via
`log_event()`. Decide explicitly whether the bill covers these idle evictions (they *free* host RAM;
the cost shows up at the reload), and say so in the code comment.

## 4. Design (the lazy version that works)

1. **Measure once, around the real call.** Read Ollama's host MB immediately before and after
   `load()`/`unload()` and store the delta. What to count: the RSS of `ollama.exe` (manager) plus
   `llama-server.exe` (runner) — that is the memory an owner actually feels; `nvidia-smi` already covers
   the GPU half.
2. **Bill it in a bounded in-memory list** (last ~200 swaps): `{"at", "to", "frm", "host_mb", "vram_mb"}`.
   Do not add a database: `data/vram.db` (footprints) and `data/model_usage.db` (events) already exist;
   if persistence is ever wanted, add a column there, not a new file.
3. **Report it** as `swap_cost()` inside `snapshot()` → `GET /api/vram`, with **round trips**: a model
   unloaded and reloaded within `swap_cost_window_s` (default 180) paid its host cost twice — that is the
   avoidable waste, and `waste_mb` is the headline number.
4. **Avoid what cannot be afforded.** Gate the *opportunistic* upshift (`upshift_tick` → `switch_role`,
   a keyword flag so the admission/downshift paths are untouched): if
   `resident(frm) + need(to) > available_RAM - swap_free_floor_mb` (default 2048 MB, `0` disables),
   return before any Ollama call and print one plain line, e.g.
   `[vram] swap held: planner -> <model> needs ~10400 MB of host RAM at once and would leave under
   swap_free_floor_mb; staying on <current>`. Refusing means the role stays on the lighter rung and the
   next tick may retry — no request fails, no partial offload is created.

Config lives in `models_ladder.yaml` through the existing `cfg()` (it re-reads on mtime change):
`swap_free_floor_mb`, `swap_cost_window_s`. Both need a one-line entry in that file's comments.

## 5. Measured pitfalls (the part that saves a day)

### 5.1 The naive host-RAM read costs ~1 second — measured
```
_host_mb():     1017.5 ms per call (min 812 ms)   # psutil.process_iter over 409 processes
swap_cost():      3.68 ms per call
_headroom_mb():   0.19 ms per call
```
`load()`/`unload()` call it twice each, and `unload()` runs from `check_violations()` **every second**
while the sampler also rebuilds `snapshot()` and publishes it to `/ws/events`. Enumerating all processes
per swap would stall that loop. Fix: resolve the two Ollama PIDs once (by name), cache the
`psutil.Process` handles, and read `.memory_info().rss` on those — same number, ~0.1 ms. Invalidate the
cache when a cached PID no longer exists (the runner dies and is respawned on every reload).

### 5.2 `snapshot()` is on a 1 Hz hot path
Anything added to it is computed every second and broadcast to every UI client. Keep `swap_cost()` a
list walk over ≤200 dicts (measured 3.7 ms). A DB query in there would be a bug.

### 5.3 `ps()` raises on a failed read when `strict=True`
Decision paths need `strict=True` (the last good answer is returned only if < 30 s old, else it raises);
display paths use `strict=False` and get `[]`. A new call site must pick deliberately — a raise inside
`switch_role` would abort a swap mid-way with the old model already unloaded.

### 5.4 Everything above happens under the `_gov` RLock
`switch_role` holds it across the real Ollama HTTP calls, so added work directly extends the window in
which `admit()` from a live request is blocked. No new network calls inside the lock; keep new work to
one cached read.

### 5.5 The host cost is not the file size
Same model, two loads today: **+8,690 MB** (cold-ish, weights paged in) and **+869 MB** (pages already in
standby). Any policy based on the file size will be wrong in both directions; only a measured delta is
honest, which is the whole point of item 1.

### 5.6 Physical RAM is the wrong axis when commit is the binding constraint
This PC ran at **96.8 GB commit of a 97.5 GB limit** (99.3%) while physical available still read ~12 GB.
`psutil.virtual_memory().available` is physical only, and `psutil.swap_memory()` on Windows is the
pagefile, not commit. If commit headroom is wanted, it needs `GlobalMemoryStatusEx` (`ullAvailPageFile`)
via ctypes — ~10 lines. Deliberately left out of the attempt; it is the documented upgrade path.

### 5.7 The bill is per process and in memory
Only the app's own swaps are recorded, and a restart clears them. That is fine for "what is this session
paying", and it matches how the governor itself works (its `_rung`/`_eff` state is in memory too).

## 6. Contract and docs touch points

- `docs/API_CONTRACT.md` §5.1 (≈ line 275) describes `GET /api/vram`; the example JSON (≈ line 161) and
  the `<!-- shape:vram -->` block (≈ line 431) list its keys.
- **Trap:** `tests/test_lifecycle_contract.py` only asserts that every *documented* key exists with the
  right type — extra response keys pass silently. Adding `swap_cost` to the response without updating the
  shape block leaves the doc stale without failing anything.
- `models_ladder.yaml`'s own header (line 1) points at `docs/arch/gpu-vram-governor.md` as the
  governor's document - verified this turn. The two new config keys belong there as well as in the
  YAML's comment block.
- `docs/RAM_GROWTH_FINDINGS_2026-10-13.md` §5.3 records why this task exists; update its "Implemented"
  note to match whatever lands.

## 7. Verification plan (in order)

1. **Unit, hermetic** — the existing `tests/test_vram_swap_cost.py` shows the pattern (monkeypatch
   `gpu`/`ps`/`need_of`/`_headroom_mb`; the real module is under test). Same style as
   `tests/test_skill_creator_vram_gate.py`. Must include: the bill for a load, a round trip inside the
   window, no round trip outside it, a refusal that touches Ollama **zero** times, and the same switch
   running when there is room.
2. **Live measurement** — against the real Ollama (a 3 GB model fits next to the resident planner):
   ```python
   from ishti import vram
   ok, e = vram.load("gemma3:4b", "10m"); vram.unload("gemma3:4b"); ok, e = vram.load("gemma3:4b", "10m")
   print(vram.swap_cost())      # expect: two +/- pairs of similar size and one round_trip
   ```
3. **Through the user's interface** — boot `ISHTI_TEST_MODE=1 venv\Scripts\python -m ishti 8889`, then
   `curl http://127.0.0.1:8889/api/vram` and check `swap_cost` bills the boot's own swaps (measured
   earlier: planner load +869 MB host / +10,160 MB VRAM, evictions −731/−943 MB). Shut down with
   `POST /api/shutdown` (202) and confirm ports are free.
4. **Regression** — `venv\Scripts\python -m pytest tests/test_vram_swap_cost.py tests/test_lint_gate.py -q`
   and, since it boots a real backend and validates the doc against the code:
   `venv\Scripts\python -m pytest tests/test_lifecycle_contract.py -m e2e -q` (needs Ollama + GPU).
5. **Real-hardware acceptance** (not pytest, needs the GPU, ISHTI stopped):
   `venv\Scripts\python tests\e2e_vram_lifecycle.py t1a t1b` — the downshift/upshift choreography this
   change touches. Run it before and after the change and keep both outputs.

## 8. Deliberately not in scope

- Pricing the *benefit* side (model quality / tok-s) so the rule becomes a ratio rather than a floor.
- Commit-headroom as a second signal (§5.6).
- Persisting the bill (§5.2, §5.5 — extend `data/vram.db` if ever needed).
- Any UI beyond `/api/vram`; nothing in `interface/app` reads these keys today.
