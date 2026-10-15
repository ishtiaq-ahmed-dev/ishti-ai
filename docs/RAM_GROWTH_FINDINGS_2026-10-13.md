# RAM growth under load — what was measured, what it is, what to do

Investigation of the audit finding [ISHTI_AUDIT_2026-10-13.md §4.4](../audit/ISHTI_AUDIT_2026-10-13.md)
("RAM climbs ~3.5 GB per 8 minutes of sustained load") and of the owner's report that RAM grows while
the app is used. Every number below was measured on this PC on 2026-10-13 with the instruments listed
in §6. Nothing here is inferred from reading code unless it says so.

## 1. Verdict

1. **The 3.5 GB / 8 min growth does not reproduce on today's tree.** The same 8-minute level-4 soak,
   scored with the project's own analyser (`tools/soak_analysis.py`), drifts **−652 MB** today against
   **+3,454 / +2,873 / +3,576 MB** on 2026-09-30.
2. **Nothing in ISHTI's own processes grew** in any run: the largest ISHTI-side surface (the real
   server under HTTP) moved 293 → 340 MB over 587 turns and 332 → 344 MB over 158 model turns.
3. **The historical growth is attributed by the project's own record** to unbounded retention of
   background-task results in `ishti/taskmgr.py` (FIX-7). That retention is now bounded and its
   regression test passes. §3 lists what this means for the earlier claim, including a caveat.
4. **What still moves RAM during real use is a one-time model (re)load step, not accumulation.** When
   Ollama pulled the 14B planner back in, the runner's host RSS went 0.97 GB → 8.7 GB and system "used"
   jumped +8.2 GB once; over the following 5 minutes of continuous model load it then *fell* 469 MB.
5. The remaining ISHTI-side unbounded structures found are listed in §4 — small, but real, and each is
   a one-line fix with a test.

## 2. Measurements

### 2.1 Same metric, same workload, same machine — 13 days apart

`tools/soak_analysis.py` splits the window into first-quarter and last-quarter averages. Higher is better
evidence: same code, same tag shape, same analyser.

| Run (all level 4, 480 s window) | RAM drift (first→last quarter) | VRAM drift |
|---|---:|---:|
| `soak_8min` — 2026-09-30 | **+3,454 MB** | −22 MB |
| `soak2_8min` — 2026-09-30 | **+2,873 MB** | +845 MB |
| `soak3_postfix` — 2026-09-30 | **+3,576 MB** | +804 MB |
| `attrib4` — 2026-10-13 (today) | **−652 MB** | +464 MB |

Today's run: `metrics/ramp_level_attrib4.jsonl` (264 samples / 452 s), 457 turns, 0 errors,
latency avg 2.22 s, max 22.3 s; record in `metrics/soak_analysis_attrib4.json`.

### 2.2 Per-process attribution — the thing the historical soaks never had

`tools/monitor.py` records only *counts* of processes and system RAM; it cannot tell a leak from an
unrelated app. `tools/ram_attrib.py` was added to sample every watched process's RSS **and** commit
(private bytes) plus the kernel pool, so "who grew" is answered directly.

Level 4, 480 s (today): system used 44,485 → 45,748 MB (+1,263 MB), peak 46,209 MB, quarter drift
−201 MB. The grower is **the load harness itself** (`python.exe`, +1,270 MB of commit = imports +
`tracemalloc`, flat after ~60 s), plus two `python.exe` processes that are not the soak, and **no ISHTI
app or server process** (all ±30 MB). Those two are named in §2.2a rather than dropped: a sampler that
watches every `python.exe` on a shared machine always picks up unrelated work.

### 2.2a What the other growing processes in that window were

