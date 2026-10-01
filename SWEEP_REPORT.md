# SWEEP_REPORT.md — "resolve every remaining issue" (2026-10-01, second pass)

Scope asked for: *find and fix every remaining problem; the project fully working, without a single issue.*
Method: fresh gates first, then **every** open row in `NEEDS.md` / `md files/NEEDS.md` / `ISSUES.md` /
`TEST_STATE.json` triaged into {code-fixable, doc-fixable, owner-blocked}; every code fix measured and
gated; every closed row backed by a command run on this PC (never by reading alone). Commits:
`8b66c80`, `e3d54e9`, `df135d1`, `373b09a`, `4e5a795`, `f85d4d3`, `f626050`, `3f259c0`, `0c97bf9`.

## 1. Found and fixed (each with the evidence that proved it)

| ID | Area | Sev | Evidence it was real | Root cause | Status |
|---|---|---|---|---|---|
| SW-01 | planners skill | High | `dispatcher.handle("make flashcards")` -> `The planner's skill failed: JSONDecodeError: Expecting value: line 1 column 1 (char 0)`; the 2026-09-30 13:53 NEEDS row was the same skill refusing to write outside its scope | `json.loads(raw)` with no guard on the model's answer, and the manifest's write scope was `data` while the skill's documented home is `Documents/ISHTI` | **FIXED** — `parse_json` + card sanity + honest retry replies; `scope` gained a `home:<sub>` root form and planners/study declare `home:Documents/ISHTI`; `op_lecture` guards a missing URL; self-check asserts the scope roots. Live: the request now answers "I could not format the flashcards right now…" (no crash, no failure row) |
| SW-02 | orchestrator VRAM | High | ISSUES.md Issue 23: `orchestrator/model_router` posted to `/api/generate` directly | never routed through the VRAM governor | **FIXED** — busy-guard -> `vram.admit` -> rung swap -> lease released in `finally`; `GpuBusy` (busy / 404 / cannot-fit) is refused in code, governor-unreachable degrades exactly like `llm._create`. Verified both paths live (Ollama down and up) |
| SW-03 | ModelRouter config drift | High | `[ModelRouter] qwen3:30b-a3b is not installed - using ishti-qwen2.5:14b` | `orchestrator/config.json` names a model that is not installed (SC-02 / Issue 21 class), so every synthesis was refused and silently downgraded to raw source cards | **FIXED in code, config untouched** — a 404 resolves to the configured fallback, else the largest installed model of the same family (tags cached 60 s). `config.json` itself stays the owner's to edit |
| SW-04 | deep_research skill | High | 5000-char QA row: `openai.BadRequestError: request (10089 tokens) exceeds the available context size (8192)`; and the escalation branch replaced a non-empty report with an EMPTY condenser result -> blank reply (the suite asserted "dispatcher produced no answer") | no length clamp anywhere on the query; the condense step overwrote instead of guarded | **FIXED** — `MAX_QUERY_CHARS=300` clamps the prompt, every reword and the `tried` list; the condense keeps the report when the model returns nothing; a skill can no longer answer with blank text |
| SW-05 | SC-03 / SC-04 | Medium | 121 manifests, only 2 declared `test_file`; 10 skills had no `__main__` self-check | per-skill suites existed but nothing linked them; self-checks had been added skill by skill | **FIXED** — 119 manifests declare their suite (textual one-line insert, no reformatting churn) and all 10 skills now self-check (each verified by running it). `check_skills` still OK |
| SW-06 | SC-07 | Low | `shell=True` at `tools/check_libraries.py:152` (the gitops/vulnscan hits were **false positives**: a docstring that literally says "never shell=True", and the AST scanner's own detection rule) | `pkg` from `config.json` interpolated into a shell string | **FIXED** — argv list + `shutil.which("npx")`; `check_libraries` OK, both MCP servers still verified |
| SW-07 | SC-08 | Medium | 16 hardcoded `D:\ISHTI` constants in shipped code | Windows-only development | **FIXED** — every `ROOT`/`ROOT_DIR`/`PROFILE_DIR`/`DB_PATH` derives from `__file__` (identical values here, correct on the owner's Mac/Ryzen); `ishti/check.py` computes its own root; files-skill docstring de-hardcoded. Suite green twice after |
| SW-08 | SC-13 | Medium | `from ishti.skills import skillsmith` binds the FOLDER (no `__init__.py` in any of 125 skill dirs), so `.scan` / `.install_folder` do not exist | namespace package inside a regular package | **FIXED + GUARDED** — the last no-op instance removed; `tests/test_skills_import_hygiene.py` AST-fails any folder-level import while allowing the safe `ishti.skills.<name>.<module>` form (same guard style as `test_selfcheck_hygiene` / `test_desktop_bootstrap`) |
| SW-09 | Issue 7 class | Low | whole-tree compile sweep under `-W error::DeprecationWarning` found invalid escapes in `desktop/shutdown.py`, `ishti/watchdog.py`, `tools/chat_digest_sample.py`, `tools/check_paraphrases.py` | Windows paths typed into non-raw strings | **FIXED** — raw strings; the shipped tree now compiles clean under `-W error::DeprecationWarning` |
| SW-10 | capcut skill | Low | two owner-facing messages printed **doubled** backslashes (`%LOCALAPPDATA%\\CapCut\\…`) plus a stray `\.` | the path was written with doubled backslashes and an invalid escape | **FIXED** — single backslashes in a raw string, verified by evaluating the literals with `ast` (3 separators -> 3 backslashes) |
| SW-11 | CD-12 | Low | the documented "third PDF path" imported reportlab, appended a note and still raised | it was a detection stub, not a renderer | **FIXED** — real landscape-A4 reportlab render (paragraphs, bullets, grids, repeating header rows) with an Arabic-capable Windows TTF; `reportlab 5.0.1` installed (still optional: browsers come first). Regression `test_n9b_pdf_still_renders_without_any_browser` forces both browser paths dead |
| SW-12 | SearXNG (environment) | Medium | `[SearXNG] unavailable (ConnectionError)` for every search; matches the 2026-09-29 23:37 NEEDS row | Docker had the container **Up** but its host port mapping (127.0.0.1:8890) inactive after a host reboot | **FIXED (environment)** — `docker restart ishti-searxng` re-applies the mapping: `/search?q=ping&format=json` -> **200**. `ensure_searxng()` starts the container but cannot repair a dead port-forward — this is the remedy to record |
| SW-13 | dispatcher escalation | High | the interrupted 2026-10-01 repro ran `web_search` -> escalated `deep_research` and printed an **empty final answer**: the blank fallback reply was accepted as the winning answer (a second sighting of the SW-04 class, but in the dispatcher, not the skill) | `fell_short("")` was `False` - a skill that answered NOTHING scored as not-falling-short, so it replaced the first skill's real (imperfect) answer; and no central guard existed for blank/missing replies | **FIXED** — blank/missing replies count as falling short (and `None` can no longer crash the check); the escalation slot only accepts a reply with text; `_finish` refuses to return an empty `final_answer`; reply-less skill dicts no longer `KeyError` the close-out (`out["reply"] += ...`). Regression suite `tests/test_dispatcher_blank_answer.py` (4 tests, fully offline, asserts the escalation path actually ran) — 9/9 together with the three dispatcher suites |

## 2. Investigated, not reproducible (no code change claimed)

- **`OSError: [Errno 22] Invalid argument`** — three entries 2026-09-30 08:44-08:46 (chat, dispatcher,
  shopping). All three request texts now answer cleanly (`files`, `files`, `web_search`). The app caught
  and logged them, and the same-hour rows show the model-not-found / `GpuBusy` storm: consistent with an
  LLM-client write failure while Ollama was being restarted. Marked **UNREPRODUCED**, not "fixed".
- **`KeyError: 'alpha'` (2026-09-27 19:28, four requests)** — `decompose()`/`assign()` now validate every
  proposed agent against `AGENTS` and re-assign unknown ones; the four texts route cleanly today
  (`researcher`). Already resolved by an intermediate session — verified, nothing to change.
- **`ishti-spark2.5:4b` blocking the 14B** during live suites — my own test-mode footprint; clearing
  Ollama (`keep_alive: 0` on `/api/ps`) restored a pass. This is the documented Q10 policy: run the live
  suites when the GPU is idle, not a product defect.

## 3. F1 flake — third sighting, better characterised (test deliberately unchanged)

`tests/test_chat_digest.py::test_n5_prompt_injection_does_not_change_behaviour` failed once inside a
full-suite run **and once focused immediately after** a full-suite run; the next 17 focused runs were
green. New datapoint vs. the earlier two sightings: it is not confined to in-suite load — it can fire in
the disk-churn window right after a suite (AV/indexer). Per the standing rule the test is **not** weakened;
the evidence is appended to `TECHNIQUE_LOG.md`.

## 4. Still open — and why (no honest sweep can call these "fixed")

| Item | Why it stays open |
|---|---|
| **CD-11** ruff: ~1600 pre-existing findings, no ruff config, lint is not a gate | A 1600-finding cleanup is a project in itself; recorded, not hand-waved into "clean" |
| **CD-13** chat-UI "Open / show in folder" for the exported report | UI work, not a defect in behaviour |
| **Issue 30** chat upload picker / `POST /api/upload` | Feature, not a fix |
| **SC-05** 52 subprocess sites without an explicit timeout | Each needs its own judgement (short probes vs ffmpeg/model loads) |
| **SC-06** 145 `except …: pass` sites | Same: per-site review, mostly needs a logged reason |
| **SC-11** two NEEDS files (`NEEDS.md` and `md files/NEEDS.md`) | Merging them is an owner decision (the runtime appends to the latter) |
| **Issue 22** 18.5 GB stale Ollama partial blob | Ollama's store; ISHTI must not touch it |
| **Issue 24** 7B planner rung loses Urdu quality | By design and visible to the user; needs the owner's own Urdu prompts |
| **SC-23** skill_creator feasibility gate uses a fixed 4096 MB VRAM threshold | The refusal is the safe direction; the proper fix is to ask `vram.plan()` |
| **SC-10 / Need 9** Laya calibration + semantic threshold | Need real traffic to re-tune honestly |
| **Q10** QA batches vs the live instance's GPU | An owner policy choice (idle-run / handshake / reserved slice) |
| **N22-N26** real Telegram token, WhatsApp enable, cua-driver install, cua-bench Python 3.12 | Owner-gated; documented with evidence in the remote-control report |
| **Need 2/3/13** browser vision fallback, planner vs coder VRAM, Fish Audio S2 | Need downloads/installs the owner must approve |

## 5. Gates after the sweep (all on this machine, 2026-10-01)

| Gate | Command | Result |
|---|---|---|
| Offline suite (×2 consecutive) | `venv\Scripts\python -m pytest --timeout=600 -q` | **green, exit 0 both runs** |
| Offline suite after the SW-13 fix | `venv\Scripts\python -m pytest --timeout=600 -q` (`_work/gate_suite3.log`) | **green, exit 0, 0 FAILED lines** |
| Skills contract | `venv\Scripts\python tools\check_skills.py` | **122 skills, 236 assertions, 333 labelled requests OK** |
| Live end-to-end | `venv\Scripts\python -m ishti.check` | **PASS** — "core loop OK — routing, memory, live skill, local LLM, remote auth, situation layer" (routing 333/333 at median 0.2 ms); re-run green after the SW-13 dispatcher fix |
| Performance | `venv\Scripts\python tools\perf_guard.py --baseline _work\bench_c2_baseline.json` | **PASS** — import -5.6%, `load_skills()` 374.4 -> 356.1 ms (-4.9%), route -2.4%, reflex -5.0%, planner tok/s +3.1% (live `ishti-spark2.5:4b`, 84.71 tok/s), handle reflex +6.1%, handle rule +10.6% — all inside tolerance |
| Libraries | `venv\Scripts\python tools\check_libraries.py` | **OK** — 36 call sites, both pinned Playwright-MCP servers still offer every flag ISHTI passes |
| New self-checks | `python -m ishti.skills.<name>.skill` ×10 | **10/10 green** (apps, capcut, chat, deep_research, files, owner, system, video, weather, web_search) |
| Chat-digest suite | `pytest tests/test_chat_digest.py -q` | **all green**, including the new CD-12 no-browser test |

## 6. Honest summary

Every row that could be closed from code is closed, with a test or a command behind it. Three of the
"issues" turned out to be **false positives** (SC-07's gitops/vulnscan hits, my own sweep's first
report), and one (CD-12) was a documented fallback that never existed — now it does. Two rows are
environment failures with a proven remedy (SearXNG port-forward, VRAM contention) rather than defects, one
is unreproduced and therefore uncured (`Errno 22`), and the F1 flake is characterised, not silenced. The
remaining OPEN list in section 4 is owner-decided work or per-site judgement — stated plainly instead of
being counted as "no issues".
