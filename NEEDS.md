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

## N30. Two sessions were working in this workspace at the same time — deliverables were overwritten
Partway through this pass, **another agent session** (working on "Remote Control (Telegram + WhatsApp) +
cua") committed 5 commits into the same repo: `0f1bb99`, `eedb20e`, `c3dbb91`, `e8a6cfc`, `8f740d9`, `cfd005b`.
Consequences observed, not guessed:
- `git commit -am`-style commits from that session **swept my in-flight, uncommitted files into its commits**;
- it **archived my plan** to `IMPLEMENTATION_PLAN_skillcreator.md` and took the canonical
  `IMPLEMENTATION_PLAN.md` name;
- it **overwrote `RESEARCH.md` (73,080 → 31,311 bytes) and `FINAL_REPORT.md`** with its own content;
- it appended its own `N21`–`N26`, so **`NEEDS.md` now has two different `N21` entries** and two `N22`–`N26` ranges.
My code, tests and my sections in `PROGRESS.md`, `ISSUES.md`, `TEST_RESULTS.md` and `NEEDS.md` were **not**
damaged, and the restored copies `RESEARCH_skillcreator.md` (72,634 B) and `FINAL_REPORT_skillcreator.md`
hold the originals. I deliberately did **not** overwrite the other session's files.
*Needed:* your decision on (a) which of the two sessions is canonical for the shared deliverable names, and
(b) how the `NEEDS.md` numbering collision should be resolved (this section uses N21–N30, theirs uses
N21–N26). *Until then:* both sessions' reports exist side by side under distinct filenames.


---

# Remote Control + cua (2026-10-01) — what is missing or needs the owner

## N22. Telegram bot token + pairing (BLOCKS real-network tests and the live channel)
*Needed:* create the bot with @BotFather, paste the token in Settings → Remote Control → Telegram
(stored in Windows Credential Manager via DPAPI — never in files/logs/git), then click "Pair Telegram"
and send `/pair <code>` to the bot from your own account (that records your numeric ID).
*Until then:* the whole 13-test suite runs against a mock Bot API (`tests/mock_telegram.py`) — the real
bot re-runs the same suite. Channel stays OFF.

## N23. WhatsApp mode decision
Mode 1 (built, default OFF): read/reply ONLY in your own "Message yourself" chat through the already-open
WhatsApp Web tab, human-paced, every send T2-gated. Risk (honest): browser automation of your own account
can get it restricted; unofficial libraries were NOT used and stay refused by default (April/September 2026
malware campaigns documented in RESEARCH.md R2). Official Business Cloud API: needs a public webhook + Meta
account — breaks the no-inbound-ports/local-only design; documented in R2, not built.
*Needed:* your explicit yes (Settings → Remote Control → WhatsApp) for mode 1, or nothing happens.

## N24. Ollama was down all session — live VRAM/grounding rows unmeasured
`curl http://127.0.0.1:11434/api/version` → connection refused (2026-10-01); per the safety rules I do not
start the model server un-supervised. *Needed:* start ISHTI/Ollama once, then I re-run the VRAM-adjacent
rows (voice STT through the governor, image-in tiers, any cua grounding) with measurements. Until then
those rows are BLOCKED, not failed — no partial-offload observations were possible (no model loaded).

## N25. cua-driver install approval (read-before-run gate)
Installing `cua-driver` (official repo only, version pinned) runs an upstream PowerShell installer
(`irm https://cua.ai/driver/install.ps1 | iex`). The installer script will be downloaded, read and
reviewed before execution, but executing an upstream installer on your PC is your call (T2-equivalent).
AGPL components (`cua-agent[omni]`, `cua-som`, `cua-perception`) stay excluded unless you explicitly
approve them. Lume/Fleets/cloud: permanently excluded (Apple-Silicon-only / cloud).
*Needed:* your yes + a pinned release; until then desktop_control stays a disabled skeleton and
CUA_FEASIBILITY.md marks rows BLOCKED.

## N26. cua-bench tooling (optional, deferred)
`cua-bench` needs Python 3.12+ + `uv` (this venv: 3.11.9). An isolated tool venv is possible but adds a
dependency chain for benchmarking only. *Needed:* your preference; until then custom local benchmarks are
deferred without prejudice.

---

# 5-REPO INTEGRATION (session 2026-10-01) — SKIP-WITH-PROOF rows + owner asks

Every SKIP-WITH-PROOF row of `REPO_FEATURE_MATRIX.md` with its proof. None of these skip hard work;
each is impossible, not-allowed, or platform-absurd on this PC.

## N27. ECC-17 — ECC Discord bot / GitHub App / ECC Pro cloud
Proof: they are community/cloud products of the ECC project (README links discord.gg, github.com/apps/
ecc-tools, ecc.tools/pricing, retrieved 2026-10-01) requiring third-party accounts and publishing an
ISHTI instance publicly; no runtime meaning for a local-first single-user assistant. *Needed:* nothing
unless you want a public community bot — that would be its own project.

## N28. ECC-30 — ECC's release CI/publishing pipeline
Proof: `scripts/release.sh`, `scripts/ci/`, release gates target npm + GitHub-App publishing cadence;
ISHTI has no package-registry release train (repo builds locally, `start.bat`/`ISHTI.spec`). *Needed:*
only if you decide ISHTI should publish releases — then we design our own pipeline.

## N29. JEV-22 — hey-jev py2app macOS packaging
Proof: `setup.py` uses py2app + `.icns` + `NSMicrophoneUsageDescription`/`NSAppleEventsUsageDescription`
plist keys — macOS-only build tooling; Windows packaging already exists (desktop/exe_bootstrap,
ISHTI.spec). Nothing to port. *Needed:* nothing.

## N30. JEV-23 — hey-jev cloud calls (TypeSafe Jev, Fish Audio, OpenRouter, OpenAI)
Proof: master-prompt local-only invariant (no cloud LLM/STT/TTS in the running app). Each capability is
replaced piecewise: typed decisions -> ISHTI Laya/local router (JEV-05), TTS -> local Fish Speech
(JEV-09), dictation STT -> local faster-whisper (JEV-03), Q&A -> local planner (JEV-10). *Needed:*
nothing — unless you explicitly opt into a cloud connector later (its own OPT-IN flow).

## N31. R27 — agent-reach install.md/update.md "paste-URL-to-your-agent" flow
Proof: master-prompt supply-chain rule treats those docs as untrusted text; executing them blindly is
exactly the vector they warn about elsewhere (ECC warns of impersonation repos too). ISHTI gets the
vetted capability as `internet_reach` skill code under the normal T2 skill-install gate instead.
*Needed:* nothing.

## N32. COU-05 — jump to the session's terminal window (macOS feature)
Proof: implemented with macOS AppleScript window management (coucou mac sources); coucou's own
Windows build lists it as "not in this version" (windows/README.md, in-repo 2026-10-01). ISHTI already
has `desktop/focus_ishti.py` for bringing its own window forward. *Needed:* nothing, or we design a
Windows terminal-jump as a separate feature request.

## N33. COU-09 — drag-Mochi-onto-window context attach (macOS feature)
Proof: macOS Accessibility/AppleScript implementation; absent from coucou's Windows build. ISHTI's
existing `screen_understand` skill already answers "what's in this window" from a captured region —
the capability exists without the macOS mechanism. *Needed:* nothing.

## N34. COU-19 — built-in chat with Claude (Anthropic cloud API)
Proof: master-prompt local-only invariant; ISHTI's chat skill runs on the local model already, i.e.
strictly better for a local-first assistant. The overlay's chat UI is kept but wired to the local
planner. *Needed:* nothing.

## N35. COU-20 — macOS notch app (SwiftUI/App Store build)
Proof: hardware/platform-only — MacBook notch + Xcode + App Store entitlements
(`NotchBuddy/Resources/*.entitlements`, xcodegen project); this PC is Windows 11 Pro. The idea is
covered by the Windows top-edge island (COU-01). *Needed:* nothing.

## N36. hey-jev has NO LICENSE (owner ask)
The repo ships no LICENSE; default all-rights-reserved applies. We therefore re-implement only
behaviours as original code and copy no source text. *Needed:* optional — if you want certainty (or
to contribute improvements upstream), contact the author asking for an MIT/permissive license; we
continue meanwhile on the clean-room-behaviour path.

## N37. Windows Sandbox feature query needs elevation (conditional)
Measured 2026-10-01: edition is Windows 11 Pro (supports it) but `Get-WindowsOptionalFeature -Online`
requires elevation. Chosen isolation path is Docker/WSL2 (already running, no elevation), so this is
BLOCKED only *if* Docker proves insufficient in T-OD-09..11. *Needed:* your approval of an elevated
`Enable-WindowsOptionalFeature -Online -FeatureName Containers-DisposableClientVM` — only then.

## N38. OPT-IN surfaces awaiting owner enablement (by design, default OFF)
Implemented and inert (status pass 2026-10-02): REACH login-state channels (Twitter/Reddit/FB/IG/
XHS/LinkedIn/Boss) and REACH Exa/mcporter exist in the channel registry and start OFF - `reach
enable <channel>` writes a local boolean only (nothing is sent, no cookies are read), and the
COU-08 integration pills are tested OFF. Each needs your explicit enablement + keys in Credential
Manager before it can send anything, and personal data never flows to a connector you have not
enabled.

NOT built, awaiting an explicit owner ask (these stay `mapped` in REPO_FEATURE_MATRIX): OD-07
Composio connectors (the local MCP connector framework is the replacement path), OD-08 GitHub
create-issue connector (the read-only gh channel exists; write actions stay T2 and unbuilt),
OD-22 third-party remote computer provider. *Needed:* say the word if you want any of these.
