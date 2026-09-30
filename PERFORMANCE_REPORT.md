# PERFORMANCE REPORT — baselines & top improvements (2026-09-30)

All numbers measured live this session on the owner machine (RTX 5070 Ti 16 GB, Ollama local).
Raw data: `reports/arch_compare_results.json` (per-row), commit history for fix evidence.

## Baselines (measured)

### Model load costs (Ollama, timed via /api/generate after keep_alive=0 unload)
| model | role | cold load | warm |
|---|---|---|---|
| gemma3:4b (2.7 GB) | translator | 3.0 s | 0.45 s |
| qwen2.5-c8k:7b (4.7 GB) | planner | 6.9 s | 0.19 s |
| qwen3:14b (9.0 GB) | arch2 main/validation | 6.1 s | 0.23 s |

First-question cost after idle is dominated by cold loads; warm inference is negligible for
short replies. VRAM after the sweep: ~10.9 GB used.

### Request latency by path (20-row arch compare, dispatcher end-to-end)
- arch1: median 1.4 s, 14/20 rows ≤ 2 s — but slow rows are very slow:
  - `check your skills` → META **79.7 s** (first health pass in the process fills FIX-10's
    mtime-hash cache; subsequent calls are cached in-process only)
  - `solve 2x + 3 = 11` → CHAT **20.8 s** (planner model writes a math explanation)
  - `what time is it right now` → WEB_SEARCH **19.7 s** (a live web round-trip for a clock!)
- arch2: median 0.9 s, 18/20 rows ≤ 2 s, max 8.7 s (sudoku row: brain tried manual solving).
- Idle CPU (from the earlier desktop-icon E2E, unchanged): 0.1–0.2 %.

### The language-rewrite tax (owner's Roman-Urdu lock is SET on this machine)
Every skill reply is rewritten by gemma3:4b before it reaches the owner:
| reply shape | measured rewrite time |
|---|---|
| short ack (33 chars) | 5.85 s (incl. one cold load) |
| data row (98 chars) | 1.49 s |
| long reply (273 chars) | 1.01 s |

≈ 1–6 s added to **every** reply — larger than the skill work itself on fast rows. FIX-13's
retry (landed this session) can add a second pass when the translator stalls.

### GPU coexistence (root cause of the session's GpuBusy flaps)
80 % budget on 16 GB = ~12.8 GB usable. gemma3:4b (2.7) + qwen2.5-c8k:7b (4.7) fit together;
adding qwen3:14b (9.0) does NOT — measured: `gemma3:4b needs about 2.7 GB and only 1.0 GB is
free (in use: qwen3:14b)`. Consequence: **while arch2's brain is loaded, the translator cannot
load**, so language-locked users on arch2 get untranslated (but correct) replies, and QA batches
flap when the live app holds models (NEEDS.md Q10).

## Top 3 improvements (in expected-value order; not yet implemented — owner call)

1. **Skip the translator rewrite for data-shaped replies.** Replies that are numbers, paths,
   tables, or already in the target script gain nothing from gemma3 and pay 1–6 s per reply
   (plus FIX-13 retry risk and GpuBusy coupling). A regex gate (no target-script chars ∧
   data-shape) saves ~1–6 s on the majority of skill rows — the single biggest per-request win.
   Accuracy side-benefit: fewer translator stalls like the one FIX-13 caught.
2. **Reflex the clock.** "what time is it right now" went to WEB_SEARCH (19.7 s). A deterministic
   reflex (the dispatcher already has the reflex layer) answers in <1 ms. Same class: local date,
   day of week. Saves ~19 s on a common ask; also removes pointless network dependence.
3. **Persist the meta health cache to disk** (FIX-10's cache is per-process, keyed on skill-file
   mtimes — safe to persist). First `check your skills` in a process costs 72–80 s today; a
   disk-backed cache turns that into milliseconds when no skill changed since the last run.

## Already landed this session (efficiency/accuracy, with regression tests)
- FIX-10: meta health 72 s/call → cached (mtime-hash); meta suite 277 s → ~155 s.
- FIX-13: confused translator no longer replaces real replies (accuracy) with bounded retry
  (efficiency guard: max 2 attempts, then original text).
- FIX-12: travel declines unmatched input instead of opening a browser session per junk row.
- verify_all/pytest fully green: 149/149 + 114/114 — the regression floor any optimization
  must keep green.
