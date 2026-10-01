# CUA_FEASIBILITY.md — trycua/cua components on this PC (WP-B)

Session 2026-10-01. Protocol per IMPLEMENTATION_PLAN.md WP-B1; evidence rows carry the producing
command or the BLOCKED reason. Verdicts: **GO / PARTIAL / NO-GO / BLOCKED** per component.
Machine: Windows 11 Pro 25H2 (build 26200.9550), RTX 5070 Ti 16 GB, Python 3.11.9 venv.

## Context measured today
- Ollama **not running** all session (`curl http://127.0.0.1:11434/api/version` → refused; no ollama.exe
  in tasklist; measured 2026-10-01). Per the safety rules the model server is not started un-supervised,
  so every measurement needing a live VRAM/model is BLOCKED (NEEDS.md N24).
- GPU idle baseline: `nvidia-smi` → 1522 / 16303 MiB used (2026-10-01).
- Windows Sandbox on this box: `InstallState=2` (disabled; measured 2026-09-30, skill_creator research).
- trycua/cua facts (README verified 2026-10-01): driver = MCP over stdio, background delivery
  "when the app and platform support it"; Windows driver stable since 2026-05-27 (vendor channels);
  core MIT; `cua-som`/`cua-perception` AGPL-3.0; Lume = Apple-Silicon-only; Fleets = cloud.

## Verdict table

| # | Component | Verdict | Evidence / what is missing |
|---|---|---|---|
| 1 | `cua-driver` install + MCP mode | **BLOCKED** (owner gate) | Install method is `irm https://cua.ai/driver/install.ps1 \| iex`. Read-before-run + pin-version rules require the installer script downloaded and reviewed first, and an upstream installer executing on the owner's PC is an owner-approval action (T2-equivalent). Not run this session. Need: owner OK + a pinned release tag from GitHub releases. |
| 2 | 5-task protocol (Notepad/Calculator/Explorer rename/local-page form/two-window copy-paste), 10 runs each | **BLOCKED** (needs #1) | Protocol fully specified below; nothing was executed because the driver is not installed. |
| 3 | Background claim (no cursor/focus theft) | **BLOCKED** (needs #1) | Vendor claim verified as *claimed* (README), NOT as measured on this PC. |
| 4 | Local grounding: UI Automation tree | **GO** (mechanism) | pywin32 312 present (pip list 2026-10-01); Windows UIA is the same mechanism the repo's desktop skills already use; CPU-only, no VRAM. Live accuracy rows need #1's tasks to measure against. |
| 5 | Local grounding: screenshot + existing vision tiers | **PARTIAL** | In-repo path exists (gemma3 4B/12B via the VRAM governor; 12B needs budget 95% — NEEDS.md N3). All latency/accuracy numbers BLOCKED with Ollama down (N24). |
| 6 | `cua-sandbox` on Windows | **NO-GO for now** | Windows Sandbox feature is disabled on this box (InstallState=2) — enabling it is an owner system decision; repo already has a tested Job-Object sandbox for generated code (skill_creator). Revisit only if the owner wants dual isolation. |
| 7 | `cua-bench` local custom tasks | **PARTIAL / deferred** | Simulated tasks need Python **3.12+** + `uv` (README verified); this venv is 3.11.9. Isolated tool venv is possible but is a new dependency chain for benchmarking only — deferred to an owner decision (N26). |
| 8 | `CUA-S1` grounding models | **NO-GO for now** | Early source-only research release; weights on Hugging Face with per-artifact licenses (README verified). Watch-list, not a dependency. |
| 9 | `Lume` VMs | **NO-GO** | Apple Silicon only; this is x86-64 + NVIDIA. No measurement needed. |
| 10 | `Fleets` / cloud model providers | **EXCLUDED** | Cloud; forbidden by the master prompt and the local-only invariant. |
| 11 | `cua-agent[omni]`, `cua-som`, `cua-perception` | **EXCLUDED** (license) | AGPL-3.0 (README verified). Owner may override explicitly (NEEDS.md N25); default stays out. |

## The 5-task protocol (ready to execute once N22/N24/N25-gate items land)
Each task: 10 runs in a sandbox user context (dedicated test account or a run-as /fresh profile), record
success rate, steps, latency, CPU/RAM/VRAM samples; typing-in-another-window runs concurrently for the
background claim; UIA-first grounding, screenshot-fallback counted separately.

1. Type a fixed 120-char sentence into Notepad; verify via UIA read-back.
2. Compute `6*7` in Calculator; verify the display shows 42.
3. Rename `rename_me.txt` → `renamed.txt` in a sandbox folder via File Explorer; verify by filesystem.
4. Fill 3 fields + submit on a local (localhost) test page in Chrome; verify by server-side receipt.
5. Copy a line from Notepad window A into Notepad window B; verify contents.

## What IS built now (GO/PARTIAL-derived, safe, default-off)
`ishti/skills/desktop_control/` — the skill skeleton with the tier/scope discipline (manifest
`status: "disabled"`, tier T1 inspect / T2 act, per-app allowlist, remote use refused, blocker check
for driver presence). It stays inert until the driver is installed AND the owner enables the skill;
the manifest gate is asserted by test.

Feasibility verdict summary: **nothing integrated this pass**; zero AGPL/cloud components installed;
protocol documented and ready; the block is owner-gated software install + a down model server.