| pid | window | growth | identified as |
|---|---|---|---|
| 18284 | 19:51–19:59 | rss +530, commit +1,267 MB | the level-4 soak harness (this report's tool) |
| 37220 | 19:51:28–19:54:05, 29→66 threads | rss +658, commit +1,029 MB | the earlier 20-second **smoke** run of the same harness — same launch time and profile — which over-ran its own window and was still finishing background work before it exited (§4.5) |
| 46292 | 19:57:38–20:00:58, 29→171 threads | rss +1,878, commit +4,748 MB | **not identified**: not the soak's pid, no ISHTI process, and gone before the next sample period. On this checkout other work runs concurrently (the audit recorded another agent creating `ishti/business/` mid-audit), so this is most likely an outside job — but it is an inference, not a measurement |
| 31356, 30352 | 19:58:10, 19:59:16 | 0.9–1.1 GB each | **new `llama-server.exe` runners**: Ollama swapped runners *during* the soak, the behaviour behind §2.5 |

So the honest reading of the level-4 window is: the soak's own bookkeeping grew, the model runtime
churned runners, and no ISHTI request-handling process grew at all.

Level 10 (10 workers, partial run — the harness over-ran its window and was stopped at 737 s):
monitor samples 190 / 358 s, RAM 44,295 → 43,965 MB, quarter drift **−111 MB**; kernel non-paged pool
1,650 → 1,629 MB (flat); standby/cache +581 MB (reclaimable); only grower again the harness
(+1,315 MB commit). The 10-worker run was using a ~6.2 GB VRAM residency at the time (the ladder had
swapped off the pinned 14B), which is itself worth watching — see §5.3.

### 2.3 The app path (the thing the owner actually uses)

`python -m ishti 8888` booted for real; requests posted to `/api/chat` from a second process.

| App-path run | turns / errors | latency avg | app process RSS | system used |
|---|---:|---:|---|---|
| Standard QA workload, 4 workers, 356 s | 587 / 0 | 0.46 s | 293 → 340 MB (+47) | +1,208 MB (other apps) |
| Model-forcing prompts, 4 workers, 196 s | 149 / 0 | 3.44 s | 332 → 342 MB (+10) | **+8,172 MB once** (§2.5) |
| Model-forcing prompts, 4 workers, 301 s (attributed) | 158 / 0 | 6.69 s | 342 → 344 MB (+2) | **−469 MB** |

In the attributed run: llama-server.exe 8,710 → 8,922 MB (oscillating, not climbing), kernel/cache flat,
VRAM 11,975 → 11,400 MB, `ishti-qwen2.5:14b` resident at 9,922 MB VRAM + nomic-embed 308 MB.

**The app's own memory is flat under both mixes.** A real turn costs ~1 KB of process memory, so the
"I leave it open and it grows" perception is not ISHTI's Python process on this path.

### 2.4 Model load/unload cycles (does the ladder leak?)

`gemma3:4b` loaded and unloaded 3 times while sampling RAM, commit, kernel pool and VRAM:

| Sample | system used | commit | non-paged | VRAM |
|---|---:|---:|---:|---:|
| baseline | 44,834 MB | 90,685 MB | 1,714 MB | 11,400 MB |
| after load | 45,752 MB | 95,840 MB | 1,697 MB | 15,257 MB |
| after unload | 44,855 MB | 90,656 MB | 1,695 MB | 11,400 MB |

A load costs ~0.9 GB of host RSS and ~5.1 GB of commit for a 3.1 GB model, and **VRAM and commit return
to baseline on unload every cycle** (after-unload readings 44,855 / 45,387 / 45,555 MB — a +721 MB
spread across three cycles, inside this machine's background noise, where an unrelated app's commit
alone moves by 12 GB). No clean accumulation.

### 2.5 The one-time step — the mechanism an owner actually sees

During the second app run, system "used" jumped +8.2 GB in 196 s while the app stayed at 342 MB:

- `llama-server.exe` reappeared as a **new** process (pid 44300 RSS 970 MB / commit 11,339 MB →
  pid 34624 RSS 8,710 MB / commit 19,316 MB). 8.7 GB ≈ the 8.4 GB of 14B weights.
- Standby/system cache fell 19.6 GB → 12.3 GB in the same interval: the weight pages moved from the
  reclaimable cache into the runner's working set.
- It did not continue: the next 5 minutes of the same load ended **469 MB lower** (§2.3).

So a model (re)load re-occupies about the model's file size in host RAM until Windows trims it. With a
ladder that swaps models, each swap re-creates that step — which is exactly the shape of a "RAM grows
when I use it" report, and it is the one thing in this investigation that a user can be shown.

### 2.6 The QA workload is mostly not the model path

Probed through the live app, today's `tools/load_ramp.py` WORKLOAD routes as:

| message | latency | route | model call |
|---|---:|---|---|
| weather | 1.31 s | shortlist → weather | no (network) |
| reminder | 0.03 s | shortlist → reminders | no |
| chat "PONG" | 0.04 s | rule → whatsapp (test-mode dry run) | no |
| file search | 2.21 s | shortlist → files | no |
| memory recall / notes / folders / bayes / system RAM / chat digest | 0.03–0.13 s | rule → skill | no |

Eight of ten messages never reach the brain. Any soak built on this list is a **skill and bookkeeping**
soak, not an inference soak — which matters for §3 (the growth it produced came from bookkeeping), and
for anyone who reads its latency numbers as model latency.

## 3. Attributed cause of the historical 3.45 GB

`ishti/taskmgr.py` kept the **full return value of every completed background task** on its record,
with up to `MAX_TASKS = 60` done records alive, and `list_tasks()` serialises every record for
`/api/tasks`. The file's own comment records the project's measurement and the fix:

```
# FIX-7 (QA prompt 3 soak): done records live up to MAX_TASKS (60) and
# list_tasks() serializes EVERY record for /api/tasks, so a full result on the
# record pins hundreds of MB (measured: 12 x 200 KB dict results -> RSS
# 18 -> 2,860 MB; 473-turn soak drifted +3.45 GB). Store a bounded preview.
```
— [ishti/taskmgr.py:90](../ishti/taskmgr.py#L90)

Today the code stores a 4,000-character preview, drops `_fn` (so callable-captured data is released),
and trims to `MAX_TASKS`; [tests/test_taskmgr_result_retention.py](../tests/test_taskmgr_result_retention.py)
asserts all of it (4 tests, passing — run in §7). Corroboration: the workload messages that produce big
results (chat digest, "give me a report", research) are exactly the ones that spawn background tasks,
and the same workload on today's tree produces no growth.

**Caveat, stated plainly:** that comment's arithmetic does not support its own magnitude — 12 × 200 KB is
2.4 MB, not 2,860 MB — so the *mechanism* is recorded by the project but the *magnitude* has never been
reproduced. Anyone re-verifying this should either reproduce it (revert the bound in a scratch copy and
run §6.1) or downgrade the claim in [ISHTI_AUDIT_2026-10-13.md](../audit/ISHTI_AUDIT_2026-10-13.md) §4.4.
The rest of §4.4 is now answered empirically: no growth, on either the dispatcher or the server path,
with per-process attribution.

Related retention fixes from the same day that are in the tree and tested
([tests/test_audit_fixes.py](../tests/test_audit_fixes.py)): per-thread SQLite connections are reaped
when their thread dies ([ishti/core/sqlite_cache.py:18](../ishti/core/sqlite_cache.py#L18)), the
stdout capture buffer is capped at 16 KB, child log rotation is bounded, and the health probe no longer
issues a metasearch.

## 4. Still unbounded — for the fixing agent

Found by inspection; none of them grew in today's runs, all are small, each is a small fix.

1. **`SESSIONS` is never evicted** — [ishti/dispatcher.py:141](../ishti/dispatcher.py#L141) writes
   `SESSIONS[sid]`; nothing pops (only get/set/values exist). Per session the state is bounded
   (`RUNTIME_MESSAGES`), but the number of sessions is not: any client that rotates `session_id`
   (scripts, tunnel clients, the QA harness) grows it forever. Fix: LRU cap or drop sessions idle > 24 h.
2. **Expired vault approvals are never swept** —
   [interface/web_ui/server.py:2149](../interface/web_ui/server.py#L2149) stores `_VAULT_APPROVALS[token]`
   with a 120 s expiry, but an entry is only removed when it is *used* (line 2154). Fix: purge expired
   entries on write.
3. **Disconnected WebSockets linger until the next broadcast** —
   [interface/web_ui/server.py:285](../interface/web_ui/server.py#L285) appends to `active_websockets`;
   removal happens only when a send fails (lines 359-360). Fix: discard in the handler's `finally`.
4. **QA harness records overwrite each other** — `tools/load_ramp.py` writes
   `metrics/ramp_summary_level_<n>.json`, keyed by level only, so today's level-4 run silently
   overwrote whatever the last level-4 run recorded (disclosed in §8). Include the tag in the filename.
5. **A finished soak does not exit** — the workload's threads end at the window, but `taskmgr`'s
   non-daemon `ThreadPoolExecutor` keeps the process alive. Measured today: the 20-second smoke run
   stayed resident **2.5 minutes** past its window (pid 37220, §2.2a), the level-4 run **2.8 minutes**,
   and the level-10 run never exited at all (stopped by hand), so a *finished* soak looks exactly like a
   hung one. Also fixed today: the monitor's JSONL writes were buffered, so a part-way file looks
   complete — `f.flush()` added.

## 5. Recommendations, ranked

1. **Make the absence of growth an enforced, measurable gate** — the instruments now exist. Suggested
   gate on the standard soak: fail if any ISHTI process's RSS slope exceeds ~20 MB/min, or if the
   system first-quarter→last-quarter drift exceeds ~500 MB over an 8-minute window
   (`tools/ram_attrib.py` already emits both).
2. **Re-verify FIX-7 causally** — one `git worktree` on a soak-era revision
   (`git log --before=2026-09-30`), the same 8-minute soak, then the same run on today's tree. That
   converts §3 from "recorded by the project" to "reproduced both ways". Use an isolated data dir
   (`ISHTI_SCRATCH_DIR`) so the worktree never writes this machine's real stores.
3. **Treat model (re)loads as the user-visible RAM event (§2.5)** — keep the planner resident, avoid
   unnecessary ladder swaps, and report available RAM rather than "used"; a swap costs roughly the
   model's file size in host RAM until Windows trims it. *Implemented 2026-10-13:* `ishti/vram.py`
   now bills every load/unload with its measured host-RAM delta, reports round-trip waste under
   `swap_cost` in `GET /api/vram`, and refuses an upshift the machine cannot afford
   (`swap_free_floor_mb`, default 2048 MB, `0` disables) — measured live at boot: planner load +869 MB
   host / +10,160 MB VRAM, evictions −731 and −943 MB. See `tests/test_vram_swap_cost.py`.
4. **Give the QA workload a model-path mix.** Today's list is 8/10 rule-fast skills (§2.6); any
   inference, concurrency or VRAM conclusion drawn from it is drawn from the wrong workload.
5. **Bound the four structures in §4** and add one regression test each.
6. **Do not use system-wide "used RAM" as leak evidence on this machine.** Before any ISHTI load today,
   46.6 GB of 68 GB was already in use: an unrelated app (`D:\SCREENER V2`, 11 python workers ≈ 15 GB)
   and a 10.4 GB Windows compression store. System-wide numbers moved by GBs with ISHTI shut down.

## 6. Instruments added (and how to re-run)

| Tool | What it answers | Command |
|---|---|---|
| [tools/ram_attrib.py](../tools/ram_attrib.py) | who grows: per-process RSS **and** commit, thread counts, kernel pool, plus `tracemalloc` top growth sites inside the load process | `venv\Scripts\python tools/ram_attrib.py 4 480 mytag` |
| [tools/sysmem_probe.py](../tools/sysmem_probe.py) | kernel paged/non-paged pool, standby cache, handles/threads, commit — the memory no per-process sampler can see | `venv\Scripts\python tools/sysmem_probe.py mytag 5` |
| `tools/soak_analysis.py` (existing) | first-quarter vs last-quarter drift, the project's historical metric | `venv\Scripts\python tools/soak_analysis.py mytag` |

Artifacts from today: `metrics/ram_attrib_attrib4.jsonl` + `_summary.json`, `metrics/ram_attrib_attrib10.jsonl`,
`metrics/ramp_level_attrib4.jsonl`, `metrics/soak_analysis_attrib4.json`, `metrics/sysmem_attrib10_tail.jsonl`,
`metrics/ramp_summary_level_10.json` (restored, see §8).

## 7. Checks run today

- `pytest tests/test_taskmgr_result_retention.py tests/test_audit_fixes.py tests/test_memory_probe.py tests/test_lint_gate.py -q`
  → **25 passed, exit 0** (the retention bounds, the retention regression tests, the probe and the lint gate).
- Both soak runs and both app runs reported **0 errors**; the app's `/api/health` reached `state=ready`
  before load, in a real `python -m ishti 8888` process.

## 8. Limitations — what this evidence does not cover

- **No historical revision was re-run.** The 2026-09-30 numbers stand as recorded; §3's attribution is
  the project's own comment plus the absence of any other growth today. A worktree bisect (§5, item 2)
  settles it.
- **Level 10 did not finish cleanly** (stopped at 737 s); its per-process evidence is complete but its
  turn/error summary never printed.
- **Two pre-existing summary records were overwritten** by today's runs, because `tools/load_ramp.py`
  keys that filename by level only (§4.4): `metrics/ramp_summary_level_1.json` (a 12-second smoke run
  replaced the 28-turn level-1 record) and `metrics/ramp_summary_level_4.json` (the 8-minute soak
  replaced the 146-turn level-4 record). The overwritten values survive in the derived
  `metrics/ramp_table.csv` (level 1: 28 turns / 2.28 s avg; level 4: 146 turns / 2.17 s avg), and the
  historical *drift* figures are untouched — they live in `metrics/soak_analysis_soak*.json`. The
  level-10 record was copied aside as a precaution and is byte-identical to the copy: the stopped run
  never reached its own summary.
- **Unmeasured paths:** voice, browser/computer-use skills, the desktop window (pywebview), the
  React front-end's own memory, and multi-hour sessions. Extrapolating 8 minutes to hours is not evidence;
  the 3.45 GB/8 min figure was itself an extrapolation.
- **Idle behaviour is not a clean control here** — this machine's background load (the unrelated app, the
  compression store, and several concurrent agents) moved system RAM more than ISHTI did. §2.2a is the
  case in point: a single outside job grew 1.9 GB inside a 3-minute window of my measurement. The
  instruments separate this per process; a future soak should still record the machine's other tenants
  or run on a quiet PC before anyone reads a system-wide number as a verdict.
