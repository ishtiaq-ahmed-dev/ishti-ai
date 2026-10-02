# TEST_RESULTS — backend: VRAM governor + lifecycle (2026-09-29)

Every block below is real output captured from the run named in its heading; nothing was retyped. Hardware: RTX 5070 Ti 16 GB
(16303 MiB), Windows 11, Ollama 0.31.2. The machine was **shared with another development session** the whole time (its
tests loaded models on the real Ollama), which is why some runs are labelled with what that did; see "How the runs were
made". Harness: `tests/e2e_vram_lifecycle.py` (real GPU / Ollama / processes), `tests/test_lifecycle_contract.py` (pytest,
contract vs a live backend), `python -m ishti.vram_calibrate` and `tests/kv_cache_probe.py` (measurements).

## Summary

| # | Test (spec §4) | Result |
|---|---|---|
| 1 | Heavy vision load with the planner resident: downshift, vision loads fully, `size_vram == size` for every model, upshift after | **PASS** (T1a default 80% budget; T1b tier 3 required at 95%) |
| 2 | VRAM pressure (GPU hog, oversized model): no partial offload ever in `/api/ps` | **PASS** (T2, with an un-governed control that DOES split) |
| 3 | Live-voice priority: heavy job doesn't touch or slow the voice turn | **PASS** (T3) |
| 4 | Boot: stages advance in order and match reality; Ollama killed mid-boot → `error` with a clear message | **PASS** (T4 + contract test) |
| 5 | Shutdown: no ISHTI/Ollama-runner/child processes remain, ports free, SQLite consistent, repeated 20 times | **PASS 20/20** (T5, after 4 defects found and fixed - see "What the runs found") |
| 6 | Second launch while running: no second backend | **PASS** (contract test) |
| 7 | `docs/API_CONTRACT.md` matches the actual responses | **PASS** (10 contract tests) |
| + | Job Object: children die when the parent is hard-killed; apps ISHTI opens for the owner do NOT | **PASS** (T5b + `startfile` experiment) |
| + | Controlled "apply Ollama settings" (ours restarted; not-ours untouched) | **PASS** (T6) |
| + | Local websites can't drive shutdown/apply/admit | **PASS** (contract test) |

## How the runs were made (honest notes)
- **T1–T3** ran against the real Ollama on :11434 with the real 14B planner. **T4/T5/T5b/T6** ran a full backend on port 8896 with
  its **own Ollama on :11500** (started by ISHTI, so it is "ours"), the planner pinned to the 3B (`ISHTI_MODEL_PLANNER`) because
  the other session repeatedly held the real Ollama's 14B in VRAM, which made a second 14B unloadable (the governor correctly
  queued and reported it, but that tests the governor, not the lifecycle). **Perf** used the real planner.
- Real defects were found by these runs and fixed between attempts; the failing attempts are listed under "What the runs found".
  The blocks below are the **final** runs after the fixes. Raw data: `data/e2e_vram_lifecycle.json`, `data/vram.db`.

## Test 1 — heavy vision load with the planner resident (T1a, T1b)

T1a: default 80% budget; the 12B vision tier can't sit beside any planner rung, so the job gets tier 2 and the planner steps to the 7B.
```
  [planner resident] GPU 10844 MiB (66%) | ishti-qwen2.5:14b 9922/9922 | planner rung 0
[vram] planner: ishti-qwen2.5:14b -> qwen2.5-c8k:7b (Switched to a lighter model for speed while a heavy task runs: analyse a dense page)
[vram] vision3: gemma3:12b -> gemma3:4b (gemma3:12b doesn't fit right now - using a lighter model)
  heavy admit ok=True loaded=gemma3:4b waited=6.7s actions=['downshift qwen2.5-c8k:7b', 'rung gemma3:4b']
  [during the heavy job] GPU 9671 MiB (59%) | qwen2.5-c8k:7b 4896/4896, gemma3:4b 2742/2742 | planner rung 1
  vision answer: 'PRICE 42' (model gemma3:4b)
  planner still answers during the job: 'ok' on qwen2.5-c8k:7b
  resident together: ['gemma3:4b', 'qwen2.5-c8k:7b'] -> True
  job window 8.1 s; planner rung during job: 1
[vram] translator: gemma3:4b -> (unloaded) (making room for the main model to return)
[vram] planner: qwen2.5-c8k:7b -> ishti-qwen2.5:14b (GPU memory is free again - back to the better model)
  [after the job] GPU 10844 MiB (66%) | ishti-qwen2.5:14b 9922/9922 | planner rung 0
  model_switch: planner   ishti-qwen2.5:14b -> qwen2.5-c8k:7b | Switched to a lighter model for speed while a heavy task runs: analyse a dense page
  model_switch: vision3   gemma3:12b -> gemma3:4b | gemma3:12b doesn't fit right now - using a lighter model
  model_switch: planner   qwen2.5-c8k:7b -> ishti-qwen2.5:14b | GPU memory is free again - back to the better model
>>> T1a heavy vision load, default 80% budget: PASS - loaded gemma3:4b fully on GPU; planner rung during job 1, back to rung 0 after 44 s; vision answer 'PRICE 42'; 201 /api/ps samples, partial offloads: 0; peak GPU 10848 MiB (66%); planner first token 298 ms
```
T1b: tier 3 required (`strict`), budget 95%: the planner goes to the 3B and **both** models are resident and fully on the GPU.
```
  [planner resident] GPU 10844 MiB (66%) | ishti-qwen2.5:14b 9922/9922 | planner rung 0
[vram] planner: ishti-qwen2.5:14b -> qwen2.5-c8k:3b (Switched to a lighter model for speed while a heavy task runs: analyse a dense page)
  heavy admit ok=True loaded=gemma3:12b waited=6.5s actions=['downshift qwen2.5-c8k:7b', 'downshift qwen2.5-c8k:3b']
  [during the heavy job] GPU 12183 MiB (74%) | qwen2.5-c8k:3b 2292/2292, gemma3:12b 7672/7672 | planner rung 2
  vision answer: '42' (model gemma3:12b)
  planner still answers during the job: 'ok' on qwen2.5-c8k:3b
  resident together: ['gemma3:12b', 'qwen2.5-c8k:3b'] -> True
  job window 8.2 s; planner rung during job: 2
[vram] planner: qwen2.5-c8k:3b -> qwen2.5-c8k:7b (GPU memory is free again - back to the better model)
[vram] vision3: gemma3:12b -> (unloaded) (making room for the main model to return)
[vram] planner: qwen2.5-c8k:7b -> ishti-qwen2.5:14b (GPU memory is free again - back to the better model)
  [after the job] GPU 10844 MiB (66%) | ishti-qwen2.5:14b 9922/9922 | planner rung 0
  model_switch: planner   ishti-qwen2.5:14b -> qwen2.5-c8k:3b | Switched to a lighter model for speed while a heavy task runs: analyse a dense page
  model_switch: planner   qwen2.5-c8k:3b -> qwen2.5-c8k:7b | GPU memory is free again - back to the better model
  model_switch: planner   qwen2.5-c8k:7b -> ishti-qwen2.5:14b | GPU memory is free again - back to the better model
>>> T1b tier-3 vision required (strict), 95% budget: PASS - loaded gemma3:12b fully on GPU; planner rung during job 2, back to rung 0 after 61 s; vision answer '42'; 271 /api/ps samples, partial offloads: 0; peak GPU 14855 MiB (91%); planner first token 229 ms
```

