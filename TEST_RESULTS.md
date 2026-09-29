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
