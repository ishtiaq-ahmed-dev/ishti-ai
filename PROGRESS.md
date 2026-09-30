# PROGRESS — Master prompt: scan → plan → fix → 24h Chat Digest → verify

Session date: **2026-09-29**. Machine: Windows 11, RTX 5070 Ti 16GB, 64GB RAM.
Working tree at start: clean, `main` @ `4a6dc3e` (rollback point for every phase below).

Legend: [x] done · [~] in progress · [ ] not started · BLOCKED = needs the owner (see NEEDS.md)

## Phase 1 — Discover (read-only)
- [x] Project mapped: 121 skill folders, `ishti/` (209 .py), `interface/` (React UI + 58KB FastAPI server),
      `orchestrator/`, `desktop/`, `browser/`, `voice/`, `tools/`, `tests/` (60 files), `data/` (7.4 GB).
      Evidence: directory listings in this session; `PROJECT_MAP.md` (prior pass) still current.
- [x] Existing docs read: `ISSUES.md` (35 KB, prior audit), `NEEDS.md` (N1–N11), `VERIFICATION_REPORT.md`,
      `MODEL_DECISIONS.md`, `MAC_CHANGES.md`, `docs/PROGRESS.md`.
- [x] Secret scan of the new surface: no credentials in the files touched by this pass (only `data/api_token`,
      already git-ignored; not read into any report).
- [x] Gap confirmed by search, not assumption: **`chat_digest` did not exist** (`search_codebase` → no results),
      and the chat UI has **no GFM table support** (`interface/app/src/components/Markdown.tsx`).
- [x] Export toolchain probed: `python-docx 1.2.0` ✅, `playwright 1.63.0` ✅, `pypdfium2` ✅ (page → PNG),
      Edge + Chrome binaries present ✅, `reportlab` **missing** (fallback only, not required).

## Phase 2 — Plan
- [x] `PLAN.md` written: work packages, ownership, files, proof test, risk, rollback.

## Phase 3 — Execute
- [x] WP1 `chat_digest` core — `ishti/skills/chat_digest/{manifest,store,window,analyze,report,skill}.py`
      (6/6 module self-checks pass: store, window, analyze, report, export, skill)
- [x] WP2 exports — `export.py`: markdown, DOCX (landscape + `w:tblHeader` + RTL runs), PDF
      (**verified live: 3-page PDF produced by `msedge.exe` headless printing**, every page rasterised with
      pypdfium2 and looked at; `reports/*_chat_digest.pdf_p1..p3.png`)
- [x] WP3 chat UI tables — `interface/app/src/components/Markdown.tsx`: escaped-pipe-aware `splitRow`,
      rectangular rows, sticky header, copy-table button, wrapped cells, `dir="auto"` for RTL.
      `npm run build` (tsc -b + vite) exit 0; the served `interface/app/dist` was rebuilt with it.
- [x] WP4 tests — `tests/test_chat_digest.py`: **17 tests** (N1–N12 + 5 regressions for bugs this pass found)
      all passing, plus `tools/chat_digest_sample.py` which writes the sample reports into `reports/`.
- [x] WP5 re-scan + docs — 12 defects found by running the code, all fixed with a regression test where
      code could fix them; `ISSUES.md` CD-01..CD-18, `NEEDS.md` N12–N20, `TEST_RESULTS.md`, this file.
      Two remain OPEN for the owner/environment: CD-17 (the shared GPU makes one dispatcher test fail with
      its own `GpuBusy` message) and CD-18 (a missing hygiene test referenced by `tests/conftest.py`).
- [x] WP6 adversarial review — **the sub-agent tool returned an authentication error**, so the falsification
      pass was run by me and is labelled self-review, not independent verification: forged source ids, table
      torture, relationship invention, chat-budget coverage, owner-name config (all in `TEST_RESULTS.md`).
- [ ] WP7 commit + tag

