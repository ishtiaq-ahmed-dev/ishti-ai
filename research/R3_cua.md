# R3 — trycua/cua packages (cua-Integrator agent)

Session: 2026-10-01. Primary source read directly: https://github.com/trycua/cua (official repo,
retrieved 2026-10-01). Rule: official repo only — many forks exist; nothing is installed from a fork.

## A. Package-by-package findings

| Package | What it is (verified from README) | License (verified) | Windows support | Local-only fit | Verdict for ISHTI |
|---|---|---|---|---|---|
| `cua-driver` | "Background computer-use driver for any agents… inspect and operate native desktop apps and browsers on macOS, Windows, and Linux… through the CLI, MCP, or typed SDKs. Background delivery lets agents work without moving your pointer or taking focus when the app and platform support it" | MIT (repo LICENSE; LICENSING.md lists per-dir exceptions) | **Windows stable** ("Today we're bringing Cua Driver to Windows… now stable", trycua on X 2026-05-27; blog "inside-windows-computer-use.md" in the repo) | Yes — local driver, MCP over stdio, no cloud dependency for operation | **Feasibility-protocol candidate** (WP-B: install pinned, 5-task protocol, GO/PARTIAL/NO-GO with evidence) |
| `cua-agent` | The agents-loop package (planning agent around a computer) | MIT core; **`cua-agent[omni]` pulls ultralytics = AGPL-3.0** (repo README: "cua-som… licensed under AGPL-3.0-or-later. Its Ultralytics dependency retains its own license") | works where the driver works | yes | **EXCLUDE `omni` extra** (AGPL); plain agent optional later |
| `cua-sandbox` | Sandboxes incl. Windows + Linux for running agents isolated | MIT (per LICENSING.md) | claimed; Windows Sandbox on this box is **disabled** (`InstallState=2`, measured 2026-09-30, RESEARCH.md of the skill_creator session) — enabling it is a Windows-features change = owner decision | yes (local sandbox mode) | Feasibility probe; Windows edition check first |
| `cua-computer-server` | Server side of computer control (used by sandboxes/Fleets) | MIT | n/a for a single-PC personal setup | yes | Not needed (we are the host, not a fleet) |
| `cua-bench` | "Build computer-use tasks, evaluate agents, and export trajectories for training. Start with a simulated task that requires no VM, Docker, or model API key." Python 3.12/3.13 + uv | MIT | tool runs where Python runs; needs **Python 3.12+** — this venv is 3.11.9 (measured) ⇒ isolated tool venv, not ISHTI's | yes (simulated tasks need no VM/model key) | Feasibility probe for local benchmarking of ISHTI's own skills |
| `CUA-S1` | "family of small, specialized System 1 models for computer use… The first research profile focuses on forms… The GitHub component is an early, source-only research release; model weights are hosted separately on Hugging Face. The source is MIT-licensed." | MIT source; **model/dataset cards carry their own licenses** (check before use) | weights = HF download; VRAM cost unmeasured | yes once weights local | Watch; not a dependency now (forms-only profile, early release) |
| `Lume` | "Create and manage local macOS and Linux VMs on Apple Silicon" | MIT | **No** — Apple Silicon only; this is an RTX 5070 Ti Windows box | n/a | **NO-GO** (hardware mismatch, no measurement needed) |
| `Fleets` | "Provision isolated cloud desktops at run.cua.ai" | commercial cloud | n/a | **No** — cloud, excluded by the master prompt | **EXCLUDED** |

## B. Security-relevant facts for WP-B integration
- The driver speaks **MCP over stdio** — fits ISHTI's existing `ishti/mcp_client.py` pattern (Playwright MCP already used that way).
- Background delivery ("without stealing focus/pointer") is **platform-and-app dependent** — must be measured on this PC, not assumed (WP-B test 3 of the master prompt).
- Install method is `irm https://cua.ai/driver/install.ps1 | iex` on Windows — **read-before-run rule applies**: the installer script must be downloaded, read and only then executed, and the version pinned. Until the owner-visible vetting pass, this is treated as a blocked install.
- The `cua-perception` extension (OmniParser detector) is **AGPL-3.0-only** and downloaded from signed GitHub release assets — excluded from any default install (master-prompt license rule).
- Windows support status "stable" is the vendor's claim (their own channels) — the 5-task protocol on this PC is the real evidence WP-B produces.

## C. Pre-decided exclusions (no further research needed)
Lume (Apple Silicon only), Fleets (cloud), `cua-agent[omni]`/cua-som/cua-perception (AGPL unless the owner approves), any cloud model provider. This is a master-prompt rule, not a judgement call.

## D. What WP-B must measure on this PC (the feasibility protocol, evidence → CUA_FEASIBILITY.md)
1. Pinned `cua-driver` install (installer read first) + `--version` + MCP server mode reachable.
2. 5 sandbox tasks × 10 runs: Notepad typing, Calculator, file rename in Explorer (sandbox folder), local-page form in Chrome, two-window copy/paste. Success rate, steps, latency, CPU/RAM.
3. Background claim: type into another window while ISHTI acts — focus stolen or not.
4. Local grounding: UI Automation tree first; screenshot+local-vision fallback measured for accuracy/latency (no partial offload).
5. `cua-sandbox` Windows edition check + startup time + host-isolation probe.
6. `cua-bench` custom ISHTI task + trajectory export (isolated venv).
