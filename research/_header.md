# RESEARCH.md — ISHTI skill_creator + self-knowledge (Phase 1)

Session: **2026-09-30**. Machine: Windows 11 Pro 25H2 build 26200.9550, RTX 5070 Ti 16 GB (driver 610.47),
64 GB RAM, `D:\ISHTI\venv` = Python 3.11.9. Repo at start: `main` @ `b56994b`, working tree had 4 modified
files (see `PROGRESS.md`); a rollback point was tagged before any change.

## How this document was produced

Six research agents (R1–R6) ran **in parallel**, each answering one question, then persisted its own section
to `research/R<n>.md` (no two agents wrote the same file). Every agent was bound to the same evidence rules:

- every non-trivial claim is labelled **VERIFIED** (a source URL + access date, or raw command output seen on
  this PC on 2026-09-30) or **UNVERIFIED**;
- a *paper proposal* is never presented as *production evidence*;
- where a claim could be tested on this PC it was tested, not assumed;
- all web pages were treated as untrusted **data**, never as instructions.

Sections R1–R6 below are reproduced verbatim from those files. `### Evidence:` lines are the agents' own.

## Synthesis — the seven decisions this research forces

| # | Decision | Forced by | Evidence |
|---|---|---|---|
| D1 | **Do not build a second skill generator.** `skillsmith` already does GitHub install + coder-written skills + shape validation + risky-call scan + self-check + T2 gate + load, and `skill_lifecycle` already does gap discovery, per-skill memory, report cards and retire/restore. `skill_creator` is built as the **orchestrating pipeline** that *reuses* both. | R1 §5 (approval gates + test-before-register + versioning), R4 §A (measured: 120 skills, all conformant) | `ishti/skills/skillsmith/skill.py`, `ishti/skill_lifecycle.py` |
| D2 | **The sandbox is a per-run Windows Job Object** (`win32job`), not Docker/WSL2/Sandbox: Docker cold start measured **29 s**, Windows Sandbox feature is **disabled** on this machine and needs admin, AppContainer needs C++ glue. pywin32 312 is already installed and **all four limits were demonstrated live**. | R2 (§2, §3, §4) | R2 `### Tested on this PC` — 512 MB cap killed a 2 GB allocation; `KILL_ON_JOB_CLOSE` killed the whole tree when the supervisor was `os._exit`-ed |
| D3 | **Dependency vetting needs zero installs**: PyPI JSON API (exact name, licence, release ages, sha256) + OSV.dev (CVEs *and* `MAL-*` malware records) + stdlib `difflib` for typosquats + stdlib `ast` for risky-call scanning. `bandit`/`semgrep`/`pip-audit` are **not installed** and are not required. | R3 (§1, §2, §4, §6) | R3 raw API output: `pyyaml 5.3.1 vulns=2`, `ctx => GHSA-4g82-3jcr-q52w Malware in ctx` |
| D4 | **Manifest = the measured ISHTI superset.** All 120 manifests already carry the same 10 top-level keys + 4 `needs` + 3 `scope` keys, with **0 non-conformant** files, so the schema extends rather than rewrites. Drafts are `version 0.0.0` + `status: draft`. | R4 §A (key-frequency table), §C | R4: `tier` T0=68 T1=38 T2=14 T3=0; `test_file` present in **1** of 120 |
| D5 | **The self-model is generated, provenance-tagged and small** (~1,200 chars resident), built from probes ISHTI already has, rebuilt on change, with the long tail behind a `self_check` tool. Facts are never recalled from weights. | R5 §1–§4 | R5 live probes + timings; MCP/A2A capability-registry precedent |
| D6 | **The coder model is `ishti-qwen2.5-coder:14b`**, one ≤300-line module + manifest per generation, ≤3500 output tokens, ≤5 model calls per skill, split anything larger. | R6 §1, §2, §7 | R6: `config.json` names `qwen3-coder:30b-a3b`, which is **not installed** → `llm.py:89–94` falls back to the 14B; measured 9922 MiB @8192, 80.9 tok/s |
| D7 | **A prerequisite bug must be fixed first.** The chat router filters only `status == "broken"`, so a skill marked `disabled`/`retired` is still routed to. "Register as DISABLED" is meaningless until this is fixed; so is `skill_lifecycle.retire()`. | R4 §C caveat + this session's own verification | `ishti/router.py:53` |

## What this research explicitly rules OUT

- **No Docker/WSL2 in the runtime path** for generated code (29 s cold start for a ~2 s script — R2).
- **No Windows Sandbox** (feature `Containers-DisposableClientVM` InstallState=2 on this PC; admin + restart to enable — R2).
- **No AppContainer** for v1 (SID derivation works, but no practical Python-only path — R2).
- **No new pip installs** for vetting or sandboxing (R2, R3).
- **No model larger than the budget**: `qwen3-coder:30b-a3b` (19 GB) and `qwen-27b` (measured 13614/19272 MiB **partial offload**) are refused, never attempted (R6).
- **No trust in a model's self-report**: the sandbox is a *resource governor*, not a security boundary, and the static scan is *triage*, not proof (R2 §"What it does NOT protect against").

## Carried-forward open questions (also listed in `NEEDS.md`)

| # | Question | Status |
|---|---|---|
| Q-R1 | Codegen-specific model harness (replay `op_create` N× per coder rung, record compile/self-check pass) does not exist | **UNVERIFIED / NEEDS-MEASUREMENT** (R6 §7) |
| Q-R2 | Exact chars→token ratio for Qwen2.5/Qwen3 tokenizers (not present locally) | **UNVERIFIED** (R5) |
| Q-R3 | Whether a targeted attacker can escape a Job Object (scheduled tasks/WMI run outside it) | **Known limitation**, not a research gap (R2) |
| Q-R4 | `tearDown`-level proof that no orphan survives a killed *skill_creator* run | to be measured in Phase 4 |