## Bugs this pass actually found (all by running, not by reading)
| ID | What went wrong | Fix | Regression test |
|---|---|---|---|
| CD-01 | the owner's own promise outranked the client waiting on him | owner messages rank last and are labelled "you said:" | `test_regression_owner_promise_never_outranks_someone_waiting` |
| CD-02 | "by 30 Sep" (the commonest form) was not a deadline | `MONTHS` gained 3-letter abbreviations | `test_regression_dates_are_not_guessed` |
| CD-03 | **every** Urdu message raised `IndexError` | the Urdu branch of `REL_DAY` now captures its group | `test_regression_urdu_relative_day_does_not_crash` |
| CD-04 | "clause 7.3" became a 7-March deadline | document-reference blacklist + date-context rule | same as CD-02 |
| CD-05 | after that fix, "submission **deadline** 02/10/2026" lost its date | the blacklist needed a word boundary | same as CD-02 |
| CD-06 | Pashto "سبا" was in the lookup table but not in the pattern | both lists aligned | same as CD-03 |
| CD-07 | the usefulness rubric never reported a deadline | `deadline_ts` now reaches the rubric | same as CD-01 |
| CD-08 | 21 identical action rows from one chat | duplicates collapse, sources kept | `test_regression_duplicate_actions_collapse_but_keep_sources` |
| CD-09 | the PDF printed `**Window:**` literally | bullets go through the inline parser | `test_regression_html_keeps_bold_in_bullets` |
| CD-10 | the chat UI broke on `\|`, ragged rows, no sticky/copy/RTL | `DigestTable` + `splitRow` | N8 asserts source + built bundle |

## Measured numbers (not estimates)
- Analysis + render, offline, no model loaded: **10 chats 0.006 s · 50 chats 0.027 s · 200 chats 0.108 s**.
- Synthetic sample: 11 chats found, 9 analyzed, 2 skipped (each with a reason), 239 messages, 9 action items,
  1 decision, 1 injection attempt flagged and ignored, 1 chat with sensitive content, 1 media item not
  analyzed, **0 markdown table problems**.
