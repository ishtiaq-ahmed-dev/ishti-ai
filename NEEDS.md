# ISHTI System Prerequisites & Requirements (NEEDS.md)

This document tracks toolchain dependencies, missing skills, or user installations required for native builds and advanced features.

---

## 1. Optional Native Tauri 2 Toolchain
ISHTI currently operates via a native Microsoft Edge WebView2 desktop shell with PyInstaller `--onedir` distribution, which runs with zero terminal windows, native GPU acceleration, and low RAM.

To compile a native Rust Tauri 2 binary (`cargo tauri build`), the following toolchain components must be installed:

1. **Rust Toolchain**:
   - Download and run the official installer: [https://rustup.rs/](https://rustup.rs/) (or `winget install Rustlang.Rustup`).
   - Default host triple: `x86_64-pc-windows-msvc`.
2. **Visual Studio 2022 C++ Build Tools**:
   - Download Visual Studio Build Tools: [https://visualstudio.microsoft.com/visual-cpp-build-tools/](https://visualstudio.microsoft.com/visual-cpp-build-tools/)
   - Workload to select: **Desktop development with C++** (includes MSVC v143, Windows 10/11 SDK, C++ CMake tools).
3. **Tauri CLI**:
   - Run `cargo install tauri-cli` or `npm install -g @tauri-apps/cli`.

*Note: The native WebView2 Python shell is already fully functional on this system and does not block desktop usage.*

---

## 2. Optional NSIS Binary Compiler
To compile the standalone `.exe` installer from `installer/ishti.nsi`:
- **NSIS (Nullsoft Scriptable Install System)**:
  - Install via winget: `winget install NSIS.NSIS`
  - Or download from: [https://nsis.sourceforge.io/Download](https://nsis.sourceforge.io/Download)
  - Default binary path: `C:\Program Files (x86)\NSIS\makensis.exe`.


---

# Backend (VRAM governor + lifecycle) — what is missing or needs the owner's decision (2026-09-29)

Nothing below blocks the backend; each item says exactly what is needed and what happens until then.

## N1. Your own prompt set, to grade the lighter models
The quality column in `MODEL_DECISIONS.md` comes from six fixed prompts I wrote (arithmetic, JSON extraction, a table, an Urdu
translation that only checks for Arabic script, a tool call, reading a number from an image). On them the 7B planner rung
**fails the Urdu prompt** and both coder rungs fail the tool-call prompt. Whether that matters depends on your real use.
*Needed:* 10–20 prompts you care about (especially Urdu/Pashto, plans, tool use) in a text file; then
`python -m ishti.vram_calibrate` grades every rung on them. *Until then:* the planner steps down to the 7B during voice sessions
and heavy vision jobs, and the UI receives a `model_switch` event saying so.

## N2. Decide: quantized KV cache (`OLLAMA_KV_CACHE_TYPE=q8_0`)
Left **off** (`f16`). The numbers from `tests/kv_cache_probe.py` are in `MODEL_DECISIONS.md`; enabling it also needs an Ollama
restart, which ISHTI only does for an Ollama it started itself (see N5). *Needed:* your OK after reading the quality result
(recall test + fixed prompts), then `POST /api/ollama/settings {"kv_cache_type":"q8_0"}` and `POST /api/ollama/apply`.

## N3. Decide: heavy vision tier 3 (gemma3:12b) and the 80% budget
At the default 80% budget the 12B vision model (8973 MiB) cannot sit next to any planner rung, so heavy vision jobs get tier 2
(`gemma3:4b`) and the planner steps to the 7B meanwhile. With `budget_pct: 95` it works (planner → 3B, both resident, 83% peak) but
leaves little headroom for the browser/Windows. *Needed:* your call on `budget_pct` (edit `models_ladder.yaml`; picked up live).

## N4. Decide: Laya on the CPU?
Laya holds ~1.3 GB of VRAM (and spikes to 3.8 GB while loading), which is why the 14B planner alone already sits at ~78.5%.
Moving it to the CPU would give the planner ~1.3 GB of room but slows routing (it was measured at ~35 ms on the GPU).
*Needed:* your OK; the change is one line in `ishti/router.py` and needs a routing-latency check (`python -m ishti.check`).

## N5. The Ollama that is running now was not started by ISHTI
It was started outside (process 4476, parent gone), so ISHTI treats it as **not ours**: it unloads its own models at shutdown but
never stops or restarts it, and it cannot apply new Ollama settings to it. *Needed:* either quit that Ollama once (tray icon →
Quit) so the next ISHTI start launches and owns one (hidden, in the Job Object, with the settings in `data/ollama_settings.json`),
or call `POST /api/ollama/apply {"take_over": true}` from the UI to let ISHTI restart it.

## N6. `config.json` model names, and a stale download
`llm.models.planner/coder` name `qwen3:30b-a3b` / `qwen3-coder:30b-a3b` (not installed, 19 GB). And an 18.5 GB `-partial` blob from
26 Sep sits in `~/.ollama/models/blobs`. Both are yours to change/remove (see ISSUES.md #21, #22).

## N7. Not measured: a live microphone turn
No microphone or speaker is connected to this PC (`Error querying device -1`, audit O-2). The voice tests ran the real pipeline in
process (Whisper load through the governor, STT decode, planner first token, TTS) with a synthesized question, not sound from a
microphone. *Needed:* reconnect the headset, then run `tests/e2e_vram_lifecycle.py t3 perf` again for the microphone-driven numbers.

## N8. For the desktop-shell session (Prompt 2)
`POST /api/focus` emits `show_window` on `/ws/events`; a second launch of the *backend* already calls it. The desktop launcher's own
second-launch path should call it too (it currently uses `desktop/focus_ishti.py`). Nothing in `desktop/` or `interface/app/` was
edited except `desktop/shutdown.py` (the stop routine, which is backend lifecycle and is now a thin client of `POST /api/shutdown`).

## N9. Chat File Upload UI Feature (Verification Scenario D3)
- **Status**: BLOCKED (Feature missing in frontend)
- **Issue**: The current chat interface (`interface/app/src/sections/Chat.tsx`) only provides a text input box. There is no visual file attachment button (paperclip) or drag-and-drop zone to upload images and PDFs directly into the chat session.
- **Current Workaround**: Users can type local filesystem paths (e.g. `summarize D:\ISHTI\data\sample.pdf`), which are parsed by the document and OCR skills.
- **Required Action**:
  1. Add a file attachment button and drag-and-drop listener to `interface/app/src/sections/Chat.tsx`.
  2. Implement a `POST /api/upload` multipart form endpoint in `interface/web_ui/server.py` that saves uploaded files to `data/uploads/` and passes the resulting path to the active conversation session.

## N10. Third-Party Integration — Apodex Clarification
- **Status**: BLOCKED (Repository ambiguity)
- **Issue**: Web search for "Apodex" identifies the commercial closed-source AI model family (`apodex.com`), but no official open-source repository exists with an embeddable Python client.
- **Required Action**: Owner must provide the exact repository URL or clarify whether this integration should interface with a specific REST endpoint.

## N11. Third-Party Integration — Pi Desktop Architecture Clarification
- **Status**: BLOCKED (Application vs library ambiguity)
- **Issue**: `vastsa/pi-desktop` is a standalone workstation application rather than an embeddable Python library with a stable agent API.
- **Required Action**: Owner must clarify the desired integration mode: (a) document and run side-by-side, (b) launch via sub-process from ISHTI, or (c) adapt UI design elements.

---

# 24-hour Chat Digest (Section 4) — what is missing or needs the owner's decision (2026-09-29)

The feature is built and tested; the items below are the parts I deliberately did **not** decide for you.

## N12. Unofficial WhatsApp reader (Baileys / `whats-mcp`) — NOT implemented, default OFF
Source 4 in the spec is deliberately not wired in. Before it can be: (a) an unofficial library can get the
account restricted or banned; (b) the Baileys npm ecosystem has had a credential-stealing "anti-ban" package
(April 2026) and a large malicious-fork campaign (September 2026) — so, if you ever approve it: the genuine
package from the official repo, pinned version, source reviewed, no telemetry, never a random fork; (c) history
is event-based, so only messages from connect time onward are available and media is not downloaded.
*Needed:* your explicit yes. *Until then:* sources 1–3 only (local store, read-only DOM read of the open tab,
official export).

## N13. Other platforms for the digest
Telegram / Discord / e-mail are **not** included. *Needed:* which platforms, and whether they may be read
with the same T0 rules. *Until then:* WhatsApp only.

## N14. Scheduled daily digest (spec 4.6, optional, off by default)
*Needed:* the time you want it and the wording. *Until then:* nothing is scheduled; the skill runs only when
you ask. If you want it, the reminders skill already has the machinery ("every morning at 8 ...").

## N15. Rubric tuning and owner name
`data/chat_digest_config.json` tunes the window (default 24 h), the timezone (default = system local), the chat
budget (250) and the usefulness horizons. Your own name in the chats can be added as
`{"owner_names": ["Ishtiaq"]}` so your messages are recognised as yours (they are already recognised when the
export writes "You"). *Needed:* nothing, unless you want different values.

## N16. `reportlab` is not installed
The PDF is produced by Chromium/Edge headless printing (already in the stack, verified on this box: 3-page
PDF + page rasterisation). `reportlab` is only the third fallback and WeasyPrint is deliberately avoided (GTK).
*Needed:* nothing unless you want a ReportLab fallback: `venv\Scripts\python -m pip install reportlab`.

## N17. "Open / Show in folder" button for exported reports
The chat UI shows the exact path of every file written to `reports/`, but there is no button next to the
message. *Needed:* a UI decision (a button that calls Explorer is a launch action; the skill itself stays
T0/read-only). *Until then:* say "open the digest report" and I will print the path again.

## N18. Visual DOCX check
No LibreOffice and no Word automation on this box, so the DOCX could not be rendered to PDF and looked at.
`python-docx` opens it and the structure is asserted (5 tables, column counts, `w:tblHeader` repeating header
rows, landscape, RTL runs). *Needed:* LibreOffice (`winget install TheDocumentFoundation.LibreOffice`) if you
want that last visual step.

## N19. Lint is not a gate in this repo
`ruff check ishti` reports 1600 findings on the pre-existing code and there is no ruff config in the repo, so
"lint passes" cannot honestly be claimed. *Needed:* your OK to add a `ruff.toml` (select = a small, chosen
rule set) and fix the pre-existing findings, or to leave lint as a non-gate.

## N20. React renderer screenshot in the live app
The table controls are verified in the built bundle and the report itself was screenshotted at 1400 px and
680 px (Edge headless) — but a screenshot of the **React chat window** needs the running desktop app and a GUI
session. *Needed:* start ISHTI and ask me to capture it, or take the screenshot yourself after asking for a
digest.

## N21. computer_use has no ISHTI_TEST_MODE guard on real actions
`desktop/computer_use.py::launch_app` (aliases -> shutil.which -> `os.startfile`/subprocess, DETACHED) and
`focus_window` execute for real regardless of `ISHTI_TEST_MODE=1` — the T2 block-at-last-step gate lives in
the dispatcher only, so direct `run()` calls (any test, skill-to-skill hook) bypass it for app control.
Found while fixing the `apps` generated suite (TEST_FIXES.md FIX-5): the only safe "open X" test row is a
nonexistent app. *Needed:* owner decision — put the guard in the agent (like the T2 terminal block) or keep
it dispatcher-level and document direct-call constraints; until then generated suites must never use a real
app name in an "open X" row.



---

# QA pass 2026-09-30 - what is missing or needs the owner (Prompt 1 of 3)

## Q1. Real multi-speaker recordings for the diarization accuracy check (NEMOTRON_FEASIBILITY.md)
The model runs (GO verdict), but accuracy on YOUR kind of audio (Urdu/Pashto/mixed, WhatsApp voice
notes, noisy/overlapping speech) is UNMEASURED: no microphone is connected (N7) and no recordings exist.
*Needed:* 2-3 short recordings (2-4 speakers, any language), placed anywhere and told to me; I run the
accuracy pass on them. *Until then:* verdict stays GO on feasibility, accuracy = unknown.

## Q2. Owner's yes to build the three OpenMuse-derived ideas (OPENMUSE_IDEAS.md)
1. follow-up queue view, 2. task leases + crash recovery, 3. recurring page monitors (price/text/
change, dedup + backoff). All local, no cloud. *Needed:* pick which (if any) to schedule.

## Q3. transformers pin decision for diarization
The diarization model only exists in transformers git-main (5.18.0.dev0); PyPI stable (5.17.0) lacks it.
*Needed:* OK to pin the git commit (isolated install, ISHTI venv untouched) until the next stable
release carries it. *Until then:* `.qa_nemotron/` stays a probe, not a dependency.

## Q4. `ISHTI_TEST_MODE=1` as the standing test rule
Implemented and wired into the T2 gate (block at the last step + audit record + scripted confirmations).
*Needed:* nothing - informational. Default OFF in production; only the QA harness sets it.

## Q5. 120 per-skill suites take time to run in full
`tests/run_skill.py all --runs 1` is hours (120 suites x net/model skills). *Needed:* your preference:
run `all` nightly vs on-demand per skill. The curated 105-test offline suite stays the fast gate.

## Q6. Voice/e2e rows need a GUI audio session (found in QA prompt 3)
Scenario 5's mic/voice rows and the TTS announcement path (`ishti.voice.speak` / wake listener)
run, but a spoken turn cannot be *verified* without an interactive audio session (no microphone
is attached, N7; sessions here are headless). *Needed:* a mic or a loopback audio device plus an
owner-run voice check. *Until then:* voice e2e rows are exercised at the dispatcher level and
BLOCKED at the audio level in CONCURRENCY_REPORT.md.

## Q7. Admin-gated chaos rows (found in QA prompt 3)
Chaos rows "drop the network", "fill the sandbox disk quota", "slow the disk", "sign out of
Windows" need administrator rights (firewall/netsh, quota settings, disk throttling, a second
logon session). *Needed:* owner-run chaos with the manual steps, or a disposable VM snapshot.
*Until then:* those rows are BLOCKED (metrics/chaos_results.json); the non-admin rows (Ollama
kill, mic toggle, 50-burst, skill crash, kill switch, shutdown) all ran.

## Q8. Residual RAM drift under sustained load (post FIX-7)
FIX-7 removed the unbounded done-task retention (A/B: 12×200 KB tasks 18→2,860 MB became
18→21 MB). Two consecutive 8-min soaks still show OS RAM settling ~3 GB higher after heavy
load; per-process evidence attributes it to model servers' working sets (llama-server.exe
~7.3 GB RSS across 3 processes, stable when idle) plus normal standby-cache behavior — NOT a
per-turn leak (idle drift +28 MB over 20 s). *Needed:* a longer soak (24 h+) on the owner's
schedule to confirm the plateau holds. *Until then:* reported as measured plateau, not a leak.

## Q9. SearXNG container has never been created on this box (QA full sweep)
Docker Desktop is installed but the engine is down AND no `ishti-searxng` container exists, so
the primary search provider is permanently unavailable; BingLite intermittently bot-walls
(returned 0 for every query during one stretch). Shopping was found calling the SearXNG provider
DIRECTLY (FIX-11: now uses the full manager cascade), but even the manager degrades to
brand-homepage junk without SearXNG. *Needed:* owner decision — start Docker Desktop + create the
container (`ishti/searxng/settings.yml` has the run line) or accept degraded search. Until then:
shopping/lead-finder/travel rows that need real store results can only be tested for
graceful-decline behaviour, not for priced results.

---

# skill_creator + self-knowledge pass (2026-09-30/10-01) — what is still missing, exactly

Everything below is something this pass could **not** finish or could **not** measure. Nothing here is
guessed: each item says what is missing and what would close it. Evidence for the finished work is in
`TEST_RESULTS.md`; the open defects are ranked in `ISSUES.md` (SC-01..SC-18).

## N21. Confirm the owner details in `owner_profile.yaml` — YOUR DECISION
`owner_profile.yaml` ships with `confirmed: false`, so ISHTI will not state these as fact until you check
them. Seeded from what the repo already records (`NEEDS.md` N15 mentions the name "Ishtiaq"):
name "Ishtiaq Ahmed", preferred address "Ishtiaq", tone "direct, plain language, no jargon",
languages [English, Urdu, Pashto].
*Needed:* your yes, or your corrections. *Until then:* the self-model deliberately omits your name entirely
and the `detail()` view prints "unconfirmed".

## N22. Real code generation was NOT measured for all three acceptance tasks — BLOCKED
The pipeline runs offline with an injected model and is proven end-to-end that way (27/27 tests, 3
consecutive green sandbox runs, packaged DISABLED, gate pending). What is **missing** is measured evidence
of what `ishti-qwen2.5-coder:14b` actually produces for a real skill on this machine, and the VRAM/latency
of a real generation run.
*Needed:* an idle GPU and ~10 minutes. `tools/skill_creator_run.py` is the driver (it forces
`ISHTI_TEST_MODE=1`, samples `/api/ps` before and after so a partial offload cannot hide, and never
auto-answers the owner's gate). *What I measured:* see `TEST_RESULTS.md` for the runs that were attempted.

## N23. `ishti/config.json` names a coder model that is not installed — YOUR DECISION
`llm.models.coder = "qwen3-coder:30b-a3b"`; `ollama list` has no such model, so `ishti/llm.py:89-94`
silently falls back to `ishti-qwen2.5-coder:14b` (the ladder's rung 0, which is what `design.vram_check()`
reports). It works today, but the config is a lie and the fallback only holds while the model list is
readable — `md files/NEEDS.md` already records the failure this causes (`NotFoundError: model 'qwen3:30b-a3b'
not found`, 2026-09-30 09:59).
*Needed:* your OK to set it to `ishti-qwen2.5-coder:14b` (or to pull the 30B, which is 19 GB and does not
fit the 16 GB card). I did not edit it because it is a system config file.

## N24. Codegen-quality harness for the coder rungs — NEEDS-MEASUREMENT
`RESEARCH.md` §R6 established the design limits from static evidence (≤300 lines/module, ≤3500 tokens,
≤5 calls, split anything larger) but there is no harness that replays the real generation prompt N times per
rung and records `py_compile` pass / self-check pass / tokens. `python -m ishti.vram_calibrate <model>`
grades arithmetic, JSON, a table, Urdu, tool-call and vision prompts — **not** codegen.
*Needed:* a script that calls the `op_create` prompt 5-10x per coder rung and tabulates the outcome, run on
an idle GPU.

## N25. Two NEEDS.md files compete — YOUR DECISION
`log_need()` appends runtime gaps to `D:\ISHTI\md files\NEEDS.md` (`ishti/skills/__init__.py:227`); the
curated one you read is `D:\ISHTI\NEEDS.md`. Runtime gaps (e.g. the 2026-09-30 09:59 `NotFoundError`, the two
`GpuBusy` entries) therefore never reach the file you are reading.
*Needed:* a decision on which file is canonical. This pass reports runtime gaps in `TEST_RESULTS.md` and
`FINAL_REPORT.md` instead of silently merging them.

## N26. 119 of 121 skill manifests have no `test_file`; 10 skills have no self-check
Measured 2026-09-30 (see `ISSUES.md` SC-03/SC-04). The per-skill suites exist under `tests/skills/<name>/`;
the manifests just do not point at them, and 10 skills cannot self-verify at all.
*Needed:* a pass that adds `test_file` to each manifest and a self-check to the 10 skills without one.
Deliberately not done here: it is ~129 mechanical edits with no behaviour change, and it touches almost every
manifest in the repo — a much larger review surface than this pass's scope.

## N27. Windows-vs-Mac portability is claimed but unproven for 16 code paths
`owner_profile.yaml` lists a Mac M1 and a Ryzen laptop as machines you use, and 16 shipped references to
`D:\ISHTI` still exist (`ISSUES.md` SC-08). *Needed:* either a decision that Windows-only is acceptable, or a
portability pass using `ishti.ROOT` everywhere plus a run on the other machines.

## N28. Sub-agent research quota exhausted mid-session
All six research agents completed (their output is in `RESEARCH.md`), but the four build agents hit
`Error 429: Daily free limit reached on model deepseek/deepseek-v4.1-flash` and **failed**. They had already
written part of WP-C1, the `scope.py` exception, `sandbox.py`, `vetting.py` and
`tests/test_router_lifecycle_flags.py`, which I reviewed and built on; WP-B and WP-C2 were then executed by
the integrator instead of in parallel. *Needed:* nothing from you — recorded so the schedule in
`IMPLEMENTATION_PLAN.md` §5 (parallel band) is understood as **not** what happened.

## N29. The self-model's identity line is asserted, not derived
"I am ISHTI … built for my owner" is a `[declared]` line with the owner's name deliberately excluded from the
resident block (privacy, `RESEARCH.md` R5 S5). `detail()` carries the name. *Needed:* your confirmation of the
wording (N21) before it is stated as fact anywhere.