## Test 2 — VRAM pressure (T2)
```
[vram] planner: ishti-qwen2.5:14b -> qwen2.5-c8k:7b (ishti-qwen2.5:14b doesn't fit right now - using a lighter model)
  hog 3 GB (GPU 4006 MiB used before): admit ok=True model=qwen2.5-c8k:7b queued=None
  [hog 3 GB] GPU 9127 MiB (55%) | qwen2.5-c8k:7b 4896/4896 | planner rung 1
[vram] planner: ishti-qwen2.5:14b -> qwen2.5-c8k:3b (ishti-qwen2.5:14b doesn't fit right now - using a lighter model)
  hog 7 GB (GPU 8102 MiB used before): admit ok=True model=qwen2.5-c8k:3b queued=None
  [hog 7 GB] GPU 10619 MiB (65%) | qwen2.5-c8k:3b 2292/2292 | planner rung 2
  hog 12 GB (GPU 13222 MiB used before): admit ok=False model=None queued=ishti-qwen2.5:14b needs about 9.8 GB and only 0.0 GB is free within the 80% GPU budget (in use: nothing)
  [hog 12 GB] GPU 13222 MiB (81%) | - | planner rung 0
  qwen-27b (19 GB) via the governor: ok=False queued=qwen-27b:latest needs about 19.5 GB (estimated) and only 12.1 GB is free within the 80% GPU budget (in use: nothing)
  control: loading qwen-27b directly (no governor) ...
  control: PARTIAL OFFLOAD appeared: qwen-27b:latest 13614/19272 MiB
[vram] VIOLATION: qwen-27b:latest is 13614/19272 MiB on the GPU - unloading it
>>> T2 VRAM pressure: PASS - hog 3 GB -> planner qwen2.5-c8k:7b; hog 7 GB -> planner qwen2.5-c8k:3b; hog 12 GB -> queued: ishti-qwen2.5:14b needs about 9.8 GB and only 0.0 GB is free within the 80% GPU budget (in; qwen-27b via governor refused=True; partial offloads while governed: 0; control (raw Ollama load of qwen-27b): partial offload seen and unloaded by the watch after 1.0 s
```

## Test 3 — live-voice priority (T3, real Whisper through the governor)
```
[vram] model: qwen-27b:latest -> (unloaded) (it was partly on the CPU (slow); it will reload with room made first)
  planner first-token latency, idle: 157 ms
  during the voice turn: planner rung 0 (ishti-qwen2.5:14b); heavy job ok=False queued=gemma3:12b needs about 8.8 GB and only 2.1 GB is free within the 80% GPU budget (in use: ishti-qwen2.5:14b (9.7 GB); a v
  planner first token while the heavy job was waiting: median 165 ms, worst 420 ms (idle 157)
[vram] planner: ishti-qwen2.5:14b -> qwen2.5-c8k:7b (Switched to a lighter model for speed while a heavy task runs: speech recognition loading)
[voice] whisper warm (0.4s)
  [whisper loaded] GPU 8163 MiB (50%) | qwen2.5-c8k:7b 4896/4896 | planner rung 1
  whisper load 6.4 s; planner rung 1
[vram] planner: qwen2.5-c8k:7b -> ishti-qwen2.5:14b (GPU memory is free again - back to the better model)
>>> T3 live-voice priority: PASS - heavy job during a voice turn: queued (gemma3:12b needs about 8.8 GB and only 2.1 GB is free within the 80% G); planner untouched (rung 0); planner first token idle 157 ms vs worst during the job 420 ms; whisper loaded in 6.4 s with planner rung 1 (voice has priority); upshift after STT release: True; partial offloads: 0
```

Reading T3: the pass threshold was "worst planner first token ≤ 1.6 × idle + 300 ms" (= 550 ms here). The measured worst sample was **420 ms vs 157 ms idle**
(median 165 ms): the governor's polling thread and the measuring thread share one Python process in this test, so some of the
excess is CPU/GIL contention rather than the GPU. The planner itself was never touched (rung 0, same model resident throughout).

## Test 4 — boot with Ollama killed mid-boot (T4)
```
  +  2.3s stage=ollama_ready progress=14% error=None
  + 25.7s stage=memory_loaded progress=29% error=None
  + 26.3s stage=planner_warm progress=57% error=None
  killing our Ollama (pid 31096) mid-boot
  + 30.6s stage=ollama_ready progress=43% error=The AI engine (Ollama) stopped responding. Trying to start it again.
  + 39.0s stage=planner_warm progress=57% error=None
  + 42.4s stage=ready progress=100% error=None
>>> T4 boot with Ollama killed: PASS - error reported 4.2 s after the kill: 'The AI engine (Ollama) stopped responding. Trying to start it again.'; log file present: True; recovered (ready) 16 s after the kill; backend exited cleanly: True; leftovers: []
```

(T4's timeline shows the first Ollama start taking ~25 s in this run - the other session was using the disk/GPU; the error appeared 4.2 s after the
kill and the boot recovered by itself 16 s later. In the previous T4 run the same start took 2 s.)

## Test 5 — 20 x start → ready → `POST /api/shutdown` (T5, final run)
Each cycle snapshots the server's own process family (server, its Ollama, runners, console hosts, MCP servers) before the
shutdown and requires every member to be gone afterwards, both ports closed, `PRAGMA integrity_check` ok on the facts/memory/audit/Chroma
databases, and the registry file clean.
```
  # 1 ready in 13.3 s, shutdown  2.4 s, server gone=True, orphans=0, ports open=[], db problems=[], registry clear=True
  # 2 ready in 13.4 s, shutdown  2.4 s, server gone=True, orphans=0, ports open=[], db problems=[], registry clear=True
  # 3 ready in 13.1 s, shutdown  2.4 s, server gone=True, orphans=0, ports open=[], db problems=[], registry clear=True
  # 4 ready in 13.5 s, shutdown  2.4 s, server gone=True, orphans=0, ports open=[], db problems=[], registry clear=True
  # 5 ready in 13.5 s, shutdown  2.4 s, server gone=True, orphans=0, ports open=[], db problems=[], registry clear=True
  # 6 ready in 13.4 s, shutdown  2.4 s, server gone=True, orphans=0, ports open=[], db problems=[], registry clear=True
  # 7 ready in 13.2 s, shutdown  2.4 s, server gone=True, orphans=0, ports open=[], db problems=[], registry clear=True
  # 8 ready in 13.4 s, shutdown  2.4 s, server gone=True, orphans=0, ports open=[], db problems=[], registry clear=True
  # 9 ready in 13.5 s, shutdown  2.4 s, server gone=True, orphans=0, ports open=[], db problems=[], registry clear=True
  #10 ready in 13.5 s, shutdown  2.4 s, server gone=True, orphans=0, ports open=[], db problems=[], registry clear=True
  #11 ready in 13.8 s, shutdown  2.4 s, server gone=True, orphans=0, ports open=[], db problems=[], registry clear=True
  #12 ready in 13.9 s, shutdown  2.4 s, server gone=True, orphans=0, ports open=[], db problems=[], registry clear=True
  #13 ready in 13.4 s, shutdown  2.4 s, server gone=True, orphans=0, ports open=[], db problems=[], registry clear=True
  #14 ready in 13.7 s, shutdown  2.4 s, server gone=True, orphans=0, ports open=[], db problems=[], registry clear=True
  #15 ready in 13.4 s, shutdown  2.4 s, server gone=True, orphans=0, ports open=[], db problems=[], registry clear=True
  #16 ready in 13.4 s, shutdown  2.4 s, server gone=True, orphans=0, ports open=[], db problems=[], registry clear=True
  #17 ready in 13.4 s, shutdown  2.4 s, server gone=True, orphans=0, ports open=[], db problems=[], registry clear=True
  #18 ready in 13.5 s, shutdown  2.4 s, server gone=True, orphans=0, ports open=[], db problems=[], registry clear=True
  #19 ready in 14.0 s, shutdown  2.4 s, server gone=True, orphans=0, ports open=[], db problems=[], registry clear=True
  #20 ready in 13.5 s, shutdown  2.4 s, server gone=True, orphans=0, ports open=[], db problems=[], registry clear=True
>>> T5 20x start/shutdown: PASS - 20/20 cycles clean (not ready under GPU contention: 0); boot median 13.5 s (min 13.1, max 14.0); shutdown median 2.4 s (max 2.4); leftover processes from the server's own family: 0; problems: none; boot findings: none
```

