# ISHTI Verified Audit Findings & Issues

Status legend: **VERIFIED-FIXED** = fix confirmed in code and by a self-check/test on 2026-09-27.

## Severity Ranking
- **P0 / Blocker**: Immediate crash, uncaught exception, or severe data loss/hang risk.
- **P1 / High**: Feature failure or contract violation when invoked outside expected narrow parameters.
- **P2 / Medium**: Degradation in performance, timeout sensitivity, or missing fallbacks.
- **P3 / Low**: Cosmetic warning, uncalibrated confidence warning, or minor encoding warning.

---

### Issue 33: Shut down showed the overlay but the app window stayed open — VERIFIED-FIXED (found 2026-09-30)
- **Severity**: P0 / Blocker (the owner's #1 complaint: "shut down did not close the app")
- **Component**: `desktop/app_window.py`, `desktop/launcher.py`, `ishti/lifecycle.py`
- **Evidence**: measured timeline - shutdown clicked, overlay reached 100% "Goodbye", backend and tray
  exited, port released, but the window stayed open for the full 60 s observation (and again in a second
  run). Third run caught the cause: the surviving window was **"ISHTI (Not Responding)"** - a wedged GUI
  thread never runs the in-process watcher threads, so JS->Python close calls and Python watchers cannot
  be trusted inside that process.
- **Fix (three layers)**: (1) the backend writes `run/shutting_down.flag` the moment shutdown begins and
  the desktop watcher closes the shell on seeing it; (2) the launcher registers its own + the tray's pids
  in the run registry, and the shutdown sequence calls `stop_shell()` - an OS-level TerminateProcess by
  recorded pid + creation time that needs no cooperation from a wedged window and never touches the
  backend itself; (3) hard-exit timers armed before any blocking destroy call as last resort.
- **Verified 2026-09-30**: exe -> ready -> Shut down -> **windows 1 -> 0 and processes 6 -> 0 within 8 s**,
  stable at zero for 40 s, port released. Tests: `tests/test_backend_watch.py` (8 tests incl. the
  flag handshake and the OS-level shell close).

### Issue 34: shutdown / startup screens spoke programmer language in the wrong color — VERIFIED-FIXED (2026-09-30)
- **Severity**: P2 / Medium (owner request: "wordings normal people" + "primary color is another but is used yellow")
- **Evidence**: screenshot 2026-09-29 - shutdown overlay said "Releasing RTX 5070 Ti VRAM and stopping
  services...", "Safe exit · Zero orphaned processes" in amber/yellow (not the primary accent color).
- **Fix**: backend step labels and the overlay now use plain words ("Saving your conversations and files",
  "Resting the AI", "All done. Goodbye!", "Everything is saved - see you soon!"); the overlay and the
  model-switch toast use the primary (accent) color; the exit dialog no longer says "Clean GPU release";
  splash errors no longer say "assistant backend". Locked by `tests/test_shutdown_ux.py` (jargon word-list
  test over every shutdown label).

### Issue 35: "Stop ISHTI" desktop shortcut removed — DONE (owner request 2026-09-30)
- **Owner**: "the stop ishti is there also on desktop not need of that - clicking the app close or shut
  down should properly close or stop the ishti". `desktop/create_shortcut.py` now creates only Start
  shortcuts and **removes** any stale `Stop ISHTI.lnk` (executed: removed from the owner's desktop).
  Stopping belongs inside the app (Shut down button / X) - which Issue 33's fix makes reliable.

### Issue 31: clicking ISHTI.exe showed a Windows error instead of the app — VERIFIED-FIXED (found 2026-09-29)
- **Severity**: P0 / Blocker (the app could not be started from the desktop at all)
- **Component**: `dist\ISHTI\ISHTI.exe` (PyInstaller build of `desktop/launcher.py`), `ISHTI.spec`
- **Evidence**: launching the exe captured `Traceback ... File "launcher.py", line 25 ... import json ->
  ModuleNotFoundError: No module named 'json'`; `tasklist` showed a zombie ISHTI.exe, no window; `_internal`
  contained only 18 files (no webview/pythonnet/requests), so the exe imported raw repo modules that need
  stdlib/3rd-party modules it never bundled. The prior `urllib.request` hiddenimport (4b9346d) was symptom
  patching, not a fix.
- **Fix**: the exe is now a tiny stdlib-only bootstrap, `desktop/exe_bootstrap.py` (entry in `ISHTI.spec`),
  which hands off to the one real launcher (`desktop/launcher.py`) under `venv\Scripts\pythonw.exe` and
  exits; readable error dialog when the venv or launcher is missing. `tests/test_desktop_bootstrap.py`
  AST-locks the stdlib-only rule so the exe cannot silently rot again.
- **Verified 2026-09-29**: exe -> ready ~20 s -> window -> `/api/chat` answered `PONG` -> external stop ->
  0 leftover processes, port released, DB integrity ok.

### Issue 32: desktop processes orphaned after an external stop ("Stop is unreliable") — VERIFIED-FIXED (found 2026-09-29)
- **Severity**: P1 / High (orphan pile-up: window on a dead backend, tray icons accumulated per launch)
- **Component**: `desktop/launcher.py`, `desktop/tray.py`, `desktop/app_window.py`
- **Evidence**: after `POST /api/shutdown` the backend and watchdog exited correctly, but 8 stale
  `launcher.py`/`tray.py` processes from earlier launches remained (measured via Win32_Process CommandLine
  + CreationDate); each new launch added another pair around whichever backend won the port race.
- **Fix**: `desktop/backend_watch.py` - every desktop process that outlives the backend watches
  `/api/health` and exits once the backend has been up and then stays gone >25 s (a watchdog restart dips
  health for seconds, not minutes; a backend that never comes up does not fire - the splash owns that case).
- **Verified 2026-09-29**: clean cycle exe -> ready -> external stop -> **0** ISHTI processes left
  (venv pythonw trampoline doubling accounted for), listener released. Tests: `tests/test_backend_watch.py`.

### Issue 1: `automation.skill.run` crashes with TypeError on mismatched input — VERIFIED-FIXED
- **Severity**: P1 / High
- **Component**: `ishti/skills/automation/skill.py`
- **Location**: Line 20-21
- **Root Cause**:
  In `ishti/skills/automation/skill.py`:
  ```python
  m = RULE.match(text)
  cond = parse_condition(m["cond"])
  ```
  If `RULE.match(text)` returns `None` (for example, if routed by semantic similarity or direct invocation with text not starting with "when/if"), `m["cond"]` raises `TypeError: 'NoneType' object is not subscriptable`.
- **Reproduction**:
  ```python
  from ishti.skills.automation.skill import run
  run("unmatched prompt", {})
  # Raises: TypeError: 'NoneType' object is not subscriptable
  ```
- **Fix**: Check `if not m: return None` before indexing `m["cond"]`.
- **Verified 2026-09-27**: `python -m ishti.skills.automation.skill` self-check passes; `run("unmatched prompt", {})` returns `None`.

---

### Issue 2: `extract.skill.run` crashes with AttributeError on missing URL — VERIFIED-FIXED
- **Severity**: P1 / High
- **Component**: `ishti/skills/extract/skill.py`
- **Location**: Line 24
- **Root Cause**:
  ```python
  m = URL.search(text)
  url = m.group(0).rstrip(".,)")
  ```
  If `text` does not contain a regex-matched URL, `URL.search(text)` returns `None`, causing `m.group(0)` to raise `AttributeError: 'NoneType' object has no attribute 'group'`.
- **Reproduction**:
  ```python
  from ishti.skills.extract.skill import run
  run("extract product data", {})
  # Raises: AttributeError: 'NoneType' object has no attribute 'group'
  ```
- **Fix**: Guard with `if not m: return None`.
- **Verified 2026-09-27**: `python -m ishti.skills.extract.skill` self-check passes; `match()` also requires a URL.

---

### Issue 3: `web_fetch.skill.run` crashes with AttributeError on missing URL — VERIFIED-FIXED
- **Severity**: P1 / High
- **Component**: `ishti/skills/web_fetch/skill.py`
- **Location**: Line 20
- **Root Cause**:
  ```python
  found = URL.search(text).group(0).rstrip(".,;")
  ```
  Calling `.group(0)` directly on `URL.search(text)` without checking for `None` causes an uncaught `AttributeError` when dispatched without a full URL in the query string.
- **Reproduction**:
  ```python
  from ishti.skills.web_fetch.skill import run
  run("fetch information about python", {})
  # Raises: AttributeError: 'NoneType' object has no attribute 'group'
  ```
- **Fix**: Guard with `m = URL.search(text); if not m: return None; found = m.group(0).rstrip(".,;")`.
- **Verified 2026-09-27**: `python -m ishti.skills.web_fetch.skill` self-check passes.

---

### Issue 4: Console UnicodeEncodeError on Windows CP1252 codepage — VERIFIED-FIXED
- **Severity**: P2 / Medium
- **Component**: CLI / Logging / Console output
- **Location**: Multiple skills logging Unicode arrows (`->`) or em-dashes to stdout without stream reconfiguration.
- **Root Cause**:
  Windows console defaults to `cp1252` encoding. Printing characters like `\u2192` directly causes:
  `UnicodeEncodeError: 'charmap' codec can't encode character '\u2192' in position ...`
- **Reproduction**:
  Running scripts in standard Windows cmd/powershell without `sys.stdout.reconfigure(encoding='utf-8')` throws `UnicodeEncodeError`.
- **Fix**: Ensure `sys.stdout.reconfigure(encoding="utf-8")` is placed at entry points or replace non-ASCII characters in log/trace strings with ASCII equivalents (`->`).
- **Fix in place 2026-09-27**: one-time UTF-8 bootstrap in `ishti/__init__.py` (the central import) reconfigures `sys.stdout`/`sys.stderr` with `encoding="utf-8", errors="replace"` - covers every entry path (`python -m ishti`, `orchestrator/startup.py`, skill self-checks, tests, voice) without touching files that reconfigure themselves.
- **Verified 2026-09-27**: under a cp1252 parent console, `import ishti` switches `sys.stdout.encoding` to utf-8 and Urdu/arrow output prints correctly.

---

### Issue 5: Laya Checkpoint Temperature Calibration Warning — VERIFIED-FIXED
- **Severity**: P3 / Low
- **Component**: `ishti/router.py` / `laya/router.py`
- **Location**: `laya/router.py:260`
- **Root Cause**:
  `RuntimeWarning: laya: this checkpoint ships invalid temperatures or values outside [0.5, 5]; using choice:11+=0.10058280825614929 -> 0.5. Treat confidence from the affected entries as uncalibrated.`
- **Impact**: Emits a noisy runtime warning to stderr on every router initialization. Functional routing still succeeds.
- **Fix**: Suppress or filter the specific `RuntimeWarning` from `laya.router` in `ishti/router.py`.
- **Fix in place 2026-09-27**: `ishti/router.py` filters exactly that message once at import, in the main thread (a `catch_warnings()` context was rejected: not thread-safe, and routing runs in parallel plan steps).
- **Verified 2026-09-27**: `ishti.router.laya()` loads with no warning on stderr; routing eval still 143/143.

### Issue 6: `desktop` volume op crashed when the audio device vanished mid-call — VERIFIED-FIXED (found 2026-09-27)
- **Severity**: P1 / High (uncaught COMError reached the owner)
- **Component**: `ishti/skills/desktop/skill.py` `op_volume` (also reached via the reflex fast path)
- **Root cause**: only `_endpoint()` was guarded; `GetMasterVolumeLevelScalar()` on the returned object can raise the same COMError when Windows loses the audio endpoint ("volume up" at 06:48, logged in NEEDS.md).
- **Fix in place 2026-09-27**: the current-level read moved inside the try; COMError / "Element not found" from any of it now returns a plain "no audio output device connected" message instead of crashing.
- **Verified 2026-09-27**: `python -m ishti.skills.desktop.skill` self-check passes.

### Issue 7: DeprecationWarnings (invalid escape sequences) — VERIFIED-FIXED (found 2026-09-27)
- **Severity**: P3 / Low
- **Component**: `ishti/planner.py`, `tests/test_dispatcher_followups.py`, `tests/test_planner_messenger.py` - docstrings with `D:\ISHTI` in non-raw strings.
- **Fix in place 2026-09-27**: affected docstrings are raw strings now.
- **Verified 2026-09-27**: `py_compile(..., doraise=True)` under `-W error::DeprecationWarning` passes for every changed file; all three test scripts still pass.

### Issue 8: the process FAILED FAST (0xC0000409) at exit after any real voice session — FIXED (found 2026-09-27, deep audit)
- **Severity**: P0 / Blocker (every quit, Stop, watchdog restart and self-check looked like a crash to Windows)
- **Component**: `ishti/voice.py` (whisper decode threading), affecting `ishti/wake.py`, `ishti/diarize.py`,
  `ishti/skills/planners/skill.py`, `ishti/skills/video/pro.py`, `desktop/shutdown.py`'s clean-exit claim
- **Symptom**: `python -m ishti.wake` printed its `[self-check] wake OK` line and then exited with `-1073740791`
  (= `0xC0000409`, STATUS_STACK_BUFFER_OVERRUN / fail-fast) — bash hid this as exit code 127. Users saw the same
  thing: 30 `python.exe` `0xc0000409` "Application Error" records in the Windows event log for today alone.
- **Root cause** (found by bisecting the self-check with a line-limited tracer, then minimal repros): a CTranslate2
  model must be decoded by ONE thread. Decoding the same `WhisperModel` from two different threads (the live
  partials ran on their own worker thread, the final decode on the command thread — i.e. every real voice turn)
  leaves the native library in a state that throws during static destruction at process exit, which the C++ runtime
  turns into `terminate()` -> `abort()` -> fail-fast in `ucrtbase.dll`. Measured matrix (real models, this PC):
  background decode + main-thread decode = crash; two worker threads only = clean; main thread only = clean;
  model load without a decode = clean; the whisper-tiny CPU spotter never mattered.
- **Fix**: `voice.stt_call(fn)` — one dedicated STT thread runs every decode (transcribe, `Stream.partial`,
  `pick_language`/detect_language, the warm-ups, the idle unload, and the model drop in Settings); callers wait for
  the result and exceptions cross the queue. Loading happens outside `_load_lock` so a decode can never wait on the
  lock that the loader holds (that would have deadlocked). The three call sites that decoded directly
  (`diarize.segments`, `planners` YouTube lecture notes, `video/pro.captions`) now go through it too, and
  `manage.py` frees the model with `voice.release_model()` instead of assigning `_stt = None` from another thread.
- **Verified 2026-09-27**: `tests/test_stt_threads.py` (new) asserts one decode thread, exception propagation,
  nested (inline) calls, and that `python -m ishti.wake` / `-m ishti.voice` exit **0**. Before/after benchmark in one
  process on the same audio: raw decode inline 115 ms vs via `stt_call` 114 ms (queue hop within noise, full
  `transcribe()` 116 ms), so the fix costs no latency.

### Issue 9: two code questions were routed to the wrong skill by Laya — FIXED (found 2026-09-27)
- **Severity**: P2 / Medium (a wrong, confidently-worded answer instead of the coder model)
- **Component**: `ishti/skills/chat/skill.py` (`match`), `ishti/router.py` (rule -> Laya order)
- **Details**: `ishti.check`'s labelled set reported 151/153; the misses were
  `"write a python function to reverse a linked list" -> apps` and `"how do I fix a segfault in C" -> capcut`
  (both `source=laya`). The chat skill already had deterministic fallback rules for exactly this class of Laya
  failure (arithmetic, creative writing); a `CODE` rule was added at priority 90 (chat's priority, evaluated last,
  so dev/skillsmith/docs rules still win) covering language names, compiler/segfault/traceback words and
  "write|fix|debug|explain ... function|code|script".
- **Verified 2026-09-27**: routing eval **153/153, median 0.2 ms**; guard cases re-checked by hand — "run the tests"
  -> dev, "create a skill that tells a random quote" -> skillsmith, "make a presentation about solar energy" -> docs,
  "open vs code" -> apps, "trim the video ..." -> video, "find the invoice pdf" -> files.

### Issue 10: Settings could not see a locally-installed whisper model — FIXED (found 2026-09-27)
- **Severity**: P2 / Medium (wrong model state in the UI; "delete the model in use" guard was bypassable)
- **Component**: `ishti/manage.py` (`whisper_state`, `set_whisper`, `delete_whisper`)
- **Root cause**: the configured `whisper_model` is a *directory*
  (`D:\ISHTI\data\models\whisper-large-v3-turbo`, what `tests/download_whisper_turbo.py` installs and what
  `voice.model_source()` loads), but `whisper_state()` compared it to the option *names*, so it reported
  `selected=False` for everything while the turbo model was in use — `ishti.manage`'s self-check assertion
  (`exactly one selected and installed`) failed on this machine, and `delete_whisper` no longer refused to delete
  the model in use.
- **Fix**: `selected_whisper()` recognises both forms (longest-name match on the directory basename, so
  `large-v3-turbo` wins over `large-v3`), `_model_dir()` points at the directory that is really in use,
  `set_whisper()` re-uses an existing local build instead of re-downloading by name, and `delete_whisper()` refuses
  for whichever form is in use.
- **Verified 2026-09-27**: `python -m ishti.manage` self-check now also simulates a local build (temp dir with
  `model.bin`) and asserts it is reported selected+installed and cannot be deleted; passes with exit 0.

### Issue 11: a partial client name could resolve to nothing while a duplicate profile could be created on send — FIXED (found 2026-09-27, deep audit)
- **Severity**: P2 / Medium (adversarial client-isolation pass; no live-data leak, but the safe answers were
  unhelpful and the send path could fork a profile)
- **Component**: `ishti/skills/clients/skill.py` (`_client`, `op_send`), `tests/test_client_isolation.py` (new)
- **Details**: `_client(name)` was exact-lowercase-match only, so `grounding_for("Aliza")` (client "Aliza Khan")
  returned `None` — safe but useless. Worse, `op_send` always did `_client(who, create=True)`: send the draft to a
  partial name and it **created a second profile** (`aliza`) instead of logging the send against the profile the
  draft was grounded in. Deliberately NOT made fuzzy: an ambiguous prefix must never be guessed between.
- **Fix**: `_resolve(name)` — exact match wins; failing that a **UNIQUE** name prefix resolves (len >= 3,
  one-directional `stored.startswith(query)`, so a query more specific than what is stored can never pull in a
  shorter client's rows); unknown OR ambiguous -> `None` and the ops answer "No client named 'X'. Clients I have:
  …". Lookups go through it; **creation stays exact** ("new client Ali" still makes a new profile when "Ali Khan"
  exists); `op_send` resolves the same way the draft did before logging the sent event.
- **Verified 2026-09-27**: `tests/test_client_isolation.py` (new) — confusable names Ali / Aliza Khan / Aliza (no
  rows, names, amounts cross over), a **second Aliza** created so the prefix is ambiguous: grounding/context/draft
  all refuse and list known clients, nothing leaks; private family facts (kin + surgery) stay out of both the
  grounding and `rgraph.export("client")`; a private (credit-card) event exists only with `allow_private=True`;
  owner preferences still pass through the export; and a sent draft lands in the right profile with no stray
  duplicate. Skill self-check extended with the ambiguity + unknown-name cases. Both green.

### Performance note (Issue 8's fix is free) — measured 2026-09-27
- Same process, same audio, real turbo model: raw inline decode **115 ms** vs decode via `stt_call` **114 ms**
  (queue hop inside noise); full `transcribe()` **116 ms**. The single-decode-thread rule costs nothing.
- Live partials were already gated behind the final decode (`_final_active`), so serialization changes no
  perceived latency; the partial worker + command-thread decode pattern it replaced is precisely the matrix
  cell that failed fast (0xC0000409) at exit.
- `tools/verify_all.py`: **82/82 passed in 278 s** (final, incl. `test_multitopic`, `test_stt_threads`,
  `test_client_isolation`, `test_capcut_skill`); `tools/check_skills.py` 46 skills / 122 assertions / 153
  labelled requests, OK; `tools/check_libraries.py` 36 call sites, OK.

### Sections 1–9 pass (performance, voice, mic, Obscura, skills) — 2026-09-28
- **Section 7 built new**: `ishti/mic_control.py` — global mic default OFF, persisted, voice commands,
  `/api/mic/*`, tray icon (`desktop/tray.py`), killswitch/shutdown force OFF, Live closes on mic-off.
  Self-check: default OFF, persistence, commands, stop hooks — all green.
- **Section 8 verified + wired**: Obscura 0.2.3 (Apache-2.0) downloaded to `tools/obscura/`, measured on this
  machine (fetch markdown 0.17 s; scrape 2 URLs/10 workers 1.15 s; CDP serve OK). `web_fetch.read` and
  `deep_research._fetch_one` now default to `ishti/obscura.py` (`--obey-robots`, markdown, no stealth; blocked
  pages reported as cannot-read). Self-check green.
- **Sections 1/2/5/6/3/4 measured & verified** (see `MODEL_DECISIONS.md`, `PERF_LOG.md`, NEEDS 19).
- **New skills** (all with self-checks, routing-safe): `screen_control` (T2), `screen_understand` (T0),
  `notes`, `writing`, `study`, `budget`, `habits` — plus `ishti/skill_lifecycle.py` (discovery/practice/
  per-skill memory/refine/retire). Routing eval stayed **157/157** (three collisions found by check_skills and
  fixed: "I spent 500 on groceries" stays ledger's, "draft an email to my professor" stays chat's,
  "make 10 flashcards" stays planners'; new skills set `laya: false` so Laya can't send chat cases to them).
- **Final**: `tools/verify_all.py` **91/91 passed (284 s)**; check_skills **54 skills / 136 assertions OK**;
  `ishti.check` **157/157, median 0.2 ms**.

### Closing the four NEEDS gaps — 2026-09-28 (audit finalised)
- **NEEDS 17 (legacy STT engine) — DONE.** `voice/stt.py` (the second decode path behind Issue 8's fail-fast) is
  deleted; `tests/test_stt_accuracy.py` now exercises the one production path (`ishti.voice`) and still verifies
  EN/UR accuracy, latency, real-audio transcription, Pashto tokenisation and language folding.
- **NEEDS 18 (no trade execution) — CLOSED as PAPER-ONLY.** New skill `ishti/skills/trading` (T2): paper buy/sell,
  close, account, history, reset on a SQLite ledger; orders T2-gated via the standard `ask_first` gate with
  cash/position validation BEFORE the gate; routing requires the word "paper" (priority 6); connecting a real/live
  broker is refused in code with the T3 escalation explained — never attempted, never LLM-improvised. Verified
  end-to-end on the owner's live app: UI gate -> yes -> fill (21 ms) -> ledger row -> account read-out -> refusal
  -> gated reset. Routing eval now **157/157** (a Laya wobble on "thanks, that helped" was fixed with the chat
  BACKCHANNEL rule, same class as Issue 9).
- **NEEDS 16 (mic evidence) — MEASURED.** `tools/probe_audio_devices.py` (+ `data/audio_devices.json`): the only
  inputs are Bluetooth Hands-Free endpoints and both fail to open (`PortAudioError -9999`), so no real-mic
  barge-in is possible on this PC right now — recorded as evidence, not assumption.
- **NEEDS 15 (UI click-through) — DONE on the owner's live instance (untouched).** Settings shows Whisper
  `large-v3-turbo` (Issue 10 fix in the real UI); Skills page shows 47 skills; the trading T2 gate renders with
  Go ahead/Cancel and the activity graph; Live mode without a mic degrades cleanly ("STOPPED — Microphone
  unavailable" + End Live, no crash).
- **Test hygiene found by the final run:** `test_lifecycle` asserted "ISHTI is not running" and failed whenever
  the owner simply had ISHTI open — it now skips the not-running-only checks (never touching the live instance)
  and exits 0. `tools/verify_all.py` SLOW set also learned the three new tests, so `--quick` can no longer skip
  the Issue 8 regression by accident.
- **Final:** `tools/verify_all.py` **84/84 passed (272 s)**; `tools/check_skills.py` **47 skills / 123 assertions,
  OK**; `tools/check_libraries.py` 36 call sites, OK; `ishti.check` **157/157, median 0.2 ms**.

### Verification artefacts added by this pass (2026-09-27)
- `tools/verify_all.py` — runs every self-check (core modules, all skills), every `tests/test_*.py` and `ishti.check`
  in one pass, and inspects the **raw** exit code so a fail-fast cannot hide behind a passing self-check.
- `tests/test_stt_threads.py` — the regression guard for Issue 8.
- `tests/test_multitopic.py` — 4 simultaneous topics in one conversation (answers, history, task tracker, a failing
  skill contained, and a T2 confirmation kept to its own topic).

### Regression evidence (2026-09-27)
- `ishti.check` full end-to-end: core loop OK (memory, weather, LLM, plan, situation layer, remote auth).
- Laya routing eval: **153/153 correct, median 0.2 ms** (was 143/143 when the queued skills were absent, then
  151/153 before Issue 9).
- 47/47 skill folders load, 0 broken; all module self-checks pass (including the new skills: `focus`, `speedtest`,
  `braintrain`, `clients`, `relationships`, and a `capcut` skill added concurrently).
- Standalone tests pass: `test_browser_tabs`, `test_dispatcher_followups`, `test_planner_messenger`,
  `test_lifecycle`, `test_whatsapp_context`, `test_voice_note_generation`, `test_stt_accuracy`, `test_stt_threads`,
  `test_multitopic`.
- `tools/verify_all.py`: **82/82 passed in ~5 minutes** (final run; includes the new isolation, multitopic and
  STT-thread tests).

---

## Backend pass: VRAM governor + lifecycle (2026-09-29, "Prompt 1")

Everything below was found by running it. Evidence: `TEST_RESULTS.md`, `data/e2e_vram_lifecycle.json`,
`data/vram_calibration.json`. Status words: FIXED (code + a check that fails if it regresses), OPEN (needs the owner or a later pass).

### Issue 12: Laya (the router) is on the GPU, contradicting MODEL_DECISIONS.md — FIXED (record), OPEN (choice)
`ishti/router.py` builds `Router(device="cuda")` in fp16. Measured: **~1.5 GB resident in the ISHTI process, 3.8 GB peak while loading**
(it loads fp32 first). The 28 Sep decision text said "CPU, near-zero VRAM". The governor now counts it (all VRAM users are
measured from nvidia-smi) and the boot loads the planner only after Laya finished. Moving Laya to the CPU would free ~1.3 GB at
the cost of routing latency: not changed, owner's call (NEEDS.md).

### Issue 13: `gpu.slot()` ran the job "anyway" after its wait — FIXED
The old slot waited 60 s for free VRAM and then proceeded regardless: that is how Real-ESRGAN once made Ollama fail its
allocation (27 Sep 01:30) and how a model ends up split between GPU and CPU. `slot()` now delegates to `vram.reserve()`
(idle models out, planner a rung down) and raises `vram.GpuBusy` with the reason instead of running.

### Issue 14: Whisper's VRAM cost was 2.5x what the code assumed — FIXED
`voice.stt_model` reserved 1200 MiB; measured growth **3047 MiB**. The planner sat on top of it at 84% VRAM. Seed is now 3000 MiB and
`gpu.slot` records the real growth of every job it wraps (`job_footprints`) for next time.

### Issue 15: Ollama's `/api/ps` size under-reports vision models — FIXED
`gemma3:4b` 2742 vs 3857 MiB really used, `gemma3:12b` 7672 vs 8973 (the vision projector). Footprints now store the GPU's real
growth (`gpu_delta`), and both loading and unloading arithmetic use the larger number.

### Issue 16: a failed `/api/ps` read counted as "nothing is loaded" — FIXED
When Ollama was busy the governor saw an empty list, double-counted the resident planner and chose a lighter one (found because
`test_multitask_voice_session` reported the planner as the 3B). Decision paths now raise (or reuse a <30 s old good reading).

### Issue 17: architecture-based KV estimates over-credit context reductions — FIXED
Predicted 770 MiB saved for `gemma3:12b` 4k→2k; measured 2 MiB (sliding-window attention). A context step is now only taken
when that exact context was measured and saves ≥ 128 MiB; scaling from another context never assumes a saving.

### Issue 18: the watchdog killed the server the moment `stop.flag` appeared — FIXED
That skipped the data flush. It now asks for the graceful shutdown (`POST /api/shutdown`) and only kills after 35 s.
It also ticks every second now (was 10 s), so a stop or a crash is noticed at once.

### Issue 19: three competing start/stop paths — FIXED (deleted)
`desktop/shutdown.py` killed **any Python process whose command line contained "ishti"**, `orchestrator/startup.py` started a second
Ollama and pinged the model outside any governor, `llm._create` started Ollama with its own `Popen`. Now: one graceful shutdown
(`ishti/lifecycle.py`), one Ollama start (`lifecycle.start_ollama`, hidden, in the Job Object, remembered as ours), and the
stop client only kills pids ISHTI recorded (`%LOCALAPPDATA%\ISHTI\run\ishti-<port>.json`) - never by process name.
`tests/test_lifecycle.py` (which pinned the old code layout) was updated to the new design.

### Issue 20: reading state created models — FIXED
`GET /api/vram` and the first boot created `*-c8k:*` alias models as a side effect. Aliases are now created only right before a
load. (Three aliases exist from before the fix: `qwen2.5-c8k:7b`, `qwen2.5-c8k:3b`, `ishti-qwen2.5-c4k:14b`; the last was made
by the bug and is unused - `ollama rm ishti-qwen2.5-c4k:14b` is safe, it shares the 14B's weights. It was left because the rule
is not to touch `ishti-*` models.)

### Issue 21: `config.json` names a planner that is not installed — OPEN (owner)
`llm.models.planner = "qwen3:30b-a3b"` (and coder `qwen3-coder:30b-a3b`): not installed, 19 GB, rejected in MODEL_DECISIONS.md.
`llm.model_for` falls back silently to `ishti-qwen2.5:14b`, and the health check reports it as "using fallback". The ladder now
governs what actually runs; the config value should be changed to `ishti-qwen2.5:14b` (not done: `config.json` has other
uncommitted edits from another session).

### Issue 22: a stale 18.5 GB partial download sits in the Ollama store — OPEN (owner)
`~/.ollama/models/blobs/sha256-58574f...-partial` (18.5 GB, dated 26 Sep 22:39), left by an interrupted pull of something
larger than any installed model. Ollama removes such files at its next start unless `OLLAMA_NOPRUNE` is set; ISHTI did not touch it.

### Issue 23: `orchestrator/model_router.py` (deep research) still calls `/api/generate` directly — OPEN
It goes through `models.used()` but not through `vram.admit()`. Deep research uses the configured model names, which the ladder
already keeps loaded, so it has not caused a violation; the sampler's partial-offload watch is the safety net. Routing it through
the governor is the next step.

### Issue 24: the `qwen2.5:7b` planner rung fails the Urdu prompt — OPEN (by design, visible)
Any planner downshift to the 7B (voice sessions, heavy vision) loses Urdu quality on my test prompt (the 3B passed it, the check is
crude: it only looks for Arabic script). The `model_switch` reason tells the user it switched; the quality question needs your
own Urdu prompts (NEEDS.md).

### Issue 25: WebSocket `/ws` in `interface/web_ui/server.py` was not subscribing to event bus — VERIFIED-FIXED
- **Severity**: P1 / High
- **Component**: `interface/web_ui/server.py`
- **Location**: Line 280 (`@app.websocket("/ws")`)
- **Root Cause**: The API contract (`docs/API_CONTRACT.md`) specifies that `/ws` mirrors `/ws/events`. However, `/ws` only handled chat streaming and was missing subscription to `ishti.events.subscribe()`, meaning frontend clients connecting to `/ws` received zero system events (`boot_stage`, `vram_status`, `model_switch`, `shutdown_progress`).
- **Fix**: Added queue subscription to `events.subscribe()` in `/ws` endpoint with event replay and async forwarding.
- **Verified 2026-09-29**: `tests/contract_test.py` validates that both `/ws` and `/ws/events` receive all broadcast events without duplication.

### Issue 26: `is_pinned()` in `ishti/vram.py` protected all ladder rungs of a role — VERIFIED-FIXED
- **Severity**: P1 / High
- **Component**: `ishti/vram.py`
- **Location**: Line 228
- **Root Cause**: `is_pinned(name)` returned True if `name` matched ANY rung of a pinned role's ladder (e.g. `ishti-qwen2.5:14b`, `qwen2.5:7b`, and `qwen2.5:3b`). When the governor downshifted from 14B to 7B or admitted an on-demand model, the previous rung could not be evicted from VRAM because `is_pinned` considered all rungs permanently pinned.
- **Fix**: Updated `is_pinned()` to only protect the currently effective model (`_eff.get(role) or rs[k]["name"]`).
- **Verified 2026-09-29**: `tests/test_vram_governor_pressure.py` verified that idle rungs are cleanly evicted under pressure and heavy models (`gemma3:12b`) load with 100% VRAM allocation.

### Issue 27: `tools/verify_all.py` crashed on Windows CP1252 codepage — VERIFIED-FIXED
- **Severity**: P2 / Medium
- **Component**: `tools/verify_all.py`
- **Location**: Line 67
- **Root Cause**: Windows command prompt defaults to CP1252 encoding. Test output strings containing unicode arrow characters (`\u2192`) raised uncaught `UnicodeEncodeError: 'charmap' codec can't encode character '\u2192'`.
- **Fix**: Added `sys.stdout.reconfigure(encoding="utf-8", errors="replace")` and `sys.stderr.reconfigure(encoding="utf-8", errors="replace")`.
- **Verified 2026-09-29**: `verify_all.py` runs cleanly without unicode encoding crashes on Windows 11.

### Issue 28: `desktop/splash.html` scale mismatch and premature completion — VERIFIED-FIXED
- **Severity**: P2 / Medium
- **Component**: `desktop/splash.html`
- **Location**: Lines 430-445
- **Root Cause**: Backend emits progress as integer 0-100, while contract noted 0.0-1.0. A condition `progress >= 1.0 && progress <= 1.0` triggered premature splash completion when the backend reached 1% progress (`progress: 1`). Furthermore, `stages` list could arrive as either `string[]` or `object[]` (`[{id, label, done}]`).
- **Fix**: Normalized progress calculation (`pct = progress > 1 ? progress : progress * 100`) and added safe object/string label extraction.
- **Verified 2026-09-29**: Splash screen displays stages and progress monotonically without premature completion.

### Issue 29: Desktop launcher spawned terminal banner and skipped splash screen — VERIFIED-FIXED
- **Severity**: P1 / High
- **Component**: `desktop/launcher.py`, `desktop/app_window.py`
- **Location**: `desktop/launcher.py:61`, `desktop/app_window.py:85`
- **Root Cause**: `desktop/launcher.py` was invoking `orchestrator/startup.py`, which printed a terminal banner and opened Chrome directly, completely bypassing `desktop/splash.html`. Furthermore, `desktop/app_window.py` was missing `SplashApi` and `MainApi` bridges (`on_ready`, `view_log`, `retry_boot`, `minimize_to_tray`, `force_close`, `show_window`).
- **Fix**: Replaced `startup.py` call with headless `python -m ishti 8888` (`CREATE_NO_WINDOW`), added `SplashApi` and `MainApi` bridges in `desktop/app_window.py`, and compiled native binary `dist\ISHTI\ISHTI.exe` with PyInstaller `--onedir --noconsole`.
- **Verified 2026-09-29**: Double-clicking `dist\ISHTI\ISHTI.exe` or desktop shortcut pops up splash screen instantly with zero terminal windows.

### Issue 30: Chat UI lacks frontend file attachment / upload picker — OPEN (Documented in NEEDS.md)
- **Severity**: P2 / Medium
- **Component**: `interface/app/src/sections/Chat.tsx`
- **Location**: `Chat.tsx` input toolbar
- **Root Cause**: Chat interface currently accepts only text inputs. Users must type raw local paths (e.g., "summarize D:\ISHTI\data\test.pdf") for document and image processing skills. No multipart form upload endpoint or visual attachment picker is present.
- **Fix**: Add a paperclip / upload icon in `Chat.tsx`, a drag-and-drop file target, and a backend `POST /api/upload` endpoint saving to `data/uploads/`.


### Unrelated failures seen in the offline suite (not from this work)
`tests/test_arch2_memory.py` (2), `test_learning.py`, `test_learning_wiring.py`, `test_memory_prompt2.py`: memory/learning stores that
another session is editing right now (one assertion fails on a lesson persisted in the real store). No module touched here is
involved.

### Independent audit of this work (a read-only review agent, 24 findings) — status
Each claim was checked against the code before acting. **Fixed** unless marked.

| # | Finding | Status |
|---|---|---|
| 1 | upshift decided outside the governor lock: could unload the planner mid heavy job | FIXED: `upshift_tick` takes the lock without waiting (skips the tick if a load is running) and re-checks jobs/voice/fit inside it |
| 2 | a failed switch left NO planner loaded (heavier one already unloaded) | FIXED: `switch_role` decides inside the lock and reloads the previous model on failure |
| 3 | arch2's `main`/`validation` roles ignored the downshifted rung and took a lease | FIXED: they are the planner role in `model_for` and `_create` |
| 4 | a queued-job entry leaked on any exception, blocking upshift for good | FIXED: `try/finally` in `admit` and `reserve` |
| 5 | chat carried on ungoverned after the governor's load failed (HTTP 500/OOM) | FIXED: an HTTP error from the load now raises `GpuBusy`; only "Ollama unreachable"/governor bugs fail open |
| 6 | a new sampler thread per boot retry | FIXED: idempotent `start_sampler` |
| 7 | the governor lock is held across a 300 s load, delaying the sampler and a voice-priority reservation | ACCEPTED: bounded by real load times (2–9 s measured); the sampler's upshift no longer waits on it. Voice does not preempt a load already in progress |
| 8 | `gpu.slot` waited on its semaphore without a timeout | FIXED: bounded by `wait_s`, then `GpuBusy` |
| 9 | `/api/vram/admit` etc. stayed open during shutdown (prefix match); the governor could load after unload | FIXED: exact always-open list; `vram.stop()` at the start of shutdown makes admit/switch/upshift refuse |
| 10 | children of a console-less server flash console windows | FIXED: `Popen` without flags gets `CREATE_NO_WINDOW` |
| 11 | the Job Object would kill apps ISHTI opened for the owner (Chrome, Notepad) when it exits | FIXED and **verified** (an `os.startfile`d process did die with the job): job allows breakaway; `os.startfile`/`webbrowser` go through `cmd /c start` with breakaway; detached launches break away; the exit sweep skips them. Own children still die: tested |
| 12 | registry pids trusted by name: pid reuse could kill an unrelated `python.exe` | FIXED: creation times stored and compared; stale entries cleared at exit |
| 13 | any loaded model not marked busy was evicted as "idle", including other clients' models; busy was marked after admit | FIXED: only models ISHTI asked for are ever evicted (the embedding model never); busy is marked before admit |
| 14 | unchecked `unload()` results | FIXED: a failed unload aborts the plan / the upshift |
| 15 | leases without owner or expiry (a vanished UI client blocks upshift forever) | FIXED: `lease_ttl_s` (900 s) reaper |
| 16 | any web page in the owner's browser could POST `/api/shutdown`, `/api/ollama/apply`, `/api/vram/admit` to `127.0.0.1`, or open `/ws/events` | FIXED: those endpoints reject a foreign `Origin` or a non-loopback `Host` (DNS rebinding); the desktop shell (no Origin) and the app itself are unaffected; tested |
| 17 | a failed `/api/show` or `/api/tags` was cached for an hour/10 s as the answer | FIXED: failures are never cached |
| 18 | WAL checkpoint reported success when the WAL was busy | FIXED |
| 19 | contract said `stopped` and a voice-failure signal that the code didn't provide | FIXED: state becomes `stopped` before exit; `/api/ready` has `warnings[]` |
| 20 | a timed-out boot stage thread could start a second Ollama | FIXED: one start at a time under a lock, "already answering" wins |
| 21–22 | log stream not thread-safe; pending WebSocket `recv` never cancelled | FIXED |
| 23 | `/api/status` (async) blocked the event loop with Ollama calls | FIXED: plain `def` (thread pool) |
| 24 | shutdown deadline (25 s) shorter than the worst case of its steps | FIXED: 40 s (watchdog waits 50 s) |

---

# Session 2026-09-29 — Section 4 (24-hour chat digest) + chat-UI tables

Everything below was found **by running** the new code and the new test suite, not by reading it. Each fixed
issue has a named regression test in `tests/test_chat_digest.py`; the command that proves each row is in
`TEST_RESULTS.md`.

| ID | Area | Severity | Evidence | Root cause | Status |
|---|---|---|---|---|---|
| CD-01 | deadlines | High | `analyze._priority` ranked the owner's own "ok I'll send it today" (deadline today) above the client waiting for the invoice | the rubric had no notion of *whose* message a task was | VERIFIED-FIXED (`test_regression_owner_promise_never_outranks_someone_waiting`) |
| CD-02 | deadlines | Med | "by 30 Sep" produced no deadline at all - the commonest WhatsApp wording | `MONTHS` only held full month names, so `MONTHS["sep"]` missed | VERIFIED-FIXED (`test_regression_dates_are_not_guessed`) |
| CD-03 | stability | Critical | **every** Urdu message raised `IndexError: no such group` in `parse_deadline` | the Urdu branch of `REL_DAY` was non-capturing while the code read `group(2)` | VERIFIED-FIXED (`test_regression_urdu_relative_day_does_not_crash`) |
| CD-04 | correctness | Med | "The vendor contract clause 7.3 states applies" became a 7-March deadline | `DATE_NUM` matched any `d.d` | VERIFIED-FIXED (document-reference blacklist + date-context rule) |
| CD-05 | correctness | High | after CD-04's fix, "Assignment 3 submission **deadline** 02/10/2026" stopped being a date | the blacklist had no word boundary, so it matched the tail of "dead**line**" | VERIFIED-FIXED (same regression test) |
| CD-06 | coverage | Med | Pashto "سبا د پشاور ته ځو" produced no deadline | `سبا` was in `REL_MAP` but missing from `REL_DAY` - a dead entry | VERIFIED-FIXED (same regression test) |
| CD-07 | rubric | Med | the usefulness reason never said "deadline inside Nh" | `deadline_ts` was not copied into the action dict, so the rubric saw `None` | VERIFIED-FIXED (same regression test) |
| CD-08 | report quality | Med | one 220-message chat produced 21 identical action rows and a 3-page PDF | no de-duplication of identical (chat, text, deadline) | VERIFIED-FIXED (`test_regression_duplicate_actions_collapse_but_keep_sources`) |
| CD-09 | export | Med | the PDF/HTML export printed `**Window:**` with visible asterisks | `markdown_to_blocks` fed bullets to the renderer without inline parsing | VERIFIED-FIXED (`test_regression_html_keeps_bold_in_bullets`) |
| CD-10 | chat UI | High | the chat UI split a cell on an escaped `\|`, left short rows ragged, and had no sticky header, no copy-table button and no RTL | hand-rolled GFM in `interface/app/src/components/Markdown.tsx` | VERIFIED-FIXED (`DigestTable` + `splitRow`; `npm run build` green, bundle asserted in N8) |
| CD-11 | tooling | Low | `ruff check ishti` reports **1600** findings on the pre-existing tree and there is no ruff config anywhere in the repo, so lint is not a gate and was never run clean | no `ruff.toml`/`pyproject.toml` | **OPEN** - documented, not "fixed": 1600 pre-existing findings are out of this pass's scope (see NEEDS) |
| CD-12 | export | Low | `reportlab` is not in the venv, so the third PDF fallback cannot run | the prompt lists it as an optional fallback only | **OPEN** (optional; the Chromium/Edge path works - see N16) |
| CD-13 | chat UI | Med | the exported report has no "Open / Show in folder" button in the chat UI - the owner must be told the path | no UI work on the report actions | **OPEN** (NEEDS) |
| CD-14 | verification | Low | DOCX cannot be rendered to PDF for visual inspection: no LibreOffice and no Word automation in this venv/box | environment | **BLOCKED** (structural DOCX checks pass; visual DOCX render pending, see N18) |
| CD-15 | test harness | Med (corrected after further evidence) | Running the **standalone** script directly - `venv\Scripts\python tests\test_stt_threads.py` - blocks for the whole wake self-check, and on this PC that self-check never returns: `data/audio_devices.json` has `usable_for_voice: []` ("cannot open: PortAudioError … PaErrorCode -9999"). Its 600 s budget also left a wake child alive when the parent was hard-killed (3 found, stopped). *Correction:* the directory run `python -m pytest` does **not** import this file - `tests/conftest.py` lists it in `collect_ignore`, and that conftest documents the same 390 s-at-import problem. The stall I first measured on the directory run had a different cause (see CD-17) | an unbounded wait for audio that does not exist, and a timeout that killed only the direct child | VERIFIED-FIXED (90 s/300 s budgets, skip **with a reason** when `usable_for_voice` is empty so a real timeout on a working mic still fails, and `taskkill /T /F` on timeout so no orphan is left) |
| CD-17 | test environment | High (not fixable in code) | The one-command suite (`python -m pytest`) stalled on `tests/test_arch2_memory.py::test_memory_commands` with the CPU idle, because **another session owns the GPU**: `nvidia-smi` = `16303 MiB total, 14794 used, 1202 free`, Ollama holding `qwen3:14b` 9199 MB + `qwen2.5-c8k:3b` 2292 MB + `nomic-embed-text` 308 MB. The same contention makes `tests/test_dispatcher_state_flow.py` fail with its own message: `GpuBusy: ishti-qwen2.5:14b needs about 9.8 GB and only 0.0 GB is free within the 80% GPU budget`. Run alone, `test_arch2_memory.py` passes **17/17 in ~15 s** | the governor is behaving correctly (refusing a model it cannot fit); the tests assume a free GPU | **OPEN - environmental** (evidence + command in `TEST_RESULTS.md`; re-run on an idle box) |
| CD-16 | cross-test hygiene | Med | `tests/test_chat_digest.py` re-points `ishti.rgraph.DB_PATH` and the digest store at temp files; if a later test in the same session used the relationship graph it would write into the test's scratch DB | module-level connection caches are global | FIXED in this pass (every test re-points both stores itself; nothing real is touched) |
| CD-18 | test hygiene | Low (resolved during this pass, by someone else) | `tests/conftest.py` documents a guard - "fails if a new script-only module is added without being listed here" - as `tests/test_selfcheck_hygiene.py`, and at the moment I checked, that file was **not** in `tests/`, so the guard that keeps `collect_ignore` honest was missing and nothing noticed | the file was removed/never committed | **RESOLVED while this pass ran**: `tests/test_selfcheck_hygiene.py` (3846 bytes) and an updated `tests/conftest.py` appeared in the working tree from a concurrent session, uncommitted. I did not touch either file and left them unstaged so that session can commit its own work |

---

## Second test-harness pass (2026-09-29, later the same day, independent session)

Found by running the documented one-command suite (`start.bat test` -> `python -m pytest`) and the independent
script harness (`python tools/verify_all.py --only test:`) and reading the raw output, not the docs.

| ID | Area | Severity | Evidence | Root cause | Status |
|---|---|---|---|---|---|
| CD-19 | test harness | High | `python -m pytest --collect-only -q` (whole `tests/`) did not finish - over 180 s, then over 300 s; one run of the same command on one file exited **127** (= `0xC0000409`, the whisper fail-fast `tests/test_stt_threads.py` exists to catch) and wrote **0 bytes**. The mechanism is visible with `-s`: `pytest --collect-only -q -s tests/test_browser_tabs.py` prints that script's own line `[self-check] browser tab reuse OK - parse, exact/host/domain match, reuse over navigate, safe fallbacks`, i.e. pytest's collection phase was EXECUTING the scripts. Nine files are standalone self-check scripts (0 top-level `def test_*`): browser_tabs, client_isolation, dispatcher_followups, multitopic, planner_messenger, shutdown_cleanliness, stt_threads, vram_governor_pressure, lifecycle. `test_whatsapp_context.py` is mixed - its module-level body ran and it printed `[models] LOAD: ishti-qwen2.5:14b`, so an "offline" run needed Ollama and a GPU | pytest imports every `test_*.py` it finds; only `test_lifecycle.py` was in `collect_ignore` | **FIXED** - `tests/conftest.py` now lists all nine plus `test_whatsapp_context.py` with the reason; new `tests/test_selfcheck_hygiene.py` (2 tests) fails if a script-only module is added without being listed, and fails if an ignored module's tests are no longer run by its `__main__` block. Measured after: collect-only **4 s**, full suite **86 passed, 10 deselected, 117 s**, and `verify_all --only test:` **24/24** |
| CD-20 | test harness | High | `venv\Scripts\python tests/test_lifecycle.py` (a) died inside `subprocess.run` with `TypeError: '_FakePopen' object does not support the context manager protocol` - the double only accepted the constructor; (b) once past that it **opened a real pywebview desktop window** (110 s, `[pywebview] Error while processing splash_win...`) because `launcher.main()` calls the un-patched `run_app_window()`; (c) it then asserted the launcher spawns `startup.py`, which `desktop/launcher.py:91` no longer does (`pythonw -m ishti 8888` + `tray.py`), and that `start.bat` contains `focus_ishti.py`, which it does not (`start.bat` launches `dist\ISHTI\ISHTI.exe` or `desktop\launcher.py`) | the test pinned a launcher layout that was refactored (Issue 29), and its subprocess double was never completed - nothing caught it because the file is in `collect_ignore` and `verify_all` only kept the last 3 output lines | **FIXED** - the fake implements `__enter__`/`__exit__`/`communicate`/`poll`/`wait`/`terminate`/`kill`; the window is patched and asserted to open exactly once; assertions now check the real contract (one `-m ishti 8888`, one `tray.py`) and assert the `startup.py` banner is **absent**. Measured after: **exit 0 in 4 s** |
| CD-21 | test harness | Med | In one full `verify_all --only test:` run two tests failed: `test_chat_digest.py::test_n5_prompt_injection_does_not_change_behaviour` and `test_dispatcher_state_flow.py` (`AssertionError: deep_research should have been routed`, 94 s in that run vs 35-39 s on a quiet one). Neither reproduces: n5 alone **0 failures in 20 runs**, whole file 3/3, script mode 2/2, harness item 3/3; `test_dispatcher_state_flow.py` 2/2 direct and green in the next full harness run (24/24) | both drive the LIVE model-assisted routing path, and the failing run coincided with the GPU contention recorded in CD-17 | **OPEN - environmental**, same class as CD-17. The assertion now prints what the router chose instead (`web_search=...`) so the next occurrence is diagnosable instead of opaque |

CD-18 is closed by this pass: `tests/test_selfcheck_hygiene.py` is in the tree, tracked, and collected
(`PASS test:test_selfcheck_hygiene`, 2 tests).

---

# Session 2026-09-30/10-01 — skill_creator + self-knowledge pass (WP-C2 scan, done by the integrator)

The Issue-Hunter sub-agent could not run (its inference quota was exhausted mid-session — recorded in
`NEEDS.md`), so this scan was run and written by the integrator instead. Every number below was measured on
this PC on 2026-09-30/10-01 with the commands shown; nothing is asserted from reading alone. Rows are
`ID | Area | Severity | Evidence | Repro | Root cause | Status`.

| ID | Area | Severity | Evidence | Repro | Root cause | Status |
|---|---|---|---|---|---|---|
| SC-01 | Routing | **High** | `ishti/router.py:53` filtered only `s.status != "broken"`, so a skill with manifest `status` `disabled`/`draft`/`retired` was still routed to; and `ishti/skills/registry.py:retrieve()` — the only function honouring the Settings `skills.disabled` flag — has **no runtime caller** (repo-wide grep: the only hits are its own `__main__` and `interface/web_ui/server.py:1261-1278` writing the flag). Effect: the dashboard's Disable button never affected chat routing, and `skill_lifecycle.retire()` was decorative. | `git grep -n 'status != "broken"' ishti/router.py`; `git grep -n 'retrieve(' -- '*.py'` | The lifecycle status and the enable/disable flag were stored but never consulted on the hot path | **FIXED** — `router.NON_ROUTABLE` + `registry.disabled_names()` (cached 5 s, exception-safe); `tests/test_router_lifecycle_flags.py` is **red before** (9 failures) and **14/14 green after**; 29 related dispatcher/registry tests still pass |
| SC-02 | Config / models | Medium | `ishti/config.json` sets `llm.models.coder = "qwen3-coder:30b-a3b"`, which is **not installed** (`ollama list` has no such model). `ishti/llm.py:89-94` hides this behind a fallback chain, so the coder silently resolves to `ishti-qwen2.5-coder:14b`. | `ollama list; & 'venv\Scripts\python.exe' -c "from ishti import llm; print(llm.model_for('coder'))"` | A config value that no longer matches reality, masked by a fallback | **OPEN** — in `NEEDS.md`; fixing it edits a system config file, so it is the owner's call |
| SC-03 | Test coverage | Medium | **121** skill manifests, but only **2** declare `test_file` (`chat_digest`, `skill_creator`). | `Get-ChildItem ishti\skills -Directory | % { if (Test-Path "$($_.FullName)\manifest.json") { if (Select-String -Path "$($_.FullName)\manifest.json" -Quiet -Pattern '"test_file"') { $tf++ } } }` | Per-skill QA suites exist under `tests/skills/<name>/` but manifests mostly do not point at them | **OPEN** — 119 manifest edits with no behaviour change; listed as NEEDS work |
| SC-04 | Test coverage | Medium | 121 skills have `skill.py`; only **111** contain an `if __name__ == "__main__":` self-check, so **10 skills cannot self-verify** and `tools/verify_all.py` reports nothing for them. | same loop as SC-03, testing `-Pattern '__main__'` | Self-checks were added skill by skill and never back-filled | **OPEN** — NEEDS; whether those 10 skills actually work is **not established** |
| SC-05 | Robustness | Medium | **111** `subprocess.run/Popen/check_output/call` call sites across `ishti/ orchestrator/ desktop/ tools/`; **52** have no `timeout=` within the following 3 lines (heuristic, so an upper bound). A hung child would hang ISHTI. | `Select-String -Pattern 'subprocess\.(run\|Popen\|check_output\|call)\(' -Context 0,3` | Most call sites got a timeout, a minority were missed | **OPEN** — each site needs its own judgement (some are short probes, some are ffmpeg/model loads); not bulk-fixed |
| SC-06 | Robustness | Medium | **145** `except <X>:` blocks whose next line is `pass` under `ishti/` (heuristic over text-shaped context). Some are deliberate best-effort cleanup; many are unlogged failures. | `Select-String -Pattern 'except[^:]*:\s*$' -Context 0,2` filtered to `pass` | Failure is the default outcome and nobody counted it | **OPEN** — NEEDS; a fix must decide per site and mostly add a logged reason |
| SC-07 | Supply chain | Low | `shell=True` at `ishti/skills/gitops/skill.py:14`, `ishti/skills/vulnscan/skill.py:8,62,171,195`, `tools/check_libraries.py:152`. The git/vuln skills build command text. | `Select-String -Pattern 'shell\s*=\s*True'` | Convenience when assembling argument strings | **OPEN** — those skills are mostly read-only, but any interpolated value would be a command-injection path; NEEDS |
| SC-08 | Portability | Medium | **16** hardcoded `D:\ISHTI` references in shipped code (`ishti/check.py:281,282`, `ishti/monitor.py:22`, `ishti/skills/files/skill.py:1`, `orchestrator/deep_research.py:21`, `orchestrator/doctor.py:23`, …), while `owner_profile.yaml` lists a Mac M1 and a Ryzen laptop as machines the owner uses. | `Select-String -SimpleMatch 'D:\ISHTI'` over `ishti/ orchestrator/ interface/` | Paths were typed during Windows-only development | **OPEN** — NEEDS; `ishti.ROOT` exists and should be used everywhere |

| SC-09 | Security | Info (no finding) | No committed secrets. `git ls-files data` = **0** files and `.gitignore:8` ignores `data/`, so `data/api_token` is untracked. The only three lines matching `api_key\|apikey\|client_secret\|access_token =` were classified without printing any value: `ishti/ftlog.py:31` env/lookup, `ishti/ftlog.py:173` a redaction test string, `ishti/llm.py:30` a config expression. | `git ls-files data`; `scratch/classify_secret_lines.py ishti/ftlog.py:31 ishti/ftlog.py:173 ishti/llm.py:30` | — | **CLEAN** |
| SC-10 | Routing quality | Medium | Laya's own runtime warning: *"this checkpoint ships invalid temperatures or values outside [0.5, 5] … Treat confidence from the affected entries as uncalibrated"* (`venv\Lib\site-packages\laya\router.py:260`, printed by every routing test run). Measured consequence: *"turn every PNG in this folder into a single PDF"* routes to `files` at confidence **0.359**, clearing the router's own `min_confidence` of **0.2** (`ishti/config.json` → router). | `pytest tests/test_prompt3_skills.py -q` (warning appears); the outcome is exercised in `ishti/skills/skill_creator/gap.py`'s self-check | The router's threshold and the scorer's calibration disagree, so "which skill owns this" is decided by a number nobody can trust | **OPEN** — mitigated, not fixed: `gap.confidence_floor()` deliberately reuses the router's own 0.2 rather than inventing a private threshold, and says so in its docstring. Re-calibrating Laya is NEEDS |
| SC-11 | Docs / process | Low | There are **two** NEEDS files: `log_need()` appends to `D:\ISHTI\md files\NEEDS.md` (`ishti/skills/__init__.py:227`) while the curated one is `D:\ISHTI\NEEDS.md`. A gap logged at runtime therefore never reaches the curated file. | `Select-String -Path ishti\skills\__init__.py -Pattern 'NEEDS.md'` | Two different files share one purpose | **OPEN** — the deliverable is the root `NEEDS.md`, so this pass surfaces runtime gaps in `TEST_RESULTS.md`/`FINAL_REPORT.md`; merging the two is an owner decision |
| SC-12 | Environment | Info | A research sub-agent reported this PC as *"Windows 11 Pro 25H2 build 26200.9550"* with driver `610.47`; the registry (`HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion`) reports `DisplayVersion=26H2`, `CurrentBuildNumber=26300`, and `nvidia-smi` reports driver `610.88` with exactly one GPU (`GPU 0: NVIDIA GeForce RTX 5070 Ti`). | `nvidia-smi -L; nvidia-smi --query-gpu=index,name,driver_version --format=csv; Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion'` | One measurement came from a different method, or a driver changed mid-session; not resolvable from here | **UNVERIFIED** — the self-model reports the registry/nvidia-smi values it measured itself and stamps them `[live:registry]` / `[live:nvidia-smi]`; the discrepancy is recorded rather than smoothed over |
| SC-13 | skill_creator | Medium | `from ishti.skills import skillsmith` returns the **folder**, not the module — `ishti/skills/skillsmith/` has no `__init__.py`, so `.scan`/`.validate`/`.install_folder` do not exist on it (`AttributeError: module 'ishti.skills.skillsmith' has no attribute 'scan'`, caught while building `skill_creator`). Any new code reusing skillsmith the obvious way breaks at runtime. | `python -c "from ishti.skills import skillsmith; print(hasattr(skillsmith,'scan'))"` → False | Namespace-package semantics inside a regular package | **FIXED** in the new code (`from ishti.skills.skillsmith import skill as skillsmith`, reason in a comment) — but **the trap remains for every other caller**, so it is NEEDS work |

| SC-14 | selfmodel | Medium | `render()` skipped any probe section whose value key was absent, so a **failed** probe was silently omitted — indistinguishable from "this machine has no GPU", which is exactly what its own docstring promised never to do. | `python -m ishti.selfmodel` → the failed-probe case in its self-check | One `if` gate covered both "no data" and "read failed" | **FIXED** — a failed probe now renders as `- <label>: <error> [live:failed]` |
| SC-15 | selfmodel | Medium | The over-budget trim loop `lines.pop(-2)` dropped the **last** non-final line, so an overflowing block silently deleted the `MISMATCH` baseline warnings and then the honesty rule itself. | `python -m ishti.selfmodel` → the MISMATCH and "honesty rule must survive trimming" cases | Trimming by index instead of by priority | **FIXED** — `render()` now assembles head/body/tail and only ever drops **body** lines |
| SC-16 | skill_creator | Low | `generate()` reported only *"never returned usable code after N attempts"* after 5 failures, hiding the real reason (e.g. a module larger than the 300-line cap, which is the documented split-into-smaller-skills case). | `python -m ishti.skills.skill_creator.skill` → the oversized-module case | The rejection reason was computed inside the loop and then discarded | **FIXED** — the last problem is carried into the failure message, so NEEDS.md gets *"the module is N lines (limit 300)"* instead of *"it failed"* |
| SC-17 | selfmodel | Low | `detail()` ("what skills do you have?") answered **"Skills (0 registered)"** on a cold cache, because it used the non-blocking path while the inventory refresh was still running in its background thread. | `python -m ishti.skills.meta.skill` before the fix | The async optimisation was applied to an explicit owner query that must be accurate | **FIXED** — `detail()` forces a full re-read. Measured cost: `load_skills()` = **1.2 s** for 121 skills, so the forced read is acceptable |

| SC-18 | selfmodel | **High** | `build()` copied `_cache["data"]` into a local `data`, then `_refresh_full()` wrote the model/skill inventory into the cache — and the final `_cache["data"] = data` **overwrote it away**. Every forced build therefore returned no `models`/`skills`, and `detail()` answered **"Skills (0 registered)"** and **"Model roles: unknown"**. | `python -m ishti.skills.meta.skill` (before the fix) → the AssertionError carried `Skills (0 registered)`; `python -m ishti.selfmodel` now asserts both keys are present | Cache written and read in the wrong order across two functions | **FIXED** — merge instead of replace in both directions, plus a regression assertion that a forced build contains `models` and `skills` with `skills_total > 50` |
| SC-19 | selfmodel | Medium | `detail()` (the answer to "what skills do you have?" / "what GPU do I have?") printed the owner and the rules but **never the hardware facts** — those lived only in `render()`. Acceptance test 10 therefore had no data source for the PC questions it asks. | `python -c "from ishti import selfmodel as sm; print('RTX' in sm.detail())"` → False before | `detail()` was written before `render()` existed and was never updated to include it | **FIXED** — `detail()` now leads with `render(data)`; measured 17,969 chars with GPU/RAM/CPU/OS/disk/models/**121 of 121 skills** |
| SC-20 | Multilingual | Medium | The self-knowledge trigger matched **English only**, so "تم کون ہو", "ته ول څوک یې" and "tum kaun ho" would never reach it (they would fall through to Laya/the chat skill). | `tools/check_paraphrases.py` → before: 1 of 28; the earlier English-only run scored 6/6 EN and 0/18 in the other languages | The regex was written for the language the owner's prompt used | **FIXED** — Urdu (Arabic script), Pashto and Roman-Urdu patterns added; **28/28 paraphrases now recognised, 0 false positives** on ordinary requests |
| SC-21 | Multilingual | Low | The Pashto/Urdu patterns missed the plural/alternate spelling of "model": `ماڈل` (U+0688 ڈ) matched but `ماډل` (U+0689 ډ) did not, and the alternation's trailing `\b` broke Pashto suffixes like `ماډلونه`. | `tools/check_paraphrases.py` → `MISS PS 'ته کوم ډول ماډلونه کارول'` | One spelling was hardcoded; `\b` is ASCII-oriented and does not behave for Arabic-script suffixes | **FIXED** — the pattern now accepts either character (`ما[ڈډ]?ل`) and the group no longer ends in `\b`. Note: the first attempt (`ڈ?`) was wrong — an optional ڈ matches ڈ-or-nothing, never ډ |
| SC-22 | skill_creator | **High** | The research stage had **budgets but no timeout**: 6 searches / 6 fetches cap the *count*, not the *time*. With SearXNG unreachable, one search fell back to Playwright and sat inside that single call — run #2 made no progress for ~5 minutes and had to be killed. | `tools\skill_creator_run.py "make yourself able to combine a folder of images into one PDF"` → last line stayed `[MultiProviderSearch] Cascading to Playwright Chrome search ...` | A count-based budget was mistaken for a time bound | **FIXED** — `_bounded()` runs each call in a daemon thread with a wall-clock ceiling (45 s per call, 240 s total), the stage continues with whatever it has, and the self-check proves a `time.sleep(3600)` search is abandoned (`[research] search timed out after 2s`) rather than waited on |
| SC-23 | skill_creator | Medium | The feasibility gate compares free VRAM to a **fixed 4096 MB** threshold, so with the planner pinned `Forever` it refuses codegen even though the governor could step the planner down — run #2 proved the governor *does* walk the ladder (`14b → 14b-c4k → coder-c8k:7b`) when a load is actually attempted. Measured free VRAM at the decision: 965 MB (run #1), 5341 → 3131 MB (run #3) against a 9922 MiB coder footprint. | `tools\skill_creator_run.py ...` → *"a local coder model that is installed and fits in VRAM (... 3131 MB free of 16303 MB) - I will not offload part of a model onto the CPU"* | A single static threshold stands in for a question that belongs to the VRAM governor | **OPEN** (deliberately) — the refusal is the *safe* direction and it caused no partial offload, so nothing was weakened to make a test pass. The correct fix is to ask `vram.plan()` and let the governor decide; see `NEEDS.md` N22 |
| SC-24 | skill_creator | Medium | `register()` took its backup **before** installing, so on a FIRST registration `_backup()` looked for `ishti/skills/<name>` — which does not exist yet — returned `""`, and `rollback()` then honestly reported *"no backup exists for that skill yet"*. A newly registered skill was therefore un-rollback-able. | `test_rollback_restores_the_previous_version` → `AssertionError: {'ok': False, 'reason': 'no backup exists for that skill yet'}` | Backup ordering: the snapshot was taken of a path that had not been created yet | **FIXED** — after a successful install, if no pre-install backup exists (a first registration rather than an update), the newly installed folder is snapshotted so rollback always has a target |

### Not verified (said so rather than guessed)


- **SC-05/SC-06/SC-07 counts are heuristics** from `Select-String`, not an AST walk; a hand-written parser could
  change every number. Each row names the command so the number can be re-derived.
- Whether the 10 skills without a self-check (SC-04) are *broken* or merely unverified is **not established** —
  nobody ran them.
- The two `GpuBusy` failures in `md files/NEEDS.md` (the CD-17/CD-21 class) were **not** reproduced: the GPU
  was shared with the research phase, and a deliberate reproduction needs exclusive access.
- **Codegen quality was not measured.** The serialised GPU window for real code generation (the three
  acceptance tasks of the master prompt) was **not run** in this pass, so no claim is made about what
  `ishti-qwen2.5-coder:14b` actually produces for a real skill — see `NEEDS.md`.