- Routing: `python -m ishti.check` → **333/333 correct, median 0.2 ms** (329 before this pass; the 4 new
  labels are the digest's own phrasings).
- Skill contracts: `python tools/check_skills.py` → **120 skills, 232 assertions, 329 labelled requests, 0
  problems** — including "no matcher steals another skill's request" (`check my whatsap ... unread` still goes
  to `whatsapp`).
- Frontend: `npm run build` → exit 0, 2328 modules, `dist/assets/index-*.js` 1,011 kB (gzip 289 kB).

## Notes for resuming after context loss
1. Everything new lives under `ishti/skills/chat_digest/`, `tests/test_chat_digest.py`,
   `tools/chat_digest_sample.py`, `reports/`.
2. Run: `venv\Scripts\python -m pytest tests/test_chat_digest.py -q` (offline, no Ollama needed).
3. The skill never sends/replies/reacts/deletes anything — T0, read-only; sending stays T2.

## Session 2026-09-29 (late) — desktop launch bug (owner report: "click Start ISHTI -> error")
- [x] REPRODUCED: `dist\ISHTI\ISHTI.exe` died at launch with `ModuleNotFoundError: No module named 'json'`
      (captured from the exe itself), left a zombie ISHTI.exe, no window.
- [x] ROOT CAUSE: the exe froze `desktop/launcher.py` but bundled only 18 files in `_internal`; at runtime it
      imported raw repo modules (`desktop/shutdown.py`) needing stdlib modules the exe never carried. The
      earlier `urllib` hiddenimport commit (4b9346d) treated one symptom of a broken packaging strategy.
- [x] FIXED (architectural): `ISHTI.exe` is now a tiny stdlib-only bootstrap (`desktop/exe_bootstrap.py`,
      frozen per new `ISHTI.spec`) that hands off to the ONE real launcher (`desktop/launcher.py`) under
      `venv\Scripts\pythonw.exe`. Readable error dialog if the venv/launcher is missing. No duplicated
      lifecycle logic; the exe can no longer go stale when the venv gains a dependency.
- [x] FIXED (found during verification): after an external `POST /api/shutdown`, the launcher, tray and app
      window survived around the dead backend (orphan pile-up = the "unreliable Start/Stop" feel).
      `desktop/backend_watch.py` now runs in all three; they exit once the backend has been up and then
      stays gone >25 s (a watchdog restart dips health for seconds, not minutes).
- [x] VERIFIED end-to-end: exe -> ready ~20 s -> window "ISHTI - Intelligent Personal Assistant" ->
      `POST /api/chat` answered `PONG` -> `POST /api/shutdown` -> **0** leftover ISHTI processes (venv
      pythonw trampoline doubling accounted for), port released, SQLite `integrity_check` ok, 0 exe zombies.
- [x] REGRESSION TESTS: `tests/test_desktop_bootstrap.py` (AST-locks the stdlib-only bootstrap rule + spec
      entry point + frozen-root resolution; e2e smoke marked e2e) and `tests/test_backend_watch.py`
      (watcher grace/reset/silent-boot semantics + wiring into launcher/tray/app_window).
- [x] `tests/test_lifecycle.py` + `tests/test_shutdown_cleanliness.py` re-run green after the lifecycle
      changes; full offline pytest suite re-run green as the session's last action (see TEST_RESULTS.md).

## Session 2026-09-30 — "Shut down did not close the app" + UX plain-language pass
- [x] REPRODUCED twice with measured timelines: overlay hit 100% "Goodbye", backend + tray exited, port
      released, but the window stayed open 60 s. Third run caught the cause: the survivor was
      **"ISHTI (Not Responding)"** - a wedged GUI thread never runs Python watcher threads, so NO
      in-process close path (JS bridge, watchers) can be trusted inside the shell.
- [x] FIXED in three layers: (1) shutdown flag handshake - backend writes `run/shutting_down.flag` at
      shutdown start; desktop watcher closes the shell on sight (no JS, no HTTP); (2) **OS-level shell
      close** - launcher registers itself + tray in the run registry, the shutdown sequence calls
      `stop_shell()` (TerminateProcess by recorded pid + creation time, never the backend); (3) hard-exit
      timers armed before any blocking destroy call.
- [x] Found + fixed on the way: the launcher's watcher callback printed before `os._exit` - under pythonw
      sys.stdout is None, so the print raised and the watcher silently died (why the launcher survived
      when the tray exited). Callback is now a bare `os._exit(0)`.
- [x] Found + fixed: a stale flag from the previous run false-fired the watcher at next boot; the backend
      `start()` and the launcher `main()` both clear it now.
- [x] UX pass (owner request): shutdown step labels + overlay + exit dialog + splash errors now plain
      language in the primary (accent) color - no RTX/VRAM/orphaned-processes wording anywhere user-facing.
      Removed the desktop "Stop ISHTI" shortcut (script now removes stale ones too).
- [x] VERIFIED: exe -> ready -> Shut down -> windows 1 -> 0 and processes 6 -> 0 within 8 s, stable zero
      for 40 s, port free, no zombies. Registry shows `shell_pids` correctly recorded.
- [x] Tests: `tests/test_backend_watch.py` (8: flag handshake, OS-level close, stale-flag, wiring),
      `tests/test_shutdown_ux.py` (6: jargon-free labels, accent color, no Stop shortcut, guaranteed
      close). Full offline suite re-run at the end of the session.
4. The PDF path that works on this box is **Edge headless printing**; Playwright's own Chromium is not
   downloaded for the Python package (the code tries it first, then falls back automatically).

## Second test-harness pass (2026-09-29, later the same day)
- [x] Found and fixed the reason the documented one-command suite could not finish: pytest's collection phase
      was **importing and executing** nine standalone self-check scripts (see `ISSUES.md` CD-19).
      `tests/conftest.py` now ignores them with the reason; `tests/test_selfcheck_hygiene.py` (new) keeps the
      list honest and also asserts that an ignored module's own `__main__` block still runs its tests.
- [x] Fixed `tests/test_lifecycle.py` (CD-20): the subprocess double lacked the `subprocess.run()` protocol, the
      test opened a **real** desktop window, and its launcher/`start.bat` assertions pinned a refactored layout.
- [x] Re-measured: `pytest --collect-only` **4 s** (was >300 s / exit 127 = `0xC0000409`); full suite
      **86 passed, 10 deselected, 117 s**; `tools/verify_all.py --only test:` **24/24** (was 21/24).
- [x] CD-18 closed by `tests/test_selfcheck_hygiene.py` (it is now tracked and collected).
- [ ] CD-17 / CD-21 stay OPEN - both are the same environmental cause (another session owns the GPU, so the
      model-assisted routing tests can fail with `GpuBusy`); they need a quiet box, not a code change.
- [x] WP7 commit + tag - done by the concurrent session: the chat-digest work is committed (`b5a2038
      feat(chat_digest): the 24-hour chat digest (Section 4) + GFM-grade chat tables`) and tagged
      **`chat-digest-2026-09-29`**; `git status` is clean apart from this pass's three doc files.

## QA Session 2026-09-30 - Prompt 1 of 3 (scan -> catalog -> matrix -> harness -> feasibility)
Rollback point: tag `pre-qa-suite1` (commit 9a001c1).
- [x] FEATURE_CATALOG.md: 120 skills (T0:68/T1:38/T2:14) + 16 core rows, generated from manifests by
      tools/gen_feature_catalog.py (regenerable). MISSING-as-named: media_rename, browser_agent,
      diagrams, skill_library (live inside media/browser/chart/skillsmith). Also MISSING: LoRA weights,
      chat upload picker, live mic hardware.
- [x] DEPENDENCY_GRAPH.md: layers 0-4 + safe test order (foundations first, T2 last).
- [x] ISHTI_TEST_MODE=1 switch (ishti/test_mode.py): T2 blocked at the LAST step + audit record +
      scripted yes/no; wired into the dispatcher's pending gate AND T2 pre-run. E2E proven: terminal
      delete request -> gate -> yes -> "TEST-MODE BLOCK T2 terminal" in audit, nothing executed.
      Default OFF; production path unchanged (11 curated dispatcher tests still green).
- [x] tests/skills/<skill>/: 120 generated suites (happy/language/edge/failure/safety/dispatcher rows)
      via tools/gen_skill_tests.py. Spot-proven: weather, bayes, notes, chat_digest, calc (55 passed).
      pytest.ini excludes tests/skills from the default run (module-name collisions + runtime).
- [x] Product finding recorded: FINDING-QA-CALC-1 - calc crashes (raw sympy ValueError) on
      "solve 2x + 3 = 11 for x" (in FEATURE_CATALOG.md). NOT fixed (prompt 1 = discovery only).
- [x] Harness tests/run_skill.py + tests/README.md + TEST_STATE.json: per-test timeout, GPU sampling,
      audit safety assertion (SAFETY-FAIL), --dry, --json, --runs. PROVEN on weather, bayes,
      chat_digest: 3/3 green; state tracked with consecutive_green.
- [x] tests/scenarios/: 12 YAMLs (all parse; required keys asserted) for prompt 3's concurrent runs.
- [x] NEMOTRON_FEASIBILITY.md: GO (on-demand tool). Measured on this GPU: loads (git-main
      transformers 5.18.0.dev0 only - 5.17.0 lacks the class), ~1.0 GB VRAM, offline RTF 0.005
      (97.6 s audio in 0.51 s), 6 speakers/29 segments on the official sample, streaming low_latency
      1.04 s alg. latency @ 15.6 ms/step. Accuracy on owner audio: BLOCKED (no mic/recordings - Q1).
      License OpenMDW 1.1 (commercial OK). Install isolated in .qa_nemotron/ (venv untouched).
- [x] OPENMUSE_IDEAS.md: NO-GO as dependency (cloud key required in every mode - conflicts with
      local-first); 3 ideas to build with attribution (follow-up queue, task leases, page monitors).
- [x] NEEDS.md Q1-Q5 added.
- [x] Final full-suite re-run after the last code change: **105 passed, 11 deselected, 0 failed** in 179 s.
      Prompt 1 of 3 is COMPLETE; prompts 2-3 can run from here (rollback tag `pre-qa-suite1`).

## QA Session 2026-09-30 - Prompt 2 of 3 (skill loop: test -> fix -> retest)
Rollback point: tag `pre-skill-loop2`. Loop = test via `tests/run_skill.py <skill> --runs 1` -> fix real
bugs (TEST_FIXES.md) -> retest until 3 consecutive greens -> reviewer adversarial pass -> sign-off in
reports/skills/<skill>.md. All runs with ISHTI_TEST_MODE=1.

| Skill | Status | Attempts | Bugs fixed | Green runs | Latency | VRAM | Notes |
|---|---|---|---|---|---|---|---|
| system | DONE | 4 | 0 | 4 | 15.0-15.4 s | none | reviewer R1-R7 green |
| files | DONE | 4 | 0 | 4 | 12.6-18.6 s | none | reviewer R1-R7 green |
| folders | DONE | 4 | 0 | 4 | 4.5-4.6 s | none | reviewer R1-R7 green |
| memory | DONE | 4 | 0 | 4 | 12.5-12.8 s | none | reviewer R1-R7 green |
| chat | DONE | 7 | 1 (FIX-2 honesty guard) | 7 | 45.1-53.5 s | 9.8 GB shared | FIX-2 found by reviewer R2 bait |
| owner | DONE | 11 | 2 (FIX-1 doctor budget; stale primary fallback) | 3 | ~262 s avg | none pinned | needs `--timeout 300`; earlier 90 s flap was harness config, not a test bug |
| weather | DONE | 4 | 0 | 3 | 30.0 s | none | batch 2; SearXNG-down fallback wait included |
| bayes | DONE | 4 | 0 | 3 | 4.5 s | none | batch 2 |
| chat_digest | DONE | 4 | 0 | 3 | 7.8 s | 10.0 GB shared | batch 2; curated 17-test suite also green |
| web_search | DONE | 5 | 2 (FIX-3 harness UTF-8 decode; FIX-4 cascade budget) | 3 | ~358-376 s suite | none pinned | `--timeout 300`; per-turn search phase bounded 25 s |
| web_fetch / reminders / apps / video | in progress | - | - | - | - | - | batch 2 next |