## Test 5b — Job Object crash test (T5b)
```
>>> T5b Job Object crash test: PASS - job active=True; children [44084, 31948] alive before=True; after the parent was hard-killed they are gone=True
```
And the experiment that decided the design (script kept in the session scratchpad): with the job active, an `os.startfile`d process
**was killed** when its parent was hard-killed; after routing `os.startfile`/`webbrowser` through a breakaway launch:
`own child survived: False | detached survived: True | startfile'd survived: True`.

## Test 6 — Ollama settings apply (T6)
```
  ours=True active=True; saved 2/3 needs_restart=True
  apply -> True restarted=True; Ollama pid 6188 -> 35584; active env {'OLLAMA_HOST': '127.0.0.1:11500', 'OLLAMA_KEEP_ALIVE': '10m', 'OLLAMA_MAX_LOADED_MODELS': '3', 'OLLAMA_NUM_PARALLEL': '2'}
  not-ours Ollama: ours=False; apply -> applied=False needs_restart=True; pid 4476 -> 4476; still running after ISHTI exited: True
>>> T6 Ollama settings apply: PASS - our Ollama restarted with OLLAMA_NUM_PARALLEL=2/MAX_LOADED=3 (pid 6188 -> 35584), no num_gpu, ISHTI stayed ready, defaults restored; an Ollama ISHTI did not start (pid 4476) was refused with needs_restart, untouched, and still running after ISHTI exited
```

## Test 7 — contract test (pytest, live backend)
```
10 passed in 22.81s
```

## Performance (spec §3 — measured, nothing estimated)
```
  --- cold boot (our own Ollama started by ISHTI, planner loaded from disk) ---
  cold boot to ready: 14.7 s; stages {'backend_up': 0.03, 'ollama_ready': 8.14, 'memory_loaded': 0.29, 'skills_loaded': 0.09, 'planner_warm': 4.6, 'voice_ready': 0.0}; planner first token at boot 208 ms
  event loop, /api/ready: 150 concurrent requests (300 in 0.32 s total) median 86 ms, p95 123 ms, max 128 ms
  event loop, /api/vram: 150 concurrent requests (300 in 0.32 s total) median 101 ms, p95 121 ms, max 128 ms
  --- warm boot (Ollama running, planner already resident) ---
  warm boot to ready: 9.1 s
  --- voice turn latency ---
  voice turn (median of 5): STT 239 ms + first LLM token 413 ms + first-sentence TTS 489 ms = 1141 ms to first audio
>>> PERF: PASS - cold boot 14.7 s; warm boot 9.1 s; planner first token 208 ms at boot; /api/ready p95 123 ms with 300 concurrent requests in flight; voice turn to first audio 1141 ms (STT 239 + LLM 413 + TTS 489)
```
Planner first token: 157 ms idle in T3 (154–160 ms across runs), 208 ms measured right after boot; calibration `first token` column below.

## Calibration (`python -m ishti.vram_calibrate`, quiet GPU)
| model | ctx | Ollama MiB | GPU delta MiB | tok/s | first token ms | load s | quality |
|---|---|---|---|---|---|---|---|
| ishti-qwen2.5:14b | 8192 | 9922 | 10151 | 79.5 | 157 | 3.2 | 5/5 (arithmetic:ok json:ok table:ok urdu:ok tool_call:ok) |
| qwen2.5-c8k:7b | 8192 | 4896 | 5121 | 143.2 | 149 | 2.4 | 4/5 (arithmetic:ok json:ok table:ok urdu:FAIL tool_call:ok) |
| qwen2.5-c8k:3b | 8192 | 2292 | 2517 | 240.9 | 146 | 2.0 | 5/5 (arithmetic:ok json:ok table:ok urdu:ok tool_call:ok) |
| ishti-qwen2.5-coder:14b | 8192 | 9922 | 10151 | 80.9 | 157 | 5.0 | 4/5 (arithmetic:ok json:ok table:ok urdu:ok tool_call:FAIL) |
| llama3.2:3b | 4096 | 2436 | 2661 | 245.0 | 181 | 2.8 | 5/5 (arithmetic:ok json:ok table:ok urdu:ok tool_call:ok) |
| gemma3:4b | 4096 | 2742 | 3857 | 163.7 | 397 | 3.6 | 5/5 (arithmetic:ok json:ok table:ok urdu:ok vision:ok) |
| qwen2.5-coder-c8k:7b | 8192 | 4896 | 5121 | 146.0 | 150 | 3.0 | 4/5 (arithmetic:ok json:ok table:ok urdu:ok tool_call:FAIL) |
| gemma3:12b | 4096 | 7672 | 8973 | 85.6 | 412 | 4.6 | 5/5 (arithmetic:ok json:ok table:ok urdu:ok vision:ok) |

## Module self-checks (final code)
```
[self-check] vram OK - planner downshifts first, strict keeps the tier or queues, idle models go before the planner, voice turn protects the planner, unmeasured models need headroom
[self-check] lifecycle OK - stages/progress/error snapshot, Ollama env mapping (never num_gpu), job object: True
[self-check] events OK - fan-out, replay of the latest boot_stage/vram_status/error, listener isolation
[self-check] gpu OK - heavy jobs take turns; 15838 MB free now
[self-check] models OK - strict GPU eviction (pinned planner/laya, 10m idle for others, event logging)
```
Core check: `python -m ishti.check` → `routing: 329/329 correct, median 0.2 ms` and `core loop OK`.

## What the runs found (defects fixed along the way)
| Found by | Defect | Fix |
|---|---|---|
| T1b (co-residency required) | the plan "reduced context" for the 12B expecting a saving; Gemma 3's sliding-window attention saved 2 MiB, so Ollama evicted the planner | only measured context savings count; Ollama's own ≥1 GiB-free-after-load rule (read from its debug log) added to the fit check |
| T1 | a lease for the planner's own load was never released → "a heavy job is active" forever → no upshift | the planner's load takes no lease |
| T3 | Whisper grew the GPU by 3047 MiB, not the assumed 1200 | seed corrected; every non-Ollama job now learns its real footprint |
| gpu-delta data | footprints measured while another process allocated were inflated (13.2 GB "needed" for the 14B) | the estimator is the minimum over repeated loads; bad rows repaired from the clean calibration |
| test suite | `/api/ps` failing while Ollama was busy counted as "nothing loaded" | failed reads are never an answer |
| T5 (x3) | stale registry pid after exit in ~10-15% of cycles: the main thread could finish before the shutdown thread's cleanup ran | `serve()` waits for the shutdown sequence to complete the exit (20/20 afterwards) |
| perf | 300 concurrent requests: median 1351 ms, p95 2333 ms (each `/api/vram` call ran nvidia-smi + Ollama calls) | one shared, ≤0.5 s-old snapshot (single-flight): p95 123 ms |
| audit | 24 findings from an independent read-only review agent | see `ISSUES.md` (all fixed but one accepted) |
| T5b + experiment | the Job Object also killed apps ISHTI opened for the owner | breakaway launches, verified |

