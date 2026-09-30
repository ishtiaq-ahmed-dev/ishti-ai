# SKILL LOOP REPORT — full QA of all 120 skills (2026-09-30)

## Method (per owner rules)
- Harness: `ISHTI_TEST_MODE=1 venv\Scripts\python.exe tests\run_skill.py <skill[,skill2,...]> --runs 1 [--timeout N]`.
  120 generated suites (`tests/skills/<skill>/test_<skill>.py`), per-skill state in `TEST_STATE.json`
  (`consecutive_green` across harness invocations).
- Every happy-row preset was probed LIVE through the skill's real `run()` before pinning; gen presets
  fixed where the generated route fragment could never match a real op (never the test).
- Commit before fixes; regression tests added for every code fix; nothing weakened.

## Final state (measured from TEST_STATE.json, 2026-09-30)
- **120/120 skills tracked. 119 at 3+ consecutive greens. 1 red: shopping — environment-blocked, not a code defect.**
- Distribution: `consecutive_green`: 119 skills ≥ 3 (7 at 4, 1 at 7), shopping at 0.
- Long-tail skills verified with `--timeout 300`: deep_research (PASS, 521 s pass-2 / 481 s pass-3),
  gemini (PASS, 353 s both), meta (PASS, 154–158 s), travel (~58–60 s), browser (~125–130 s),
  whatsapp (~245 s pass-2; T2 rows exercise real WhatsApp Web read-only).

## Fixes landed this mega-request (all with regression tests)
| Fix | What | Evidence |
|---|---|---|
| FIX-9 | mailmerge/redact/pdftables crashed with AttributeError on unmatched shapes (`.group()` on None) → decline honestly | `tests/skills/redact/test_redact_no_crash.py`; suites green |
| FIX-10 | meta op_health ran ~72 s of subprocess self-checks per call, no cache → mtime-hash cache (900 s TTL); meta suite 277 s FAIL → 154–158 s PASS | `tests/skills/meta/test_meta_cache.py` |
| FIX-10 follow-up | the cache regression test had never been executed: its ROOT was one dirname short → FileNotFoundError on first real run | fixed `tests/skills/meta/test_meta_cache.py`, meta suite green |
| FIX-11 | shopping called SearxngSearchProvider directly, skipping the manager cascade → now uses get_search_manager() | shopping suite reaches honest decline; priced rows still env-blocked |
| FIX-12 | travel.run() had NO matcher gate: any input (empty edge rows, prompt-injection row) drove the real Chrome to Google Travel and waited on results → declines at the door; matched rows still hit the live page (priced flights table verified live) | `tests/skills/travel/test_travel_decline.py` (3 tests) |
| gen presets | ~50 skills' happy rows were bare route fragments that could never match a real op (e.g. `add word`, `run`, `sensitivity analysis of f(x) = 30*x - 1000` without the range clause, `show my paper trading account` — trading requires the word "paper"). Each replaced with a live-probed real row | `tools/gen_skill_tests.py` presets dict; all suites green |
| process | commit 64c2e46 claimed 5 preset fixes that were never in the file (only standup landed). Re-verified with grep AFTER edit, re-landed, committed b9ada92 with `git show --stat` checked | b9ada92 |

## Honest findings (not fixed here, by design or environment)
1. **shopping priced results — BLOCKED on Q9** (owner decision): SearXNG Docker engine is down and NO
   container exists; BingLite bot-walls for stretches. `compare price of usb c cable` matches TRIGGER but
   run() returns None (0 priced rows) → the honest decline is correct behaviour; there is nothing to list.
   FIX-11 is in place; the suite will go green the moment a working search backend exists.
2. **Sudoku myth-busted**: the widely-copied "530070000**006**195000..." grid is genuinely UNSOLVABLE
   (adjudicated with an independent solver). The skill's "No solution exists — verified by exhaustive
   search" was correct; the canonical grid (row 2 = 600195000) solves. Preset pinned to the canonical grid.
3. **desktop skill is GPU/display-contention flaky**: its happy row does a real screen capture + vision
   model. Passed 9.5 s standalone, timed out at 300 s once while the owner's live ISHTI instance held
   14.9/16.3 GB VRAM, green (11–12 s) on retry. Environment-sensitive, not a code bug.
4. **QA-vs-live-app GPU contention**: harness runs repeatedly hit GpuBusy while the owner's live instance
   (`python -m ishti 8888`, up since 11:05) held models. VRAM dropped 14.9 GB → 0.7 GB after the app's
   idle-unload (~4 min). Batch runs interleaved with live use will flap; run long batches when idle.
5. **skillsmith generation gaps** (NEEDS.md log): two of its live generations failed self-check
   ('pdf_to_text', 'temperature_conversion'). Its own suite is green (creates a skill, honest failure path);
   generation quality is a separate workstream.
6. **T2 test-coverage nuance**: under ISHTI_TEST_MODE=1 the dispatcher blocks T2 skills entirely
   (audit-recorded), so the generated suites prove routing/gating for whatsapp/workflows/browser/trading;
   their read-only run() paths were additionally probed live (whatsapp unread check, browser, paper account).
7. Two generated-suite oddities confirmed as harness contract, not bugs: EDGE rows include
   `happy + "???!!!"` (same op) and the prompt-injection row contains "delete" (honest declines vary).

## Harness/process notes for the next runner
- Batch comma-lists are supported, but state saves only at end of invocation — keep batches ≤ 20 skills.
- `-x` in the harness means one failing test aborts a suite; a GpuBusy/timeout on a browser-heavy suite
  can cascade to later skills in the same batch — re-run those individually before diagnosing.
- The five suites needing `--timeout 300`: deep_research, gemini, meta, travel, browser (whatsapp ok at 120–300).

## Coverage statement
- 120 generated suites exist; all 120 executed through the real harness this session (118 skills at 3+,
  browser/pantry/trading brought from 0 to 3, shopping re-attempted and re-classified).
- No test was weakened; every preset change made rows HARDER (real args, real files, live services).