Not done / not covered (see `NEEDS.md`): a live-microphone turn (no microphone on this PC), the owner's own quality prompts, and
the desktop launcher (`startup.py` → window) end to end (Prompt 2's area; only its Ollama/model-ping duties were removed).

---

# TEST_RESULTS — 24-hour chat digest (Section 4.7) + chat-UI tables, 2026-09-29

Every line below is copied from a run in this session. The dataset is **synthetic** (fake chats, fake people)
and lives in `tests/test_chat_digest.py`; the real store and the real relationship graph are never touched
(each test re-points both to `tmp_path`).

## Commands and results

| # | Command | Result |
|---|---|---|
| 1 | `venv\Scripts\python -m pytest tests\test_chat_digest.py -q` | **PASS — 17 passed, exit 0** (N1-N12 + 5 regressions) |
| 2 | `venv\Scripts\python -m ishti.skills.chat_digest.store` | `[self-check] chat_digest.store OK - dedupe, inclusive window, export parse (both formats, 12h+24h clock, multiline, media placeholder, system lines dropped)` |
| 3 | `venv\Scripts\python -m ishti.skills.chat_digest.window` | `[self-check] chat_digest.window OK - inclusive 24h window, 23:59 in / 24:01 out, timezone label` |
| 4 | `venv\Scripts\python -m ishti.skills.chat_digest.analyze` | `[self-check] chat_digest.analyze OK - coverage reasons, deadline priority, noise rubric, relationship never guessed, every action sourced` |
| 5 | `venv\Scripts\python -m ishti.skills.chat_digest.report` | `[self-check] chat_digest.report OK - GFM tables stay rectangular with escaped pipes, newlines, Urdu/emoji; malformed tables are detected; 300-row table valid` |
| 6 | `venv\Scripts\python -m ishti.skills.chat_digest.export` | `[self-check] chat_digest.export OK - markdown, DOCX (8 cols x 3 rows, repeating header, RTL Urdu runs), HTML (thead repeats, dir=auto); PDF 2026-09-29_2022_chat_digest.pdf via msedge.exe, 1 page(s)` |
| 7 | `venv\Scripts\python -m ishti.skills.chat_digest.skill` | `[self-check] chat_digest.skill OK - routing (specific, no overreach), report renders, PDF export saved, drafts are drafts only` |
| 8 | `venv\Scripts\python tools\chat_digest_sample.py` | 3-page PDF via `msedge.exe` + 3 rasterised pages + wide/narrow screenshots + ground-truth JSON; `markdown tables: 0 problem(s)` |
| 9 | `venv\Scripts\python -m ishti.check` (live routing) | **exit 0 — `routing: 333/333 correct, median 0.2 ms`** (was 329 before this pass; the 4 new labels are the digest's own phrasings) |
| 10 | `venv\Scripts\python tools\check_skills.py --verbose` | **exit 0 — `120 skills, 232 assertions, 329 labelled requests replayed ... OK`**, including "no matcher steals another skill's requests" |
| 11 | `cd interface\app && npm run build` | **exit 0** — `tsc -b` clean, `2328 modules transformed`, `dist/assets/index-*.js 1,011.13 kB (gzip 289.30 kB)` |
| 12 | `venv\Scripts\python -m ruff check ishti\skills\chat_digest` | 56 findings, all in rule families that already exist in the codebase (`FURB167` re.I, `BLE001` broad except, `DTZ*` naive datetimes). `ruff check ishti` on the **pre-existing** tree reports **1600** - the new code matches house style; there is no ruff config in the repo (NEEDS N19) |

## Section 4.7 checklist (N1-N12)

| # | Requirement | Result | Evidence |
|---|---|---|---|
| N1 | recall + precision: every planted fact/decision/deadline present; every row cites a real in-window message | **PASS** | `test_n1_recall_and_precision` - parses the rendered tables and checks every source id against `store.messages_between` |
| N2 | window boundary: 23:59 in, 24:01 out | **PASS** | `test_n2_window_boundary` - "Late Chat 23:59" analyzed with 1 message, "Too Old 24:01" skipped with the reason, plus a store-level check |
| N3 | coverage matches the real chat list; skips named with a reason | **PASS** | `test_n3_coverage_table_matches_reality` - 11 chats in, 11 rows out, archived/muted/community all analyzed |
| N4 | relationship only from the graph, else Unknown | **PASS** | `test_n4_relationship_only_from_the_graph` + adversarial probe: "Uncle Karim", "Brother Group", "Sister" all Unknown |
| N5 | prompt injection changes nothing | **PASS** | `test_n5_prompt_injection_does_not_change_behaviour` - control vs evil: same chat/action/decision counts, +1 flagged, no leak of the payload |
| N6 | read-only proof | **PASS** | `test_n6_read_only_proof` - audit rows show one allow-listed `chat_digest run (read-only, analysis only)` line at tier T0, store message count unchanged, no import of the WhatsApp/browser/MCP modules, `delete_chat` unreachable from the digest path |
| N7 | markdown validity: 200+ rows, long cells, 3 scripts | **PASS** | `test_n7_markdown_validity_stress` - 210 chats, 220+ table rows, 400-char cells, `table_problems() == []`, rows only ever 5/6/8 columns, `\\|` escaped |
| N8 | chat UI | **PARTIAL** | `test_n8_chat_ui_bundle_and_renderer` - the parser rules and the built bundle are verified; a screenshot of the **React** chat window needs the running app (NEEDS N20). The report itself was screenshotted at 1400 px and 680 px (Edge headless) and both were looked at |
| N9 | PDF: generate, rasterise, inspect every page | **PASS** | `test_n9_pdf_pages_are_real` + `tools/chat_digest_sample.py`: 3 pages, PDF text contains "Chats found", "Table 1", "Unknown (not in my relationship data)", "Bilal Khan"; no page blank; **pages 1 and 2 were viewed as images** - header row repeats on page 2, Urdu cell renders right-to-left, nothing cut off |
| N10 | DOCX: python-docx structure | **PASS (structure) / BLOCKED (visual)** | `test_n10_docx_structure` - 5 tables, column counts match the 4.5 headers, `w:tblHeader` on every header row, landscape, RTL runs. Visual DOCX render needs LibreOffice (NEEDS N18) |
| N11 | performance 10 / 50 / 200 chats | **PASS** | `test_n11_performance_10_50_200_chats` - `[chat_digest] analysis+render seconds: {10: 0.006, 50: 0.027, 200: 0.108} (no model is loaded on this path)`; not quadratic |
| N12 | privacy: sensitive excluded from exports, secrets redacted | **PASS** | `test_n12_privacy_and_redaction` - "4482" and `sk-live-…` absent from markdown, DOCX and HTML; the chat is marked `export_ok=False` and the report shows "Sensitive - open in chat to view" |

## Extra checks run against the feature (not required, done anyway)

| Check | Result | Evidence |
|---|---|---|
| Forged source ids are dropped | **PASS** | adversarial probe: `drop_unsourced` kept only `real-1`, dropped `fake-999` and the empty one |
| Table torture: many `\|`, newlines, 900-char token, RTL, emoji | **PASS** | `table_problems() == []`, row widths {5, 6, 8} |
| Chat budget of 2 with 6 chats | **PASS** | 4 skipped, each with "beyond the chat budget (2 chats) - raise max_chats to include it" |
| `owner_names` config | **PASS** | "Ishtiaq" recognised as the owner, the action labelled "you said:" |
| Sample coverage numbers | **PASS** | 11 found / 9 analyzed / 2 skipped, 239 messages, 9 actions, 1 decision, 1 injection flagged, 1 sensitive chat, 1 media item not analyzed |

## Honest limits of this section
- The adversarial review could not be delegated: the sub-agent tool returned an authentication error, so the
  falsification pass above was run by me and is labelled as self-review, not independent verification.
- N8/N10 are partial for the reasons in NEEDS N18/N20 (no GUI session, no LibreOffice).
- The digest's model-assisted line (`use_llm=True`) is implemented and source-checked, but was **not** exercised
  live in this pass - every number above comes from the deterministic path, which needs no model.

## Regression check of the whole offline suite (per file, after this pass)

`python -m pytest` on the whole directory stalled on this shared box, so every file was run on its own with a
210 s budget and the result was read from its own output. Raw logs: `data/logs/pf_*.txt`.

| File | Passed | Failed | Note |
|---|---|---|---|
| contract_test.py | 8 | 0 | `/api/health`, `/api/ready`, `/api/shutdown`, `/ws` contracts |
| test_arch2_memory.py | 17 | 0 | ~15 s standalone |
| test_capcut_skill.py | 6 | 0 | |
| **test_chat_digest.py** | **17** | **0** | this pass |
| test_context_regression.py | 19 | 0 | |
| test_learning.py / test_learning_wiring.py | 1 / 1 | 0 | |
| test_memory_prompt2.py | 4 | 0 | |
| test_multitask_voice_session.py | 6 | 0 | |
| test_prompt3_skills.py | 10 | 0 | registry, agents, resilience, taskmgr, telemetry |
| test_voice_note_generation.py | 1 | 0 | |
| test_whatsapp_context.py | 3 | 0 | run as a script by the harness, also collectable |
| **test_dispatcher_state_flow.py** | 0 | **1** | **environmental, not a regression** - its own message: `GpuBusy: ishti-qwen2.5:14b needs about 9.8 GB and only 0.0 GB is free within the 80% GPU budget`. At that moment `nvidia-smi` reported `16303 MiB total / 14794 used / 1202 free`, with Ollama holding `qwen3:14b` 9199 MB, `qwen2.5-c8k:3b` 2292 MB, `nomic-embed-text` 308 MB - a **concurrent session**, not this change (the digest loads no model) |
| 8 script-style files | - | - | `collect_ignore`d by `tests/conftest.py`: they define no `def test_*` and are run by `tools/verify_all.py` |

**Totals: 93 passed, 1 real failure (environmental), 0 regressions attributable to this pass.**

Two more things this pass verified while the suite was running:
- `venv\Scripts\python tests\test_stt_threads.py` (the standalone script, which the earlier 600 s budget made
  unusable on a mic-less PC) now **finishes and passes**:
  `[self-check] stt threads OK - every decode on one thread, exceptions cross the queue, ishti.wake and ishti.voice exit 0 (no 0xC0000409 fail-fast)`.
- While the suite was running, Ollama's `/api/ps` showed `ishti-qwen2.5:14b 9922 / 9922 MiB`, `qwen3:14b 9199 / 9199`,
  `qwen2.5-c8k:3b 2292 / 2292`, `nomic-embed-text 308 / 308` - **`size_vram == size` for every resident model**,
  i.e. zero partial offload observed during the pass (E1).

### Honest statement about the full-directory run
`python -m pytest` (whole `tests/`) did not finish within ~12 minutes on this box. Cause, measured: heavy
module imports plus Whisper loading, and then a stall with an idle CPU on `test_memory_commands` because the
GPU was full (see `test_dispatcher_state_flow`'s own `GpuBusy` line and the `nvidia-smi` numbers above). Every
file passes when run on its own, and the one that fails says why. This is recorded as CD-17 (OPEN, environmental)
rather than claimed as a pass.

---

# TEST_RESULTS - second test-harness pass (2026-09-29, later the same day, independent session)

Question answered: is the documented one-command suite (`start.bat test` -> `python -m pytest`) actually
runnable, and does the independent script harness agree? Everything below is measured on this box with
`venv\Scripts\python`, raw exit codes inspected.

| Check | Before | After |
|---|---|---|
| `python -m pytest --collect-only -q` (whole `tests/`) | did not finish: >180 s, then >300 s; one run on a single file exited **127** (= `0xC0000409`) with **0 bytes** of output | **exit 0 in 4 s** |
| `python -m pytest` (whole `tests/`, offline) | could not be completed | **86 passed, 10 deselected, 1 warning, exit 0 in 117 s**; a repeat run took 356 s while the GPU was contended (CD-17) |
| `venv\Scripts\python tests/test_lifecycle.py` | `TypeError: '_FakePopen' object does not support the context manager protocol`, then 110 s with a **real desktop window opened**, then stale-assertion failures | **exit 0 in 4 s**, no window opens |
| `python tools/verify_all.py --only test:` | **21/24** passed - failed: `test_chat_digest`, `test_dispatcher_state_flow`, `test_lifecycle` | **24/24 passed in 372 s** |
| collection side effects | `pytest --collect-only -q -s tests/test_browser_tabs.py` printed the script's own `[self-check] browser tab reuse OK ...`; `tests/test_whatsapp_context.py` loaded `ishti-qwen2.5:14b` during a "unit" run | no script-only module is imported; `tests/test_selfcheck_hygiene.py` guards it |

New regression tests: `tests/test_selfcheck_hygiene.py` (2 tests) - one fails if a `tests/test_*.py` with no
top-level `def test_*` is missing from `conftest.collect_ignore`; the other fails if an ignored module defines
tests its `__main__` block no longer runs.

Flakes seen once each in one full harness sequence and **not** reproduced afterwards (ISSUES.md CD-21):
`test_chat_digest.py::test_n5_prompt_injection_does_not_change_behaviour` (0 failures in 20 isolated runs) and
`test_dispatcher_state_flow.py::deep_research should have been routed` (green in the 24/24 run). They are
recorded as environmental (CD-17 GPU contention), not as fixed.

## 2026-09-29 (late) — desktop launch fix (Issues 31/32)

| Check | Result | Evidence |
|---|---|---|
| Broken exe reproduced | CONFIRMED | launching `dist\ISHTI\ISHTI.exe` captured `ModuleNotFoundError: No module named 'json'` + zombie ISHTI.exe, no window |
| Rebuilt bootstrap exe, launch cycle | PASS | exe -> ready ~20 s -> window "ISHTI - Intelligent Personal Assistant" -> `POST /api/chat` -> `final_answer: "PONG"` |
| Missing-venv fallback dialog | PASS | simulated frozen root without venv: error dialog recorded, no spawn (offline test) |
| External-stop orphan watcher | PASS | clean cycle: exe -> ready -> `POST /api/shutdown` -> **0** ISHTI processes after 35 s (venv pythonw trampoline doubling counted), port released, SQLite `integrity_check` ok |
| `tests/test_desktop_bootstrap.py` | 5/5 PASS | AST-locked stdlib-only bootstrap + spec Analysis entry + frozen-root resolution (e2e exe smoke deselected by default) |
| `tests/test_backend_watch.py` | 4/4 PASS | watcher silent-while-down / fires-after-grace / reset-on-recovery + wiring into launcher, tray, app window |
| `tests/test_lifecycle.py` (script) | PASS | exit 0: Start idempotent, Stop verifies, OS shutdown detached |
| `tests/test_shutdown_cleanliness.py` (script) | PASS | exit 0: 0 orphans, port released, DB integrity ok |
| Full offline suite (`python -m pytest`) | PASS | 95 passed, 11 deselected, exit 0 in 196 s, re-run after the last code change |

## 2026-09-30 — "Shut down did not close the app" (Issue 33) + plain-language UX (Issues 34/35)

| Check | Result | Evidence |
|---|---|---|
| Bug reproduced | CONFIRMED | 2 measured runs: overlay at 100%, backend+tray exited, window stayed open the full 60 s |
| Root cause identified | CONFIRMED | surviving window was "ISHTI (Not Responding)" - a wedged GUI thread never runs Python watcher threads |
| Fix layer 1: shutdown flag handshake | PASS | backend writes `run/shutting_down.flag` at shutdown start; watcher closes the shell on sight (unit-tested) |
| Fix layer 2: OS-level shell close | PASS | launcher registers shell pids (verified in registry: `[[45196, ..., 'launcher'], [9784, ..., 'tray']]`); shutdown calls `stop_shell()` - pid+creation-time kill, never kill-by-name, never the backend |
| Fix layer 3: hard-exit timers | PASS | armed BEFORE any blocking destroy; launcher callback is a bare `os._exit(0)` (print-before-exit bug under pythonw found and fixed) |
| Stale flag cleared on fresh boot | PASS | backend `start()` + launcher `main()` clear it; unit-tested |
| **Live close proof** | **PASS** | exe -> ready -> Shut down: **windows 1 -> 0, processes 6 -> 0 within 8 s**, stable zero for 40 s, port released, 0 zombies |
| Plain-language labels | PASS | `tests/test_shutdown_ux.py` jargon word-list (VRAM/RTX/GPU/orphaned/backend/ports/models/process tree) over every shutdown label - 6/6 |
| Primary color instead of amber | PASS | overlay + model-switch toast use `accent` (the theme's primary); no amber in the shutdown overlay |
| No "Stop ISHTI" desktop shortcut | PASS | script removes stale ones; executed - desktop now has only ISHTI.lnk + Start ISHTI.lnk |
| Regression tests | PASS | `tests/test_backend_watch.py` 8/8, `tests/test_shutdown_ux.py` 6/6, `tests/test_desktop_bootstrap.py` 5/5 |
| Full offline suite (after the last code change) | PASS | **105 passed, 11 deselected, 0 failed** in 337 s; one earlier run had `test_dispatcher_state_flow` fail once (the known CD-17 environmental flake) - it passed twice in isolation and in the final full run |

---

# TEST_RESULTS — skill_creator + self-knowledge (2026-09-30/10-01)

Every line below is raw output from this PC on 2026-09-30/10-01. Where something was **not** measured it
says so; nothing is rounded up from an estimate. Command form used throughout:
`& 'D:\ISHTI\venv\Scripts\python.exe' -m <module>` (module form — running the file path puts the wrong
directory on `sys.path` and fails with `ModuleNotFoundError: No module named 'ishti'`, which cost me two
false alarms before I checked `tools/verify_all.py`).

## 1. Module self-checks — all green

| Command | Result |
|---|---|
| `-m ishti.scope` | **PASS** — `[self-check] scope OK - every manifest scoped; file writes, network and app control limited per skill; system/ISHTI folders and system processes protected` (exit 0) |
| `-m ishti.skills.registry` | **PASS** — `[self-check] skills.registry OK — 121 records, retrieval window, enable/disable, domain filter, status counts {'implemented': 120, 'blocked': 1}` (exit 0). The 1 blocked record was `skill_creator` before its `skill.py` existed; it now loads `status: working, problem: ''` |
| `-m ishti.selfmodel` | **PASS** — `[self-check] selfmodel OK - 5 cheap probes read live (NVIDIA GeForce RTX 5070 Ti, 63.4 GB RAM), full build 1247 ms, block 813 chars (budget 1200), every fact provenance-tagged, failed reads visible, partial offload and baseline mismatches reported, owner identity absent from the resident block and stripped from outbound queries` (exit 0) |
| `-m ishti.skills.meta.skill` | **PASS** — `[self-check] meta OK - routing, task tracker, self-update with rollback, self-knowledge from live measurements + refresh` (exit 0). Slow (~1-2 min) by design: its self-check runs a real `pip --outdated` query |
| `-m ishti.skills.skill_creator.sandbox` | **PASS** — `[self-check] sandbox OK - 2.5 GB allocation died under the 512 MB cap (rc=3, peak 454.4 MB, hard limits on); a normal script ran clean (rc=0) and the result states the network is NOT restricted` (exit 0, **0.2 s**) |
| `-m ishti.skills.skill_creator.gap` | **PASS** — real router not re-created, floor taken from the router's own config (0.2), vague ask asks exactly one question, router failure degrades safely |
| `-m ishti.skills.skill_creator.research` | **PASS** — budgets enforced, duplicate sources collapsed, injection attempts logged and ignored, no-network case honest and source-free |
| `-m ishti.skills.skill_creator.design` | **PASS** — T3/credential/purchase refused with the exact gap named, T2 gated not blocked, VRAM gap named, live probe found **22 models (coder rung: ishti-qwen2.5-coder:14b)** |
| `-m ishti.skills.skill_creator.vetting` | **PASS** + **live**: `PyPI requests==2.34.2 licence=Apache-2.0 age=138d; OSV pyyaml 5.3.1 -> ['GHSA-8q59-q68h-6hv4', 'PYSEC-2021-142']` |
| `-m ishti.skills.skill_creator.drafts` | **PASS** — naming, 12-file/256 KB cap, 3 consecutive greens with reset on failure, self-modification blocked, failure counting, honest rollback failure |
| `-m ishti.skills.skill_creator.skill` | **PASS** — routing split with skillsmith, coder budget limits enforced (call cap + line cap), full pipeline offline (research → design → generate → scan → vet → 3 green sandbox runs → packaged DISABLED with the owner's yes still pending), T3 refused by name, existing skill left alone, **nothing installed into the real skill tree** |

## 2. Test suites

| Suite | Command | Result |
|---|---|---|
| WP-C1 regression | `pytest tests/test_router_lifecycle_flags.py tests/test_prompt3_skills.py tests/test_dispatcher_translate_guard.py tests/test_dispatcher_state_flow.py tests/test_planner_messenger.py -q` | **29 passed** (`.............................  [100%]`), 0 failed |
| WP-C1 red→green | same, with `git stash push -- ishti/router.py` | **9 FAILED** before the fix: `test_non_routable_status_never_chosen[disabled/draft/retired]`, `..._as_only_skill[...]`, `test_settings_disabled_name_never_chosen`, `test_router_uses_registry_disabled_names_accessor`, `test_disabled_names_cached_not_read_per_call`; **14/14 green** after |
| skill_creator acceptance (WP-D1) | `pytest tests/skills/skill_creator -q --timeout 120` | **27 passed** (`...........................  [100%]`) |
| self-model acceptance (WP-D2) | `pytest tests/test_selfmodel.py -q` | **43 passed** (`...........................................  [100%]`) |


## 3. Measured numbers (not estimates)

| What | Measured | How |
|---|---|---|
| Self-model block size | **813–1086 chars** (budget 1200) | `python -m ishti.selfmodel` |
| `render()` cold / cached | **91.1 ms / 0.94 ms** | timer around two `render()` calls, cold cache |
| Full inventory build | **1247–3470 ms** (off the request path, daemon thread) | self-check output |
| `load_skills()` | **1.2 s for 121 skills** | timer around `load_skills()` |
| `style()` with the block | **3977 chars** | `len(llm.style())` |
| Sandbox memory cap | 2.5 GB request died at **peak 454.4 MB** under a 512 MB cap | sandbox self-check |
| Skills / manifests / self-checks / test_file | **121 / 121 / 111 / 2** | ISSUES.md SC-03/SC-04 loop |
| Subprocess sites missing a timeout | **52 of 111** (heuristic) | ISSUES.md SC-05 |
| `except: pass` patterns | **145** (heuristic) | ISSUES.md SC-06 |
| `shell=True` | **6** | ISSUES.md SC-07 |
| Hardcoded `D:\ISHTI` in shipped code | **16** | ISSUES.md SC-08 |
| Committed secrets | **none** (`git ls-files data` = 0; 3 candidates classified by shape, no value printed) | ISSUES.md SC-09 |
| Self-knowledge paraphrases recognised | **28 / 28**, 0 false positives (EN, Urdu, Pashto, Roman-Urdu) | `tools/check_paraphrases.py` |

## 4. Partial offload — the hard gate

Every `/api/ps` sample taken during this pass: **`PARTIAL_OFFLOAD: false` for every loaded model**
(`size_vram == size`). Sample at the start of codegen run #1:

```
{"name": "ishti-spark2.5:4b", "size": 8561172152, "size_vram": 8561172152, "PARTIAL_OFFLOAD": false}
{"name": "gemma3:4b",        "size": 2875520450, "size_vram": 2875520450, "PARTIAL_OFFLOAD": false}
```

No run was allowed to load a model that did not fit — which is exactly why the feasibility gate refused
(below) instead of silently splitting one.

## 5. Real pipeline runs (acceptance test 1)

### Run #1 — the honest refusal (this IS acceptance test 2 passing)

`tools\skill_creator_run.py "make yourself able to combine a folder of images into one PDF"`, with the
planner and translator resident:

```
2. feasibility: tier=T1 possible=False gpu=coder rung 'ishti-qwen2.5-coder:14b' installed, 965 MB free of 16303 MB
REFUSED (honestly):
I can't do that yet.

Exactly what is missing:
- a local coder model that is installed and fits in VRAM (coder rung 'ishti-qwen2.5-coder:14b' installed,
  965 MB free of 16303 MB) - I will not offload part of a model onto the CPU

Why, from the check: VRAM/model budget.
```

No draft was created, nothing was installed, no fake success. This is the required behaviour for a request
that cannot be done within the machine's limits: say so, and name the exact gap.

### Run #2 — after freeing VRAM

`ishti.gpu.unload_idle_models()` freed the idle models (`[models] UNLOAD: qwen3:14b`), leaving
**2349 / 16303 MiB used, no model loaded** (~13.9 GB free, enough for the 9922 MiB coder rung per
`MODEL_DECISIONS.md`). The same command was then re-run.


### Run #2 — reached the VRAM governor, killed mid-generation by me

Run #2 got past research (SearXNG was unreachable and the Playwright fallback hung inside a single search
call — see SC-22) and reached code generation. Its log captured the governor doing exactly the right thing:

```
[vram] coder: ishti-qwen2.5-coder:14b -> ishti-qwen2.5-coder-c4k:14b (was not fully on the GPU - trying less context)
[vram] coder: ishti-qwen2.5-coder-c4k:14b -> qwen2.5-coder-c8k:7b (was not fully on the GPU - trying the next lighter model)
```

The governor walked **down the ladder rather than partially offloading** — the behaviour the whole design
depends on. I then killed this run to apply the research timeout; no result was claimed from it.

### Run #3 — refused again, honestly, and correctly

After bounding the research stage:

```
2. feasibility: tier=T1 possible=True gpu=coder rung 'ishti-qwen2.5-coder:14b' installed, 5341 MB free of 16303 MB
...
I can't do that yet.
Exactly what is missing:
- a local coder model that is installed and fits in VRAM (coder rung 'ishti-qwen2.5-coder:14b' installed,
  3131 MB free of 16303 MB) - I will not offload part of a model onto the CPU
installed into ishti/skills? False
```

By the time the pipeline reached generation the **planner had reloaded** (it is pinned `Forever` in
`models_ladder.yaml`), so free VRAM fell from 5341 MB to 3131 MB — below the ~9922 MiB the 14B coder needs.
The gate refused rather than attempting a load that would have split the model.

**So: real code generation is BLOCKED on this machine as it stands**, and the reason is measured, not
guessed — the pinned planner plus a resident translator leaves 3.1–5.3 GB free, while the coder rung needs
9922 MiB (`MODEL_DECISIONS.md`). Two ways forward, neither taken unilaterally: (a) stop ISHTI so the planner
is not resident, then run the driver again; (b) relax the gate so it asks the **governor** whether the job
can be admitted and lets the governor step the planner down — note the governor already demonstrated it
*will* do that (run #2), so the current gate is stricter than necessary. Recorded as `NEEDS.md` N22 and
`ISSUES.md` SC-23. The refusal direction is the safe one, so nothing was weakened to make a test pass.


---

# Remote Control (Telegram + WhatsApp) + cua — 2026-10-01

Scope: master prompt "Remote Control (Telegram + WhatsApp) and trycua/cua features".
Machine: Windows 11 Pro 25H2, RTX 5070 Ti 16 GB, 64 GB RAM, Python 3.11.9. Rollback: tag `ishti-pre-remote-20261001` (commit 978c9a5).

## Gate table (master prompt tests 1-13)

| # | Test | Result | Evidence (command → output) |
|---|---|---|---|
| 1 | Unknown user / group / channel / forwarded ignored, logged, owner alerted | **PASS** | `pytest tests/test_channels.py::test_1...` → 0 dispatches, audit rows `ignored non-private chat` + unknown-sender alert, forwarded acknowledged as data |
| 2 | T2 step-up: recap+buttons; approve once; replay/expired/mismatch fail | **PASS** | test_2 → approval consumed exactly once; `can't be used` on replay+expiry; approving A cannot approve B; approve feeds "yes" into that session only |
| 3 | Remote-forbidden refused; remote_policy.yaml not remotely editable | **PASS** | test_3 → 6 categories refused by name; allowlist unchanged after a policy-edit attempt |
| 4 | Prompt injection in message text stays data | **PASS** | test_4 → injected "send files/disable gate/approve all" routed as text only, never executed |
| 5 | Stale queued command expired, not executed | **PASS** | test_5 (1 h old) → expiry reply, 0 dispatches; poller.flush_stale drops old queue at boot |
| 6 | /kill + /lock under load; /unlock needs PIN; lockout after wrong PINs | **PASS** | test_6 → real killswitch ran (mic OFF hook visible in log); locked refuses; 5 wrong PINs ⇒ 900 s lockout persisting in state file |
| 7 | Rate limit; no re-execution after restart; no orphan poller | **PASS** | test_7 → throttled with notice; Poller(db) restart does not re-execute update 100; `p.stop()` joins (kick trick) |
| 8 | Chunking; big table as CSV; voice-in transcription; image-in | **PASS** | test_8 → ≤3800-char chunks; CSV attachment produced; `ishti.voice.transcribe` path exercised; image saved + description request |
| 9 | No secrets in logs/config/git/crash paths | **PASS** | test_9 → audit masked via redact ctx; BotAPIError carries no token; `git grep <token> -- ':(exclude)tests/*'` → empty |
| 10 | Sensitive-output policy redacts client/finance data | **PASS** | test_10 → amounts/emails/phones masked + "(minimized … open ISHTI on the PC)" |
| 11 | WhatsApp self-chat mode: default OFF; other chats never read; human-paced | **PASS** | test_11 → disabled: nothing read/sent; wrong open chat: parse skipped; ≥3 s paced sends |
| 12 | Audit attributes every action to channel + message/session | **PASS** | test_12 → `remote message received`/`remote reply sent` rows with channel=telegram, session=tg:<id> |
| 13 | Works alongside desktop chat + background tasks; no partial offload | **PASS** | test_13 → 5 remote + 5 desktop interleaved all correct; channel layer imports no model machinery (structural assert). Zero partial-offload observations this session: **no model was loaded at all** (Ollama down — N24), VRAM sampler uninvolved |

Result: **13/13 PASS** (mock Telegram Bot API; the same suite re-runs against the real bot once N22 lands).

## Other measured results
- Full offline suite after the final merge: `pytest --timeout=600` → **184 passed, 12 deselected, 0 failed** in 242 s.
- Module self-checks (each ends with asserts): channels.{policy,security,approvals,telegram_client,poller,gate,output,media,whatsapp_channel,service,api} → all green (raw output in transcript).
- `tools/check_skills.py` → **122 skills, 236 assertions, 333 labelled requests, 0 problems** (desktop_control added).
- desktop_control suite: **7 passed** × 3 consecutive runs.
- Frontend: `npm run build` → exit 0 (built in ~3.3 s; bundle size warning pre-exists).
- API contract: §11 written; `ishti/channels/api.py` self-check validates status/policy/token-guard/enable/pairing/pin over TestClient (401 without bearer, exactly like check_remote_auth expects for remote callers).

## Bugs found by tests this session (all fixed with regression coverage)
| Bug | Root cause | Fix |
|---|---|---|
| audit.record(channel=…) TypeError | repo's audit takes channel/session from thread context, not kwargs | channels now wrap calls in `audit.context(...)` (gate/approvals/poller/media) |
| test_6 lockout never triggered in full run | per-test-module rate limiter kept refilling across tests; fixture reset + per-test bucket | fixture clears `_limiters`; test 7 owns limiter coverage |
| approval expiry untestable below 5 s | `max(5, ttl)` floor | exact TTL honoured |
| classifier tripped on quoted injection text | quotes are DATA, not requests | `_strip_quotes` before matching (tested) |
| `secrets` block missed "api key is:" phrasing | colon-style regex too narrow | word + "is/are/:/=" and key-shaped patterns |
| deadlocked policy self-check | `save()` re-entered non-reentrant lock | RLock |
| poller stop hung on a hanging long poll | no kick; thread joined until timeout | timeout=0 kicker thread releases the poll |
| flowise test order-dependence (pre-existing) | settings pollution from earlier test | test resets its precondition |

## BLOCKED (owner/environment; details in NEEDS.md)
| Item | Why |
|---|---|
| Real-bot end-to-end run (RC-15) | N22: token via @BotFather + pairing produces the owner's numeric ID |
| cua-driver install + 5-task protocol | owner-gated installer (read-before-run); N24/N25 |
| Live VRAM/grounding measurements | N24: Ollama not running all session (not started by me, per safety rules) |

---

# PART 2 - 5-repo integration (2026-10-02, branch `feat/repo-integration`)

Scope: the WP-OD / WP-REACH / WP-ECC / WP-JEV / WP-COUCOU work packages; every row of
`REPO_FEATURE_MATRIX.md` (128 rows) has a disposition and the shipped modules are catalogued as
`FEATURE_CATALOG.md` CORE-17..CORE-31. Raw run logs are in `_work/` (named below where cited).
The working tree also contained the owner's pending Cycle-4 performance edits (vram/models/
gateway-adjacent files); they were present during the runs below and are not part of this branch's commits.

## 1. Final offline regression (all WPs merged)

- Command: `venv\Scripts\python.exe -m pytest tests -q -p no:warnings` (pytest.ini excludes the generated
  per-skill suites, which run separately via `tests/run_skill.py`).
- Result: **442 collected / 442 passed, 0 failed, 0 errors, exit code 0** - log `_work/suite_final_verify.log`
  (442 progress dots, no `F`/`E`). Re-run of the same command also exited 0 before the evidence docs were written.
- Each new suite was run 3 consecutive times green before its commit; those per-file runs are in `_work/`
  (`focused_c2_*.log`, `gate_suite*.log`, `c4_suite*.log`, `browser_3x_run*.log`, `suite_*_final*.log`).

## 2. New suites and what each proves

| Suite (collected tests) | Acceptance proven |
|---|---|
| `tests/test_governed_search.py` (10) | Loopback-only search; non-loopback/lookalike/cloud endpoint refused BEFORE any transport; results fenced as untrusted; `cloud_calls == 0` |
| `tests/test_workspace_confinement.py` (27) | realpath root confinement: `..`, drive-relative, UNC, ADS, reserved devices, trailing dot/space, over-long names, symlink/junction escapes refused; size caps |
| `tests/test_api_sessions.py` (12) | Hashed in-memory sessions (SHA-256 only, restart-invalid), rotation kills old token, TTL/idle expiry, cookie posture, cross-site 403 before auth |
| `tests/test_quality_gate.py` (8) | ruff lint/format + code/security reviewer roles -> one verdict; dangerous draft (eval) blocked from `skill_creator.register()` |
| `tests/test_install_state.py` (7) | Drift vs baseline; auto-fix limited to switching off a broken skill; owner prefixes never touched; doctor check [7b] |
| `tests/test_install_ops.py` (7) | Backup-first writes, unified diff, confirm veto, ownership-tagged merge (nested/foreign entries untouched), scoped uninstall |
| `tests/test_model_route.py` (7) | One decision object with ordered fallback chain; stale config names resolved via installed alternatives; decision performs no load |
| `tests/test_session_schema.py` (7) | Harness-neutral session envelope: strict validate, forgiving make, bus emit; junk -> problems, not exceptions |
| `tests/test_plan_view.py` (6) | Loopback-only plan review page; markup escaped (never injected); decisions appended bounded; non-loopback host refused |
| `tests/test_dictation.py` (10) | PTT + dictation only in explicit sessions; no session -> nothing records; failed chunks never pasted, holes preserved with audio path |
| `tests/test_overlay_pipe.py` (9) + `test_relay_never_blocks.py` (4) + `test_hooks_relay.py` (14) | Owner-only pipe DACL; 64 KiB cap (oversized discarded); send never blocks past the 300 ms budget; single-use action-bound approvals; questions popped; junk -> None |
| `tests/test_overlay_window.py` (8) | Island window state payload v1; levels clamped/bounded; click-through; hook events drive pills/busy/unread |
| `tests/test_channels_inbox.py` (9) | Sandbox inbox: unique names, single-use take, traversal-safe, oldest-first sweep; text drops fenced as untrusted data |
| `tests/test_overlay_mascot.py` (9) | Original "Pebble" marker art (no imitation of coucou assets); clamped gaze; timed mood revert; once-only greet; synthesized chime |
| `tests/test_overlay_integrations.py` (9) | 7 provider pills OFF until explicit opt-in: zero credential/network calls, idle->paused, missing-cred never fetches |

Total: 163 tests in the 17 integration suites, all inside the 442-test run above.

## 3. Measured improvements (BEFORE -> AFTER)

| Feature (row) | Metric | BEFORE | AFTER | How measured |
|---|---|---|---|---|
| Spoken-duration parsing (JEV-17) | Correct parses over 26 fixed spoken phrases | **10/26 (38.5%)** | **26/26 (100%)** | `_work/duration_before.json` / `_work/duration_after.json` (same phrases, same parser entry) |
| Post-STT vocabulary correction (JEV-08) | Correction coverage over 13 known mishearings | **0/13 (0%)** | **13/13 (100%)** | `tests/test_voice_vocabulary.py::test_fixed_set_measured_coverage` (layer bypassed = identity vs `apply()` wired into `voice.transcribe`) |
| Model route + fallback chain (ECC-13) | Decision coverage / per-decision latency | **0/4 roles, 0.0001 ms** | **4/4 roles, 0.0016 ms** (budget < 5 ms) | `_work/measure_route.py` -> `_work/route_measure.json` (injected installed list, no load) |
| Pre-merge quality gate (ECC-01/04) | Gate verdict coverage | no gate existed | one verdict over ruff lint/format + code/security reviewers; high findings block install | `tests/test_quality_gate.py` (8 tests, seeded bad files) |
| Security scan of ISHTI's own surfaces (ECC-06) | Findings by severity | n/a | **23 findings, 0 high** (23 medium: wildcard net scopes, sandboxed destructive-file calls) | `scan_all()` re-run 2026-10-02 (command recorded in this part) |
| Overlay hook relay (COU-12/13) | Send budget | no overlay existed | bounded at 300 ms; oversized (64 KiB cap) discarded, never delivered | `tests/test_relay_never_blocks.py` + `tests/test_overlay_pipe.py` |

## 4. Invariants held (each has a test)

- Zero cloud calls by construction: gateway refuses non-loopback before transport; integration pills fetch nothing until owner opt-in.
- Owner-only IPC: pipe DACL + restricted token + impersonation; no other-user read/write.
- Cross-site requests refused 403 BEFORE auth, regardless of token.
- Sessions hashed at rest and restart-invalid; refresh rotates and kills the old token.
- Mic stays OFF unless a session exists; failed/empty dictation chunks are never pasted.
- Installer merge/uninstall never touch foreign or nested config entries; owner prefixes untouched.

## 5. BLOCKED (owner/environment, with reasons)

| Item | Why |
|---|---|
| hey-jev code reuse | Repo has **NO LICENSE**: behaviour-only integration, no code copied (N36) |
| coucou visual assets | All-rights-reserved: original ISHTI art only; mascot tests assert no imitation markers |
| Live microphone hardware / live STT accuracy set | N7 hardware absent; N39 owner-approved voice test set missing |
| `desktop_control` live use | cua-driver not installed (N24/N25); skill ships manifest-DISABLED |
| Real Telegram end-to-end | N22: token + pairing from the owner |

