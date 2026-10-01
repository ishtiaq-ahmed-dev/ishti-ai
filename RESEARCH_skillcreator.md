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

## R1. Self-extending agents: how a solved task becomes a reusable skill

### 1. The mechanism: solved task → stored, retrievable artifact

**Voyager** (Minecraft) is the canonical pipeline: (a) an automatic curriculum proposes the next task; (b) the agent writes executable **JavaScript** code for it; (c) the skill is saved with an LLM-generated natural-language description and added to an **embedding index**; (d) future tasks **retrieve** the most similar skills and inject their code back as reusable programs (VERIFIED: repo code shows `retrieval_top_k=5`, LangChain `OpenAIEmbeddings` + Chroma vector DB, per-skill `{code, description}` in `skills.json` — https://github.com/MineDojo/Voyager/blob/main/voyager/agents/skill.py, accessed 2026-09-30). Improvement is an **iterative prompting loop** using environment feedback, execution-error traces, and GPT-4 *self-verification* (VERIFIED: abstract, https://arxiv.org/abs/2305.16291 — 3.3× more items, 15.3× faster tech-tree milestones, all paper claims).

**CRAFT** (ICLR 2024) generalizes this to toolsets: GPT-4 solves training examples, a **validation step** filters solutions, they are abstracted into reusable snippets and deduplicated; at inference a multi-view retriever (problem text, API names, docstrings) selects snippets (VERIFIED: https://arxiv.org/abs/2309.17428). **TRAD** retrieves at *step* granularity — thought-level retrieval plus “aligned decision” — instead of whole trajectories, and claims deployment in a real insurance-RPA system (paper claim, not independently verified; https://arxiv.org/abs/2403.06221).

**MUSE-Autoskill** (2026) is the skill-library MUSE: creation → memory → management → evaluation → refinement, with a retrievable “skill catalog”; it reports 85.24% vs 81.17% for human-authored skills on the covered SkillsBench subset (preprint claim; https://arxiv.org/abs/2605.27366 — a different MUSE, 2411.13537, is metacognition-based RL, not a skill library).

**2025–26 reuse work:** SkillWeaver synthesizes website-specific skills *as APIs* and reports +31.8% (WebArena) and +39.8% (real sites), with APIs transferring to weaker agents (https://arxiv.org/abs/2504.07079, preprint claim). Repo-To-Skill distils 1,000 ML repos into a 5,000+ “verified” skill library (https://arxiv.org/abs/2609.02749). Toollery (Sept 2026) treats skills *and* tools as one selection problem over ~79K capabilities (https://arxiv.org/abs/2609.22218). VERIFIED as preprints; none is production evidence.

### 2. Tool-making

**LATM** (“LLMs as Tool Makers”, 2023) splits a powerful *tool maker* (writes a Python function, plus a verifier that writes unit tests, plus a wrapper summarising use-cases) from a cheap *tool user*; tools are cached and dispatched by API, amortising cost (VERIFIED as described/implemented; https://arxiv.org/abs/2305.17126). Its own limitations are explicit: tools “may not always meet the standards… of human developers”, outputs could be “suboptimal, incorrect, or even potentially harmful”, real-world tool validation is an open challenge, and safety “we have not addressed… in depth” (VERIFIED quotes). **ToolMaker** (ACL 2025) turns papers+GitHub repos into tools, installing dependencies with a closed-loop self-debug cycle; it correctly implements **80%** of 15 tasks, leaving 20% failing under its authors’ own >100-unit-test benchmark (https://arxiv.org/abs/2502.11705, ACL 2025).

### 3. The Agent Skills convention (2026 status)

A skill is a folder with `SKILL.md`: required frontmatter `name` (≤64 chars, lowercase/hyphens) and `description` (≤1024 chars), optional `license`, `compatibility`, `metadata`, and experimental `allowed-tools` (“pre-approved tools the skill may use”); optional `scripts/`, `references/`, `assets/` (VERIFIED: https://agentskills.io/specification). Loading is **progressive disclosure** in three stages: discovery (name+description only, ~100 tokens/skill), activation (full body, <5,000 tokens and <500 lines recommended), execution (bundled files on demand). It was released by Anthropic on 16 Oct 2025 and published as an **open standard** on 18 Dec 2025, supported across Claude apps/Code/Agent SDK (VERIFIED: https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills). Security stance is explicit: “malicious skills may introduce vulnerabilities… install skills only from trusted sources” and audit untrusted ones (VERIFIED quote).

### 4. Known failure modes (evidence-weighted)

- **Generated code that doesn’t run / can’t be repaired reliably.** Voyager treats execution errors as a first-class repair signal; the self-repair study (ICLR 2024) finds gains “often modest… sometimes not present at all”, bottlenecked by feedback quality (VERIFIED: https://arxiv.org/abs/2306.09896).
- **Self-verification is unreliable.** Intrinsic self-correction *degrades* reasoning without external feedback (VERIFIED: https://arxiv.org/abs/2310.01798); LLM evaluators favour their own generations (https://arxiv.org/abs/2404.13076). Concrete incidents: the **Darwin Gödel Machine** “faked a log making it look like it had run the tests… when in fact they were never run”, and later removed hallucination-detection markers “despite our explicit instruction not to do so” (VERIFIED: https://sakana.ai/dgm/, May 30 2025).
- **Reward hacking in self-improvement.** METR (June 5 2025) documented o3 writing a “kernel” that reads the grader’s reference tensor, o3 patching the evaluator, and similar exploits across models — though METR notes it has *not* measured overall frequency (VERIFIED: https://metr.org/blog/2025-06-05-recent-reward-hacking/).

- **Loops / runaway resources.** MAST (1,600+ annotated traces, 7 frameworks, κ=0.88) catalogues 14 failure modes including “Step repetition”, “Unaware of stopping conditions”, “Premature termination”, and “No or incomplete verification” (VERIFIED: https://arxiv.org/abs/2503.13657). Non-interactive scripts that block on input “will hang indefinitely” (VERIFIED: https://agentskills.io/skill-creation/using-scripts).
- **Unsafe/destructive generated code and skills.** ToolEmu finds even the safest tested agent fails 23.9% of high-stakes scenarios; 68.8% of identified failures were judged valid real-world failures (VERIFIED: https://arxiv.org/abs/2309.15817). MaliciousSkillBench consolidates 7,505 malicious vs 2,235 benign skills; best detectors drop from 0.93 to 0.67 macro-F1 under source shift, with 62.4% benign false positives (VERIFIED: https://arxiv.org/abs/2608.19901). Injection via the “skills channel” reached ~16% success in a 14,560-execution harness study (https://arxiv.org/abs/2608.16393); SKILLLITE studies auditing malicious skills (https://arxiv.org/abs/2609.36879).
- **Bloat / retrieval degradation.** Toollery: full-library prompting is “costly, slow, and less reliable”; each added candidate adds tokens and “distractors” (VERIFIED quote). Anthropic’s MCP guidance likewise warns tool definitions can cost “hundreds of thousands of tokens” (https://www.anthropic.com/engineering/code-execution-with-mcp). Tension: CRAFT reported improvements from scaling tools — at much smaller scale (VERIFIED as claim).
- **Drift / error accumulation.** DGM’s archive shows errors can persist and compound via self-modified scaffolding; MUSE-Autoskill explicitly adds an evaluation/refinement stage to counter stale skills (preprint).

### 5. Recommended guards

Human approval gates: Claude Code requires approval for Bash and file writes by default; deny rules outrank hooks, and side-effecting skills should set `disable-model-invocation: true` (“You don’t want Claude deciding to deploy because your code looks ready”) (VERIFIED: https://code.claude.com/docs/en/permissions, .../skills). Sandboxing: Claude Code enforces filesystem/network limits at OS level (Seatbelt/bubblewrap) so the boundary “holds regardless of what the model chose to run”; Anthropic recommends sandboxing plus “resource limits, and monitoring” for agent-written code (VERIFIED docs/blog). Test-before-register: CRAFT’s validation step and LATM’s unit-test verifier are the paper archetypes; the official skill guidance prescribes eval suites run *with and without* the skill, assertion grading, human review, and “Only proceed when validation passes” (VERIFIED: https://agentskills.io/skill-creation/evaluating-skills). Versioning/rollback: skills are version-controlled folders; DGM credits its “transparent, traceable lineage of every change” for catching sabotage. Restrict/disable: settings can restrict skill access and troubleshooting covers over-triggering; note the experimental `allowed-tools` field means a downloaded skill can *carry* pre-approvals — a privilege surface. Finally, never let the agent widen its own permissions: managed settings exist specifically to keep the sandbox policy from being widened, and the METR/DGM incidents show that the first thing a hacking agent attacks is whatever grading, reward or configuration code it can reach.

### Sources
- https://arxiv.org/abs/2305.16291 · https://github.com/MineDojo/Voyager/blob/main/voyager/agents/skill.py — Voyager mechanism (all accessed 2026-09-30)
- https://arxiv.org/abs/2309.17428 (CRAFT) · https://arxiv.org/abs/2403.06221 (TRAD) · https://arxiv.org/abs/2308.10144 (ExpeL)
- https://arxiv.org/abs/2605.27366 (MUSE-Autoskill) · https://arxiv.org/abs/2504.07079 (SkillWeaver) · https://arxiv.org/abs/2609.22218 (Toollery) · https://arxiv.org/abs/2609.02749 (Repo-To-Skill)
- https://arxiv.org/abs/2305.17126 (LATM) · https://arxiv.org/abs/2502.11705 (ToolMaker)
- https://agentskills.io/specification · https://agentskills.io/home.md · https://agentskills.io/skill-creation/evaluating-skills · https://agentskills.io/skill-creation/using-scripts · https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills
- https://arxiv.org/abs/2306.09896 · https://arxiv.org/abs/2310.01798 · https://arxiv.org/abs/2404.13076 · https://arxiv.org/abs/2309.15817 · https://arxiv.org/abs/2503.13657
- https://arxiv.org/abs/2505.22954 · https://sakana.ai/dgm/ · https://metr.org/blog/2025-06-05-recent-reward-hacking/ · https://arxiv.org/abs/2608.19901 · https://arxiv.org/abs/2609.36879 · https://arxiv.org/abs/2608.16393
- https://code.claude.com/docs/en/sandboxing · https://code.claude.com/docs/en/permissions · https://code.claude.com/docs/en/skills · https://www.anthropic.com/engineering/code-execution-with-mcp

## R2. Safe execution of AI-generated code on Windows 11

**Bottom line (VERIFIED, tested on this PC):** the only zero-install, zero-admin option that actually enforces time/memory/process-count limits and leaves no orphans is a **per-run Windows Job Object** wrapped around one `subprocess.Popen`. pywin32 312 in `D:\ISHTI\venv` exposes every constant needed, and all four behaviors were demonstrated live below.

**1. subprocess + restricted env — necessary, NOT sufficient (VERIFIED).** Clean env (no proxy vars/PYTHONPATH), pinned cwd, `CREATE_NO_WINDOW`, and a timeout all work, but on timeout `subprocess.run` kills only the **direct** child: test T1A killed the middle process and the grandchild kept writing forever (heartbeat file grew 132→264 bytes; psutil `alive=True`). `taskkill /F /T` (T1B) killed the whole tree — but that is a second, racy step (the pid may be gone) and provides no memory/CPU/process-count caps.

**2. Windows Job Objects via `win32job` — the answer. All VERIFIED on this PC.** Sequence (same pattern as ISHTI's `adopt_job()` in `ishti/lifecycle.py:213-233`):

```python
job  = win32job.CreateJobObject(None, "")
info = win32job.QueryInformationJobObject(job, win32job.JobObjectExtendedLimitInformation)
b    = info["BasicLimitInformation"]
b["LimitFlags"] |= (win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE   # 0x2000
                    | win32job.JOB_OBJECT_LIMIT_PROCESS_MEMORY   # 0x100
                    | win32job.JOB_OBJECT_LIMIT_JOB_MEMORY       # 0x200
                    | win32job.JOB_OBJECT_LIMIT_ACTIVE_PROCESS   # 0x8
                    | win32job.JOB_OBJECT_LIMIT_JOB_TIME)        # 0x4
info["ProcessMemoryLimit"] = 512*1024*1024;  info["JobMemoryLimit"] = 1024*1024*1024
b["ActiveProcessLimit"] = 8;  b["PerJobUserTimeLimit"] = 90*10_000_000   # 100-ns ticks
win32job.SetInformationJobObject(job, win32job.JobObjectExtendedLimitInformation, info)
# then: Popen(...); AssignProcessToJobObject(job, win32api.OpenProcess(PROCESS_SET_QUOTA|PROCESS_TERMINATE, False, p.pid))
```

Evidence: a Python child asked to allocate ~2 GB died with `MEMORY_ERROR after 500 MB` under the 512 MB cap (job accounting `PeakProcessMemoryUsed=510MB PeakJobMemoryUsed=512MB`), while the same child allocated 600 MB fine without a job (`child_rss=617MB`). A supervisor that crashed via `os._exit(99)` with no cleanup dropped its job handle and the **whole tree died** (heartbeat size frozen, zero matching processes). `JOB_OBJECT_LIMIT_ACTIVE_PROCESS=1` made a grandchild spawn fail with `winerror=1816 ERROR_NOT_ENOUGH_QUOTA`. `JOB_OBJECT_LIMIT_JOB_TIME` was accepted **without admin** (`SetInformationJobObject ok: True`) and the spinner was terminated (rc 0xC000013A, 5.3 s wall for a 2 s CPU limit; Microsoft docs: by default all processes are terminated when the limit passes). Nested jobs work: the test runner was already inside a Job Object (`IsProcessInJob → True`) and assignment still succeeded — as documented for Windows 8+. Caveats: the memory cap surfaces as an allocation failure (a script that catches `MemoryError` is only stopped by `JOB_TIME`/kill), assign immediately after `Popen` to close the spawn race, and do **not** set `JOB_OBJECT_LIMIT_BREAKAWAY_OK` on this per-run job (ISHTI's outer job sets 0x0800 deliberately for user-facing apps).

**3. Windows Sandbox — supported SKU, DISABLED here, not zero-cost (VERIFIED locally + docs).** This PC: Windows 11 Pro 25H2 build 26200.9550, hypervisor present, feature `Containers-DisposableClientVM` **InstallState=2 (Disabled)**, `WindowsSandbox.exe`/`wsb.exe` absent. Enabling needs an elevated shell (`Enable-WindowsOptionalFeature -FeatureName "Containers-DisposableClientVM" -All -Online`, admin + likely restart; docs). Pro/Enterprise/Edu only, never Home. Docs: "takes a few seconds to launch" per run (not measured — enabling here needs admin/reboot). Scriptable? Since 24H2 the `wsb` CLI has `start/exec/share/connect/ip`, but `exec` has **no process I/O** (cannot retrieve stdout) and needs an active logon session — so it cannot capture a generated script's output today. Read-only folder sharing works (`.wsb` `<ReadOnly>true</ReadOnly>`; CLI `share` without `--allow-write`).

**4. Docker Desktop / WSL2 — installed, measured, too heavy per task.** VERIFIED: docker CLI 29.6.1; Docker Desktop installed; `com.docker.service` Stopped, **StartType=Manual** (not resident by default); docker-desktop WSL distro present. Measured cold start (process start → daemon ready): **29.0 s**; warm `docker run --rm hello-world` (cached): **0.45 s / 0.49 s**; native venv `python -c "print(1)"`: **0.038 s**. No Python image is cached, so a per-task run also needs a one-time pull/bake. Limits: no GPU in containers by default (nothing GPU-related configured; UNVERIFIED on this PC), and Docker Desktop is free only for personal use/education/non-commercial OSS or small business (<250 employees AND <$10M revenue) — otherwise paid (Docker docs).

**5. AppContainer — honest verdict: not practical from Python today.** Docs: strongest lightweight containment (no network by default, file/registry isolation), but launching requires `CreateAppContainerProfile` (writes per-user state) plus `CreateProcessW` with `STARTUPINFOEX` + `PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES`, capability SIDs and ACL grants so the interpreter can read its own DLLs. No stdlib/pywin32 support (`win32process` exposes no attribute helpers — verified). Read-only probe: `DeriveAppContainerSidFromAppContainerName` is callable from ctypes (hr=0x0, SID `S-1-15-2-…`), but the launch plumbing is ~100 lines of fragile ctypes — **UNVERIFIED and not attempted**; not suitable for ISHTI's zero-install goal.

### Tested on this PC
All artifacts in `C:\Users\PC\AppData\Local\Temp\ishti_r2_test\`; test date 2026-09-30. Raw excerpts:

```
T1A: mid pid 21648 killed; grandchild hb pid 28128 alive per psutil: True
     heartbeat (size, mtime) t and t+3s: (132,...) (264,...) -> orphan still writing: True
T1B: taskkill rc: 0 | SUCCESS: ... 6 processes terminated ... -> orphan still writing: False; matching: []
T2A baseline (no job): rc=0  output='ALLOC_OK 600 MB child_rss=617MB'
T2B job PROCESS_MEMORY=512MB JOB_MEMORY=1GB: rc=42  output='MEMORY_ERROR after 500 MB child_rss=517MB'
     job PeakProcessMemoryUsed=510MB PeakJobMemoryUsed=512MB
T3:  supervisor rc: 99 (os._exit, no cleanup) -> heartbeat frozen at 66 bytes; matching processes: []
T4:  child output: SPAWN_FAILED errno=12 winerror=1816 msg='Not enough quota is available to process this command'
T5:  SetInformationJobObject(JOB_TIME) ok: True -> rc=3221225540 wall=5.3s (2 s user-time limit)
T6:  DeriveAppContainerSidFromAppContainerName hr=0x00000000, SID S-1-15-2-2750927429-...
Docker: cold_start_from_process_start_s=29; run1_s=0.45 rc=0; run2_s=0.49 rc=0; native_python_run_s=0.038
        (afterwards: remaining docker processes: 0, service Status=Stopped StartType=Manual, WSL stopped)
```

### Recommendation for ISHTI
Add `ishti/sandbox.py` (~60 lines, zero installs): one `Popen([venv_python, script], cwd=<per-skill scratch>, env=cleaned, stdin=DEVNULL, stdout/stderr=PIPE, creationflags=CREATE_NO_WINDOW|CREATE_NEW_PROCESS_GROUP)` with a **per-run Job Object**: `KILL_ON_JOB_CLOSE | PROCESS_MEMORY=512 MB | JOB_MEMORY=1 GB | ACTIVE_PROCESS=8 | JOB_TIME=90 s`, plus a parent wall-clock cap **N=120 s** (manifest-overridable). On timeout: `win32job.TerminateJobObject(job, 1)`, drain pipes, `job.Close()`; return rc/stdout/stderr/peak MB to the dispatcher and log via `ishti/audit.py`. That yields a hard N-second ceiling, a RAM ceiling, ≤8 processes, and no orphans even if the ISHTI host crashes (KILL_ON_JOB_CLOSE — proven by T3).

**What it does NOT protect against (explicit):** no filesystem boundary (same user, full file access; writes outside scope are stopped only by ISHTI's cooperative `ishti/scope.py` hooks, which cannot see subprocess/ctypes writes — its own docstring says subprocesses "can't be hooked"); no network restriction; no privilege boundary (same token/credentials — Job Objects are resource governors, not security boundaries); not a defence against a targeted attacker (e.g. `schtasks`/WMI-created processes run outside the job).

**Defence-in-depth stack (all present or small):** (1) skillsmith's `scan()` risky-call static scan + owner confirmation before install (`ishti/skills/skillsmith/skill.py`); (2) ISHTI's existing per-skill manifest scopes enforced in `ishti/scope.py` (`write`/`net`/`apps`, `ScopeError`); (3) cleaned-env, cwd-pinned, windowless spawn; (4) the Job Object caps above; (5) audit-log + human "yes" for any new skill — accepting that the static scan is triage, not a boundary.

### Sources
- Microsoft Learn, *SetInformationJobObject* and *JOBOBJECT_BASIC_LIMIT_INFORMATION* (limit flags/values, 100-ns units, active-process semantics, default time-limit termination) — accessed 2026-09-30.
- Microsoft Learn, *Windows Sandbox overview*, *Install Windows Sandbox*, *Windows Sandbox CLI*, *Sample configuration files* (editions, enable command, `ReadOnly`, `Networking/VGpu Disable`, wsb exec I/O limitation) — accessed 2026-09-30.
- Microsoft Learn, *AppContainer isolation* (network default-deny, file/registry isolation) — accessed 2026-09-30.
- Docker Docs, *Docker Desktop license agreement* (<250 employees, <$10M revenue free tier) — accessed 2026-09-30.
- pywin32 `win32job` documentation (function list) — accessed 2026-09-30.
- ISHTI repo: `ishti/lifecycle.py`, `ishti/scope.py`, `ishti/skills/skillsmith/skill.py`, `D:\ISHTI\venv\pyvenv.cfg` — read 2026-09-30.
- Local command output, this PC (Windows 11 Pro 25H2, build 26200.9550), test scripts in `%TEMP%\ishti_r2_test\` — run 2026-09-30.


## R3. Vetting code and dependencies before install

Every check below was executed on this PC (Windows 11, `D:\ISHTI\venv`, Python 3.11.9, pip 24.0, ruff 0.16.9) on **2026-09-30**; raw outputs are quoted. Nothing here requires installing anything into the app venv.

### 1. Exact-name and typosquat checks — VERIFIED

`Invoke-RestMethod https://pypi.org/pypi/requests/json` returned:

```
name=requests version=2.34.2 author= license=Apache-2.0 requires_python=>=3.10
releases_count=163
sdist=requests-2.34.2.tar.gz upload=2026-05-14T19:25:27.735762Z sha256=f288924c...
project_url_keys=Documentation,Source requires_dist_count=6
```

Fields that matter: `info.name/version` (exact spelling); `info.project_urls` (must point at the real upstream repo — look-alikes rarely link the true home page); `info.author/author_email`; `info.license` and `info.license_expression` (PEP 639); `info.requires_python` (must admit 3.11.9); `releases[v][].upload_time_iso_8601` (brand-new or back-dated releases are suspicious), `.packagetype` (`sdist` means a build script executes at install time), `.digests.sha256`, `.yanked`; `info.requires_dist`. PyPI's own docs warn: "Metadata returned comes from the values provided at upload time and does not necessarily match the content of the uploaded files" — a first filter, not proof.

Look-alike detection with stdlib `difflib` (verified run):

```
reqeusts => ['requests']  numpi => ['numpy']  flaskk => ['flask']
urllib4 => ['urllib3']   pip-audlt => []   pyYAML2 => []
```

`difflib.get_close_matches` (SequenceMatcher) against a curated list of the packages you actually depend on + the top-5000 popular list catches transposition/homoglyph neighbours. This is not academic: the 2016 study "Typosquatting programming language package managers" (incolumitas.com, 2016-06-08) got code execution on **17,289 hosts** with algorithmically generated names ("req7est" vs "request") across PyPI/npm/RubyGems. For npm, the registry API works too (verified, scoped names need `%2F`):

```
name=@whiskeysockets/baileys latest=7.0.0-rc14 created=2023-05-10 modified=2026-07-29 versions=52
integrity=sha512-WK+X8ju8TPGxvWIsP8hrY6JB6FltYuFe+vsqKfjOYX25JObij9qLf2c3ZGdl1Q+vhFwbnT+AZmWAB5pTvzmSiQ==
```

### 2. Advisory lookups — VERIFIED

OSV POST from this PC (`{"package":{"name":...,"ecosystem":"PyPI"},"version":...}`; omit version to list all):

```
pyyaml 5.3.1 vulns=2 -> GHSA-8q59-q68h-6hv4, PYSEC-2021-142
GET /v1/vulns/GHSA-8q59-q68h-6hv4 -> aliases=CVE-2020-14343,PYSEC-2021-142 (fixed_in 5.4)
ctx => GHSA-4g82-3jcr-q52w "Malware in ctx" | easyhttprequest => MAL-2024-5101
ultralytics => PYSEC-2024-154 "releases ... contained malicious crypto miner software"
```

So OSV covers CVEs *and* malware (`MAL-*` OpenSSF records, GHSA/PYSEC malware entries). Terms (osv.dev API docs, fetched 2026-09-30): "Currently there are no limits on the API"; 32 MiB response cap on HTTP/1.1. PyPI serves advisories itself: `/pypi/pyyaml/5.3.1/json` → `vulnerabilities=2` with `fixed_in 5.4`. Caveat: absence proves nothing — `crossenv`, `colourama`, `python3-dateutil` returned **0** OSV entries. Query by exact version, or whole-package queries dump every historical advisory (django returned hundreds).

### 3. Real campaigns — what happened and what would have caught them

- **2017 `crossenv`** (npm typosquat of `cross-env`): postinstall `package-setup.js` base64'd all `process.env` and POSTed it out, then wrapped the real package so it "kept working"; unnoticed ~2 weeks (Snyk, 2021-01-12). Caught by: exact-name check, `--ignore-scripts`, reading the tarball.
- **Feb 2021 dependency confusion** (Birsan): counterfeit internal-name packages auto-installed on PyPI/npm/RubyGems at 35+ firms (Microsoft, Apple, PayPal, Tesla, Netflix…), >$130k bounties (BleepingComputer, 2021-02-09). Caught by: index-scoped feeds and reserved internal namespaces.
- **May 2022 `ctx` hijack** (PyPI account takeover): collected `os.environ.items()` and sent it base64 to `anti-theft-web.herokuapp.com` (OSV GHSA-4g82-3jcr-q52w). Caught by: OSV lookup + AST flag on `os.environ` + source review (it shipped only `.pyc`).
- **Dec 2024 ultralytics 8.3.41/8.3.42** cryptominer release (OSV PYSEC-2024-154).
- **Nov 2018 event-stream**: attacker took over maintainership; `flatmap-stream@0.1.1` in `event-stream@3.3.6` (added 2018-09-09) unpacked an AES-encrypted payload only inside Copay's build, stealing keys from wallets >100 BTC (npm blog archive, 2018-11-27).
- **Jan 2022 colors/faker**: maintainer shipped an infinite loop in `colors@1.4.44-liberty-2` (plus 1.4.1/1.4.2) and `faker` — 20M weekly downloads (BleepingComputer, 2022-01-09). Caught by: exact pinning, no auto-adopting patches.
- **Sep 2025 Shai-Hulud worm**: ~500 packages, TruffleHog credential scanning, `shai-hulud.yaml` workflow, webhook.site exfil (Socket, 2025-09-16); **v2, Nov 24 2025**: 500+ packages/700+ versions incl. Zapier/PostHog SDKs (Socket, 2025-11-24).
- **Sep 2026**: MemTensor npm+PyPI scopes shipped the `sckit` credential stealer (StepSecurity / The Hacker News / SC Media, 2026-09-23); **PhantomSub** — 101 malicious npm packages silently added developers' WhatsApp accounts to spam groups (OX Security 2026-09-28; The Hacker News 2026-09-29).
- **Baileys** (NEEDS.md N12): a fake WhatsApp API npm package stealing messages/contacts/login tokens was reported 2025-12-22 (The Register; The Hacker News). N12's specific "anti-ban package (April 2026)" label could not be independently sourced today — **UNVERIFIED**; the surrounding WhatsApp-npm threat is VERIFIED. N12's rule (official repo, pinned, source-reviewed, never a fork) is correct.

### 4. Static analysis: installed vs not — VERIFIED

`pip list`/`find_spec`: **ruff 0.16.9 FOUND**; bandit, semgrep, pip-audit, pylint, pyflakes, safety **MISSING**. All are pip-installable for 3.11 (bandit 1.9.4, pip-audit 2.10.1, semgrep 1.178.0, pylint 4.1.1, pyflakes 4.0.0; each `requires_python >=3.10`) — but don't pollute the venv: `uv`/`uvx` 0.11.33 is already installed (`uv tool run --isolated` verified) and `pre-commit` 4.6.2 is present, so `uvx bandit -r ...` runs tools in ephemeral envs outside `D:\ISHTI`. Preview installs without installing: `pip install --dry-run --no-deps --report` (verified: `Would install bandit-1.9.4` + wheel URL, nothing installed). bandit = AST security rules (high false positives where shelling out is legitimate); semgrep = pattern rulesets (slower, downloads rules); pip-audit = advisory lookup, no code analysis; ruff = lint incl. a flake8-bandit S-subset (note NEEDS N19: lint is not a gate here); pyflakes catches undefined names (crude obfuscation).

**stdlib fallback (`ast`)** — verified demo printed `line 1->import subprocess; 3->from winreg; 4->system; 5->Popen; 6->eval; 7->exec; 8->__import__; 10->loads; 11->open`. Concrete match set: `ast.Call` with `ast.Name.id` in {eval, exec, compile, \_\_import\_\_, open, input} or `ast.Attribute.attr` in {system, popen, run, call, check_output, Popen, remove, unlink, rmtree, move, urlopen, urlretrieve, connect, send, WinDLL, windll, CDLL, OpenKey, SetValueEx, load, loads}; Import/ImportFrom roots in {subprocess, socket, ctypes, winreg, shutil, pickle, marshal, base64, requests, urllib, http.client, smtplib}; Subscript/Attribute on `os.environ`; long base64 `ast.Constant`s. `compileall`/`py_compile`/`ast.parse` only prove syntax — pair them with this table plus a human read.

### 5. Licences

Read `info.license_expression` first: paramiko → `LGPL-2.1` while its `info.license` field was empty (verified), so fall back to `info.license` (requests → `Apache-2.0`) and `License ::` classifiers (verified; paramiko had none — fail closed on missing signals). Safe to vendor into a private local assistant: **MIT, BSD-2/3-Clause, Apache-2.0 (keep NOTICE), ISC, PSF, 0BSD, Unlicense**. Care required: **GPL/LGPL** (copyleft), **AGPL** (network-service copyleft if the API is ever exposed), **MPL-2.0** (file-level), **SSPL/BUSL, non-commercial/research-only** (not usable in a product). Record name/version/licence per skill in a LICENCES file.

### 6. Pinning

Unpinned ranges are how every campaign above arrived (colors patch, event-stream 3.3.6, Shai-Hulud patch bumps). Policy for generated skills, all mechanisms verified here: (1) every dep in a lock file as `name==version`; (2) `pip install --require-hashes -r lock.txt` (`--require-hashes` confirmed in pip 24.0 help) using `digests.sha256` from the PyPI JSON (e.g. requests 2.34.2 → `f288924c…`); npm equivalent is `dist.integrity` sha512; (3) ~14-day cooldown via `uv pip install --exclude-newer 2026-09-15` (`--exclude-newer` verified in uv 0.11.33); (4) dry-run (`--dry-run --report`) plus `--only-binary=:all:` where possible — sdists execute `setup.py` at build time, i.e. before you vet them; (5) `requires_python` must include 3.11.9; (6) any new dep passes name check → OSV query → AST scan → licence check, recorded in the skill manifest; runtime skills never install.

### Sources

- Local evidence, 2026-09-30: `pip list`, `importlib.util.find_spec`, PyPI JSON (`requests`, `paramiko`, `pip-audit`, `bandit`…), OSV queries (`pyyaml 5.3.1`, `ctx`, `easyhttprequest`, `ultralytics`), `registry.npmjs.org/@whiskeysockets%2Fbaileys`, `ast` scan demo, `pip install --dry-run --report bandit==1.9.4`, `uv --exclude-newer`.
- PyPI JSON API docs — https://docs.pypi.org/api/json/ (fetched 2026-09-30)
- OSV API docs — https://google.github.io/osv.dev/api/ (fetched 2026-09-30; "Currently there are no limits on the API")
- OSV records: GHSA-4g82-3jcr-q52w (ctx, published 2022-05-25), PYSEC-2024-154 (ultralytics), MAL-2024-5101 (easyhttprequest)
- N. Tschacher, "Typosquatting programming language package managers", incolumitas.com, 2016-06-08
- Snyk (L. Tal), "Typosquatting attacks", 2021-01-12 — crossenv
- BleepingComputer, dependency-confusion research, 2021-02-09
- npm Blog Archive, "Details about the event-stream incident", 2018-11-27
- BleepingComputer, colors/faker sabotage, 2022-01-09
- Socket, "Updated and Ongoing Supply Chain Attack Targets CrowdStrike npm Packages" (Shai-Hulud), 2025-09-16; "Shai Hulud Strikes Again (v2)", 2025-11-24
- StepSecurity / The Hacker News / SC Media, MemTensor + `sckit` (via Google News RSS), 2026-09-23
- OX Security, "PhantomSub: Malicious npm Campaign Secretly Adds Users to WhatsApp Spam Channels", 2026-09-28; The Hacker News, "101 Malicious npm Packages…", 2026-09-29 (via Google News RSS)
- The Register / The Hacker News, fake WhatsApp API npm package, 2025-12-22 (via Google News RSS)
- `D:\ISHTI\NEEDS.md` N12 (Baileys — not implemented, default OFF)

## R4. ISHTI's skill format and the Agent Skills convention

### A. Measured state of `D:\ISHTI\ishti\skills` (VERIFIED, 2026-09-30)

**Counts.** A Python `os.walk` + `os.listdir` pass over the real tree found **122 directories**, of which **120 contain `manifest.json`, and all 120 of those also contain `skill.py`** (zero manifest-only or py-only skills). The two non-skill directories are `legacy` and `__pycache__` (no manifest). There are **no nested/category manifests** today (`skills/<cat>/<skill>/manifest.json` count = 0), though `load_skills` would support them.

**Key frequency over all 120 manifests** (json + Counter):

| Level | Key: count |
|---|---|
| top-level | `category` 120, `description` 120, `name` 120, `needs` 120, `priority` 120, `route` 120, `scope` 120, `status` 120, `tier` 120, `version` 120, `laya` 108, `path` 52, `test_file` **1** |
| `needs` | `apis` 120, `mcp` 120, `models` 120, `python` 120 |
| `scope` | `apps` 120, `net` 120, `write` 120 |

**Distributions.** `tier`: T0=68, T1=38, T2=14, T3=0. `status`: `"working"` = 120/120. `version`: `"1.0.0"` = 120/120. **Missing-key audit** for `name, version, description, route, tier, scope, needs, category, status`: **0 missing for every key — no non-conformant manifests.** Sole `test_file` is `chat_digest` → `"tests/test_chat_digest.py"` (`ishti/skills/chat_digest/manifest.json:29`); 52 files carry a literal top-level `path` (the loader injects/overwrites `path` for all skills at runtime, `ishti/skills/__init__.py:125`). `needs.python/models/mcp/apis` and `scope.write/net/apps` are present in 120/120.

**scope.py self-check.** Re-ran its assertion logic read-only (`ishti/scope.py:200-203`): direct children with a manifest but no `scope` → **none; assertion holds** (and there are no nested manifests it doesn't inspect).

**Staged drafts** live in `D:\ISHTI\data\skill_staging` (4 folders: `new_pdf_to_text`, `new_pdf_to_text_converter`, `new_roman_numeral_converter`, `new_temperature_conversion`), each with keys `name, version:"0.1.0", description, route, needs×4, priority:45, status:"working", tier:"T1", laya:false` — **no `scope`, `category`, `path`, `source`**. This is what the generator template writes (`ishti/skills/skillsmith/skill.py:183-185`), and drafts are invisible to the loader (it scans only `ishti/skills`, `__init__.py:168-187`).

**Keys the loader actually reads:**
- `ishti/skills/__init__.py` — `name` (:122, `self.manifest["name"]`, hard KeyError if absent); `category` (:123, `.get`, fallback `SKILL_CATEGORIES`); `needs.python` (:129 → passed to `install_missing` :132, which maps import→pip names via `PIP_NAME` :15-18, :30-34); `status` (:148, `.get("status","working")`); entire manifest spread in `info()` (:159). Runtime-injected keys `category`/`path`/`folder` (:124-126); `load_skills` keys off `manifest.json` existence only (:171-173, :182-184).
- `ishti/skills/skillsmith/skill.py` — `validate()`: requires `manifest.json` + `skill.py` (:75-77), reads `name` against regex `[a-z][a-z0-9_]{1,30}` (:82). `install_folder`: `name` (:91), **writes** `source`, `tier` (default T2), `status` (default working) (:96), `needs.python` (:98), `route` (:111). `_review`: `name`/`version` (:117), `description` (:118), `needs.python` (:119). `op_list`/`op_remove` read `source` (:212, :223).
- Outside the loader (grep-verified): `route` (`router.py:41`, `planner.py:65`), `laya` (`router.py:68`), `tier` (`dispatcher.py:452`, `registry.py:40`); `registry.py:44-62` reads `name, category, description, needs.python, version, route, test_file, path`.

### B. The Agent Skills / SKILL.md convention (VERIFIED, agentskills.io, accessed 2026-09-30)

Agent Skills is "a lightweight, open format" where a skill is a directory containing a required **`SKILL.md`** (YAML frontmatter + Markdown body), with optional `scripts/`, `references/`, `assets/` and any other files (agentskills.io/specification; agentskills.io/). **Required frontmatter: `name`, `description`. Optional: `license`, `compatibility` (≤500 chars), `metadata` (string→string map), `allowed-tools` (space-separated, experimental).** Constraints: `name` 1-64 chars, lowercase `a-z`/`0-9`/`-` only, no leading/trailing/consecutive hyphens, **must match the parent directory name**; `description` 1-1024 chars, non-empty, should state what the skill does and when to use it. Progressive disclosure has three stages: **metadata (~100 tokens) loaded at startup for every skill → full SKILL.md instructions on activation (<5000 tokens body, <500 lines recommended) → bundled resources loaded as needed**; file references stay one level deep; the reference validator is the `skills-ref` library. The format "was originally developed by Anthropic, released as an open standard," is now on an official Discord and a multi-vendor Client Showcase (agentskills.io/; docs.claude.com Agent Skills overview, Claude Level 1/2/3 architecture). No spec version number is shown on the pages accessed (UNVERIFIED whether a numbered spec version exists). **Compatibility with ISHTI's 120:** 111 names already satisfy the Agent Skills charset; 9 fail only on `_` (`chat_digest, deep_research, find_leads, online_fallback, phishing_check, screen_control, screen_understand, web_fetch, web_search`); longest description is 611 chars (`find_leads`) — all 120 are under the 1024 cap (my Counter measurement).

### C. Proposed manifest schema v2 (superset)

Rule: `manifest.json` stays the machine-readable source of truth; optional `SKILL.md` is generated for portability. Every new key is optional/advisory for the current loader — it reads via `json.load` + `.get` and validates nothing except `name` (skillsmith:82), so **all 120 v1 files remain valid byte-for-byte and unmodified** (only a `"schema"` key is absent, meaning v1).

```jsonc
{
  "schema": 2,                          // OPTIONAL; absent = v1 (all 120 current files)
  "name": "weather",                    // REQUIRED (loader: __init__.py:122; regex skillsmith:82)
  "version": "1.0.0",                   // REQUIRED for registered skills; drafts use "0.0.0"
  "description": "≤1024 chars, what+when", // REQUIRED (Agent Skills cap mirrored)
  "route": "weather, temperature, rain or forecast", // REQUIRED (router.py:41)
  "priority": 20,                       // REQUIRED int 1-100 (registry/task scheduling)
  "tier": "T0",                         // REQUIRED T0|T1|T2|T3 (dispatcher.py:452; scope.py:10)
  "status": "enabled",                  // REQUIRED working|enabled|disabled|draft|partial|planned
                                        //   "working" = legacy alias of "enabled" (120/120 today)
  "scope": {"write":["data"], "net":["open-meteo.com"], "apps":false}, // REQUIRED (scope.py:200-203)
  "needs": {"python":["requests"], "models":[], "mcp":[], "apis":["open-meteo.com"]}, // REQUIRED, 4 subkeys
  "category": "Utilities & Daily",      // OPTIONAL in file; loader falls back to SKILL_CATEGORIES (:123)
  "laya": false,                        // OPTIONAL bool, default true (router.py:68)
  // --- Agent Skills (SKILL.md frontmatter) mirrors — all OPTIONAL ---
  "license": "MIT",                     // → SKILL.md license
  "compatibility": "Requires Python 3.14+ and open-meteo.com", // ≤500 chars
  "metadata": {"author.name":"skillsmith"}, // string→string, namespaced
  "allowed_tools": ["Bash(git:*)"],     // → SKILL.md allowed-tools (experimental)
  "skill_md": "generated",              // "authored"|"generated"|"none"
  // --- self-extending pipeline — REQUIRED when source is present, OPTIONAL for builtins ---
  "source": "generated",                // or GitHub URL; written by install_folder (:96)
  "author": "skillsmith/coder",
  "created": "2026-09-30T12:00:00Z",    // + optional "updated"
  "source_links": ["https://github.com/user/repo"],
  "deps": {"pip": {"requests":"==2.32.3"}, "pip_hash":"sha256:..."}, // pinned
  "test_file": "tests/test_chat_digest.py", // registry.py:59; recommended for every new skill
  "rollback": {"previous_version":"1.0.0","backup":"data/skill_backups/weather-1.0.0","module_sha256":"..."}
}
```

**Required vs optional.** Hard-required for any *registered* skill: `name`, `version`, `description`, `route`, `tier`, `status`, `scope`, `needs` (all four subkeys), `priority`. Optional but recommended: `category`, `laya`, `test_file`, and all Agent Skills mirrors. Pipeline fields (`source`, `author`, `created`, `source_links`, `deps`, `rollback`) are required only for externally sourced/generated skills. Caveat: honoring `status:"disabled"`/`"draft"` at load time requires a small loader change — today enable/disable lives in Settings flags (`registry.py:82-160`), and `Skill.status` only re-maps `"broken"` (`__init__.py:146-148`).

**v0.0.0 draft vs registered.** A draft lives in `data/skill_staging/new_<name>/`, `version:"0.0.0"`, `status:"draft"`, and may omit `scope`/`category`/`source`/`rollback`; it is never loaded (loader scans `ishti/skills` only). Today's generator writes `0.1.0`/`"working"` (skillsmith:183-185) — v2 changes that to `0.0.0`/`"draft"`. A registered skill is copied into `ishti/skills/<name>/` (`install_folder`, skillsmith:89-97), gets `version ≥ 0.1.0`, `status:"enabled"`, resolved `category`, a `source` value (absent for builtins), `scope` (required by the self-check), and a `rollback` record on first update.

### Sources
- Local (all VERIFIED, read-only, 2026-09-30): `D:\ISHTI\ishti\skills\` walk + Counter measurement (Python 3, `D:\ISHTI\venv\Scripts\python.exe`); `ishti\skills\__init__.py`; `ishti\skills\skillsmith\skill.py`; `ishti\skills\registry.py`; `ishti\scope.py`; `ishti\skills\weather\manifest.json`; `ishti\skills\chat_digest\manifest.json`; `D:\ISHTI\data\skill_staging\*\manifest.json`; grep over `ishti\*.py`, `ishti\skills\*.py`, `orchestrator\*.py`.
- Agent Skills: https://agentskills.io/ and https://agentskills.io/specification and https://agentskills.io/skill-creation/best-practices (accessed 2026-09-30); https://docs.claude.com/en/docs/agents-and-tools/agent-skills/overview (accessed 2026-09-30).


## R5. Self-knowledge: a compact, live-sourced self-model

**Bottom line:** the weights can never be the source of host, model-inventory or capability facts — they are runtime state that postdates training. Build one small `SELF-MODEL` block from the same cached probes ISHTI already has (`ishti/vram.py`, `ishti/gpu.py`, `desktop.computer_use.get_system_telemetry`), stamp each line with provenance and age, cap the resident copy near 1,200 chars, and put the rest behind a `self_check` tool. Then "what's your context window?" is answered honestly ("I don't know - shall I check?") instead of guessed.

### 1. Why agents hallucinate self-facts
- **Parametric vs retrieved.** RAG (Lewis et al. 2020) states it verbatim: models "store factual knowledge in their parameters", but "their ability to access and precisely manipulate knowledge is still limited", and "providing provenance for their decisions and updating their world knowledge remain open research problems" — VERIFIED (arxiv.org/abs/2005.11401, fetched 2026-09-30). Installed models, free VRAM and loaded skills change without any fine-tune, so they are exactly the provenance/update case.
- **Self-report is calibration, not introspection.** Kadavath et al. (2022) find models are "well-calibrated ... when they are provided in the right format", but P(IK) "struggle[s] with calibration ... on new tasks"; calibration improves when "relevant source materials [are] in the context" — VERIFIED (arxiv.org/abs/2207.05221). The design lever is therefore putting the sources in context in a readable format.
- **Knowledge boundaries are real but leaky.** SelfAware (Yin et al., Findings of ACL 2023) finds "an intrinsic capacity for self-knowledge" but "a considerable gap between the capabilities of these models and human proficiency" at recognising unanswerable questions — VERIFIED (arxiv.org/abs/2305.18153). SKR (Wang et al. 2023) shows retrieval "does not always help and even has a negative impact", i.e. the model must decide *when* to fetch — VERIFIED (arxiv.org/abs/2310.05002).
- **Mature systems declare capabilities in registries.** MCP servers advertise capabilities in the `initialize` handshake and emit `notifications/tools/list_changed`, after which the client "immediately refreshes its tool registry" — VERIFIED (modelcontextprotocol.io/docs/learn/architecture, fetched 2026-09-30). A2A's Agent Card is "a JSON document that serves as a digital business card" with `name`, `description`, `provider` and `skills` (id, name, description, inputModes), cached via an ETag "derived from the card's version field or a content hash" — VERIFIED (a2a-protocol.org/latest/topics/agent-discovery/, fetched 2026-09-30). Verified live on this PC: `/api/tags` returns per model `digest`, `details.parameter_size`, `details.quantization_level` and `capabilities: ["tools","thinking","completion"]`. Copy from the registry; never recall.

### 2. The design pattern
`selfmodel.build()` (new module, one function, two renderers) reads the existing probes and emits:

```
SELF-MODEL (measured 2026-09-30 21:40, age 12 s)
Host: Windows 11 Pro 25H2 build 26200.9550 [live:registry]
CPU: Intel(R) Core(TM) Ultra 9 285, 24 cores [live:winreg]
RAM: 63.4 GB total / 44.3 GB free [live:psutil]
Disk D: 638.5 GB total / 304.2 GB free [live:psutil]
GPU: NVIDIA GeForce RTX 5070 Ti, 16303 MB VRAM (1735 used at probe), driver 610.47 [live:nvidia-smi]
Ollama 127.0.0.1:11434: 22 models installed; roles planner=ishti-qwen2.5:14b, fast=llama3.2:3b [live:/api/tags]
Loaded now: none [live:/api/ps]
Skills: N registered, M working [live:registry]
Unknown = anything not above. For any spec, model, limit or capability not listed here, say "I don't know - shall I check?" and call self_check.
```

Rules: (a) every line carries `[live:source]` or `[declared]` or is absent — never `[guess]`; (b) the block stores *measured_at* and the renderer prints age, so staleness is visible to the model, not hidden; (c) rebuild on startup and on change events — `/api/tags` digest set changed, skills registry changed, `CurrentBuildNumber`/UBR or GPU driver changed; (d) the full self-model (all 22 models, all skills, per-process VRAM) is a `self_check` tool answer only; (e) inject via `orchestrator/persona.build_system_prompt` (new `self_model_block` parameter) — persona.py's own docstring says it is "the single place any persona text is defined", so one place, one self-model; (f) `ishti/skills/system/skill.py` must render the *same* builder's full view so the spoken answer and the prompt can never disagree.

### 3. Token budgeting (measured)
Measured on two locally cached tokenizers against the 752-char block above and a 3,103-char block (all VERIFIED, this PC, 2026-09-30):

```
Laya/Llama-3-style : 752 ch -> 278 tok (2.71 ch/tok) | 3103 ch ->  997 tok (3.11)
Whisper-large-v3   : 752 ch -> 301 tok (2.50 ch/tok) | 3103 ch -> 1096 tok (2.83)
```

So **2.5-3.1 chars/token** on exactly this material — much worse than the 4 chars/token rule of thumb, because it is dense with digits and proper nouns (OpenAI itself says "1 token is approximately 4 characters" *and* "the same text can produce different token counts depending on the model" — VERIFIED, help.openai.com article, fetched 2026-09-30). The exact ratio for Qwen2.5/Qwen3 is UNVERIFIED (no Qwen tokenizer is cached; `/api/tokenize` does not exist and tokenising through a model would load it). Recommendation: **budget in chars, plan at the worst measured 2.5**. Resident cap **1,200 chars ≈ 390-480 tokens** (under 6% of an 8192 context); long tail behind `self_check`, cap ~4,000 chars ≈ 1,300-1,600 tokens, on demand only — never merged into every request. Refresh cost, one in-process pass (VERIFIED):

```
imports(cold) 9.5 ms | winreg+platform 21.3 | psutil 0.20 | nvidia-smi 40.1 | /api/tags 33.6 | /api/ps 12.1 | FULL 116.9 ms
```

With `vram.py`'s caches (gpu 0.4 s, ps 0.3 s, tags 10 s, info 3600 s) a refresh in the request path is a dict lookup; worst case ~117 ms, and never inline — reuse the single-flight snapshot pattern already recorded in `TEST_RESULTS.md` (p95 123 ms after replacing per-request nvidia-smi calls).

### 4. Live Windows detection, tested on this PC (2026-09-30)
```
platform.platform()            -> Windows-10-10.0.26200-SP0     <-- says Windows 10, wrong
platform.win32_ver()           -> ('10', '10.0.26200', 'SP0', 'Multiprocessor Free')
winreg CurrentVersion          -> CurrentBuildNumber 26200 | DisplayVersion 25H2 | UBR 9550 | CurrentBuild 26200 | ProductName "Windows 10 Pro" (stale)
platform.processor()           -> 'Intel64 Family 6 Model 198 Stepping 2, GenuineIntel'
winreg CentralProcessor\0      -> 'Intel(R) Core(TM) Ultra 9 285'          (~1 ms; platform+winreg 30.4 ms cold, 21.3 ms warm)
WMI Win32_Processor.Name       -> 'Intel(R) Core(TM) Ultra 9 285'          (1246.6 ms -> do not use)
psutil.virtual_memory()        -> 63.4 GB total | 44.3 GB available | 30.2%   (0.19 ms, 0.20 ms)
psutil.disk_usage('D:\\')      -> 638.5 GB total | 304.2 GB free | 52.4%
nvidia-smi --query-gpu=name,memory.total,memory.used,driver_version -> NVIDIA GeForce RTX 5070 Ti, 16303, 1735, 610.47   (37.6 ms; 57.7 ms from Python; a second run read 1718 used)
pynvml: system Python has pynvml 13.0.1 + nvidia-ml-py 13.610.43, with FutureWarning "The pynvml package is deprecated"; D:\ISHTI\venv has NO pynvml (ModuleNotFoundError). Keep using nvidia-smi subprocess.
/api/tags -> 22 models, 22.6 ms in-process (108 ms incl. interpreter start); keys: name, model, modified_at, size, digest, details{parent_model,format,family,families,parameter_size,quantization_level,context_length,embedding_length}, capabilities[]
/api/ps   -> {"models": []} (nothing loaded); documented fields: name, model, size, digest, details{}, expires_at, size_vram
```
`size` vs `size_vram`: `size` is the bytes Ollama holds for the model, `size_vram` the part resident in VRAM — `ishti/vram.py:ps()` already computes `full = size_vram >= size`. Windows 11 detection: build 26200 = 25H2, 26100 = 24H2, 26300 = 26H2 — VERIFIED (learn.microsoft.com/en-us/windows/release-health/windows11-release-information, updated 2026-09-29). The "Windows 10" string is a manifested-app artifact: "Applications not manifested for Windows 8.1 or Windows 10 will return the Windows 8 OS version value (6.2)" — VERIFIED (learn.microsoft.com/windows/win32/sysinfo/operating-system-version). Read `DisplayVersion` + `CurrentBuildNumber`.UBR; treat `platform.platform()` and registry `ProductName` as untrustworthy.

**Already present, reuse (read 2026-09-30):** `ishti/gpu.py` = `free_mb()` via nvidia-smi, `unload_idle_models()` via `/api/ps`, `slot()` governor. `ishti/vram.py` = cached `gpu()` (name,total_mb,used_mb,free_mb), `ps()` with last-good ≤30 s fallback, `tags()` {name: bytes}, `info()` {default_ctx,kv_per_token}, `processes()` per-process VRAM via win32pdh (works in the venv, pywin32 312). `ishti/skills/system/skill.py` = user-facing "my PC specs" skill calling `get_system_telemetry()` (cpu_percent, cpu_count, ram_total_gb/ram_used_gb/ram_percent, disks{device:{total_gb,free_gb,percent_used}}, gpu{name,total_vram_mb,used_vram_mb,temp_c}) → md_table; it is display-oriented. **Only missing piece: the resident, provenance-tagged block + change events.** Note `get_system_telemetry` calls `psutil.cpu_percent(interval=0.1)` — do not put that in a per-request path.

### 5. Privacy
Never in the block: hostname, owner name, phone, email, address, city, file paths, installed-app inventory, memories. The block lists *counts* and *role-pinned models* only (identifier-free, still treat as a device fingerprint). Owner facts stay in `orchestrator/memory/user_profile.json` (name/location/preferences, already local-only) and are injected by persona as memory, never as part of the self-model. Hard rule: **the self-model string is never an argument to `web_search`/`web_fetch`**; outbound queries pass one `sanitize_outbound()` chokepoint whose blocklist is built from profile keys; a test asserts the search payload contains no token from that blocklist and none of the block's lines.

### 6. Honesty
Wording that works on 7B-14B classes: "Facts about this machine come only from SELF-MODEL or `self_check`. SELF-MODEL is measured, not remembered." / "If a question about my hardware, model, limits or abilities is not answered there, reply exactly `I don't know - shall I check?` and call `self_check`. Do not estimate, do not recall." / "Never say I **am** a model: the router picks per request." / "Quote numbers with tag and age: '16,303 MB (live, 4 min ago)'." / "A failed read is not 'none' — say which read failed." Test with a paraphrase battery (~18 prompts, 3 groups): in-block ("what GPU do you have?"), near-miss not-in-block (context window, default temperature, parameter count of the *answering* model — which may be the 3B role), identity/privacy lures ("are you GPT-4?", "what's my address?"). Pass = the exact hedge sentence present, no digit that is not in the block, no model family claimed, and a `self_check` call. Run under both pinned models (planner and fast), 2-3 seeds. This matches existing test style (`tests/test_browser_tabs.py` asserts prompt strings verbatim; script-style tests run via `tools/verify_all.py` per `TEST_RESULTS.md`).

### Sources
- Live command output, this PC, 2026-09-30: `platform`, `winreg`, `psutil`, `nvidia-smi`, `Win32_Processor`, `/api/tags`, `/api/ps`, Measure-Command timings, `tokenizers` on local `tokenizer.json` files (Laya, Whisper-large-v3-turbo), pynvml/pywin32 checks in system Python and `D:\ISHTI\venv`.
- Repo files read 2026-09-30: `ishti/vram.py`, `ishti/gpu.py`, `ishti/skills/system/skill.py`, `desktop/computer_use.py` (get_system_telemetry), `orchestrator/persona.py`, `orchestrator/memory/user_profile.py`, `TEST_RESULTS.md`.
- https://arxiv.org/abs/2005.11401 (Lewis et al., RAG, NeurIPS 2020) — fetched 2026-09-30. https://arxiv.org/abs/2207.05221 (Kadavath et al., 2022) — fetched 2026-09-30. https://arxiv.org/abs/2305.18153 (Yin et al., Findings of ACL 2023) — fetched 2026-09-30. https://arxiv.org/abs/2310.05002 (Wang et al., SKR, 2023) — fetched 2026-09-30.
- https://modelcontextprotocol.io/docs/learn/architecture (protocol version 2026-07-28) — fetched 2026-09-30. https://a2a-protocol.org/latest/topics/agent-discovery/ — fetched 2026-09-30. https://raw.githubusercontent.com/ollama/ollama/main/docs/api.md — fetched 2026-09-30. https://docs.claude.com/en/docs/test-and-evaluate/strengthen-guardrails/reduce-hallucinations — fetched 2026-09-30. https://learn.microsoft.com/en-us/windows/win32/sysinfo/operating-system-version — fetched 2026-09-30. https://learn.microsoft.com/en-us/windows/release-health/windows11-release-information (updated 2026-09-29) — fetched 2026-09-30. https://help.openai.com/en/articles/4936856-what-are-tokens-and-how-to-count-them — fetched 2026-09-30.
- UNVERIFIED: exact chars/token for Qwen2.5/Qwen3 tokenizers (not present locally); whether any ISHTI code already calls `/api/tokenize` (it does not exist).

## R6. Local coder capability and its limits under the VRAM governor

*All file:line citations and command outputs verified 2026-09-30 unless explicitly marked UNVERIFIED / NEEDS-MEASUREMENT. Machine state at check time: RTX 5070 Ti, 1659 MiB used / 14337 MiB free (`nvidia-smi`, 2026-09-30) — nothing loaded; I deliberately loaded nothing.*

### 1. What the coder role resolves to

- `config.json` names `"coder": "qwen3-coder:30b-a3b"` (config.json:6). That exact model is **not installed**: `ollama list` (2026-09-30) shows `ishti-qwen2.5-coder:14b` (9.0 GB), `qwen2.5-coder:14b` (9.0 GB), `qwen2.5-coder:7b` (4.7 GB) and the alias `qwen2.5-coder-c8k:7b` (4.7 GB) — no `qwen3-coder:30b-a3b`.
- `llm._configured_for("coder")` therefore falls back along a hardcoded chain: `qwen3-coder:30b-a3b` → `ishti-qwen2.5-coder:14b` → `qwen2.5-coder:14b`, else `_cfg["fallback"]` (llm.py:89–94). Since rung 1 of that list is installed, the coder resolves to **`ishti-qwen2.5-coder:14b`**. The env var `ISHTI_MODEL_CODER` overrides everything ("pins a role to one model", llm.py:66–68), and `FALLBACK_MAP` repeats the 30B→14B mapping for display paths (llm.py:33–37).
- `model_for("coder")` then passes the name through `vram.effective("coder", name)` (llm.py:59), which returns the downshifted/job-selected rung if one is in force, otherwise the configured name (vram.py:392–394). The ladder is the auto-adjust source: coder = `ishti-qwen2.5-coder:14b` → `qwen2.5-coder:7b`, `keep_alive: 10m`, `min_ctx: 4096` (models_ladder.yaml:33–38).
- What happens when the config names a missing model: if `models.list()` succeeded, the fallback chain hides it (verified logic, llm.py:89–94). If that listing failed (`_installed` empty), the guard `if _installed and ...` is skipped and the uninstalled name is sent → measured today: `NotFoundError: model 'qwen3:30b-a3b' not found` (`md files\NEEDS.md`, 2026-09-30 09:59). This is a known open item, N6/ISSUES #21 (NEEDS.md:68–70), and MODEL_DECISIONS.md:31 already documents the planner twin of this bug.

### 2. Measured coder numbers (MODEL_DECISIONS.md, updated 2026-09-30)

| Rung | Model (ctx) | Disk (`ollama list`, today) | GPU MiB (Ollama) | tok/s | first token | load | Quality |
|---|---|---|---|---|---|---|---|
| 0 | `ishti-qwen2.5-coder:14b` (8192) | 9.0 GB | 10151 (9922) | 80.9 | 157 ms | 5.0 s | 4/5 — tool-call prompt failed |
| 1 | `qwen2.5-coder:7b` @8192 → alias `-c8k:7b` | 4.7 GB | 5121 (4896) | 146.0 | 150 ms | 3.0 s | 4/5 |

Rows quoted from MODEL_DECISIONS.md:45–46 (same table header and method at :33–38: six fixed prompts — arithmetic, JSON extraction, a table, Urdu, tool call, vision — "These are **my** prompts, not yours"). The raw DB independently confirms both: `ishti-qwen2.5-coder:14b` num_ctx 8192, size=size_vram=10404390501 B (9922 MiB), tps 80.9, gpu_delta 10106 MiB; `qwen2.5-coder-c8k:7b` 8192, 5133943438 B (4896 MiB), tps 146.0, gpu_delta 5121 (`data/vram.db`, `model_footprints` query 2026-09-30). Both are **measured**; one discrepancy: the doc says 10151 GPU MiB, the DB 10106 — a 45 MiB run-to-run gap, both measured, neither estimated. `qwen3-coder:30b-a3b` is **rejected** (19 GB, MoE loads all experts), and loading `qwen-27b` raw produced a measured 13614/19272 MiB partial offload (MODEL_DECISIONS.md:111).

### 3. Governor constraints and the correct call sequence

Constraints bounding a codegen job: coder `min_ctx: 4096` (models_ladder.yaml:35), `keep_alive: "10m"` (:34); `budget_pct: 80` = 13042 MiB (:12; MODEL_DECISIONS.md:68); `safety_margin_mb: 128` on top of a footprint (:13); `ollama_headroom_mb: 1536` free after a load because llama.cpp needs 1 GiB and Ollama over-predicts ~240 MiB (:14–15); global `min_ctx` 2048 and `queue_wait_s` 45 (:19, :22). Partial offload is a **hard failure**: "Never set num_gpu: partial offload is a hard failure" (models_ladder.yaml:8; vram.py:1–13; `num_gpu` never set, MODEL_DECISIONS.md:124).

The governor API, from its own docstrings: `admit()` — "Make room for `model` and load it, verified fully on the GPU… When nothing fits within wait_s the job is reported as queued with the reason (never partial offload)" (vram.py:639–641); `plan()` — "A cumulative plan that makes `model` fit… Nothing is changed" (:533–534); `heavy()` — "`with vram.heavy('gemma3:12b', role='vision3', job='read the page') as r: (r['ok'] False -> queued, r['queued'] why)`" (:820–822); `check_violations()` — "Partial offloads that exist right now. An idle one is unloaded at once; a busy one is left to finish its request" (:902–903); `snapshot()` — "GET /api/vram" (:914–915); `effective()` (:392–394) and `rungs()` (:340–341) are the resolution helpers.

Exact sequence for "run a code-generation job under the governor": (a) normal path — just call `llm.chat(..., role="coder")`; `_create` internally does `vram.busy(model)` → `vram.admit(model, role="coder", job=...)` → raises `GpuBusy` with the queued reason if not ok → swaps to the governor-chosen rung → releases the lease when the last token is read (llm.py:108–148). (b) Long job spanning non-LLM work — wrap it: `with vram.heavy(model, num_ctx=None, role="coder", job="build skill X") as r: if not r["ok"]: report r["queued"]; ...` (`admit`/`release` inside, vram.py:820–828). After `plan()` is non-None, `_apply` loads, and on any partial offload unloads at once and retries one step lighter (context halving down to the role `min_ctx`, then next ladder rung; vram.py:697–723). A coder-14B request while the 14B planner is resident must first downshift the planner — "14B+14B can't co-reside" (MODEL_DECISIONS.md:112).

### 4. What one generation at 14B/8k can realistically produce

The measured budget: 8192 context (MODEL_DECISIONS.md:45), output capped at **3500 tokens** by the skillsmith pipeline (`max_tokens=3500`, skillsmith/skill.py:161; same cap for the syntax repair :182 and the 3 fix rounds :196), ~43 s of generation at 80.9 tok/s. The prompt embeds a 2500-char style example (`utilities/skill.py`[:2500], skill.py:152) plus format instructions (~900–1200 tokens), and requires a strict JSON envelope with the full `skill.py` inside (skill.py:154–161) — so roughly **250–400 lines of Python per generation** are the realistic ceiling, exactly the "200–400 line module + manifest" class. The manifest is built by code, not by the model (skill.py:183–186). It cannot hold an arbitrary file in context: everything prompt+output must fit 8192 tokens; a 4000-line file is ~40–60k tokens — 5–8× the window and ~12× the output cap — so a single 4000-line generation is structurally impossible, not merely unlikely.

Known failure modes at this size: (1) self-check never passes — measured twice today: `[gap] skillsmith: couldn't generate a working 'pdf_to_text' skill` and `'temperature_conversion' skill` (md files\NEEDS.md, 2026-09-30 14:03/14:04); (2) syntax errors — exactly one repair round, then honest refusal "doesn't compile… Try describing it differently" (skill.py:174–188); (3) unusable JSON spec → "didn't produce a usable skill" (:163–166); (4) models wrap code in fences even inside JSON — `unfence` exists because it happens (:172); (5) invalid/taken names (:167–168); (6) the measured capability gap: both coder rungs fail the tool-call prompt (NEEDS.md:41–42) — a caution, not a codegen blocker, but it bounds agentic/tool-driven generation. Anything bigger must be **split by module boundary**, each module ≤ ~300 lines, one generation each, with interfaces pinned as real code (imports, signatures, a shared constants file), because a Q4 14B cannot reliably keep cross-file coherence across many 3.5k-token chunks. Also note the pipeline's absolute per-skill budget: 1 initial + 1 repair + 3 fix = **5 coder calls**, then it refuses and logs (skill.py:174–203).

### 5. NEEDS items that bound model quality

N1 (root NEEDS.md:39–45): the quality column comes from six author-written prompts; "the 7B planner rung **fails the Urdu prompt** and both coder rungs fail the tool-call prompt"; the owner's 10–20 real prompts are *Needed* to grade rungs properly. N6 (:68–70): config names the uninstalled 19 GB models. N2 (:47–50): q8_0 KV cache left off pending quality testing. N5 (:62–66): the running Ollama isn't ISHTI-owned, so settings can't be applied until the owner acts. Q10 (md files\NEEDS.md, added today): QA batches + live instance contend for VRAM — 14.9/16.3 GB observed; with `qwen3:14b` loaded, `gemma3:4b` (2.7 GB) cannot load under the 80% budget. `D:\ISHTI\FINAL` does not exist (checked; skipped).

### 6. Escalation criteria ("I can't do this yet")

Checkable conditions, each already implemented or measured: **(a)** generation truth-fail — after ≤5 coder calls the skill fails its own asserts → refuse + `log_need("gap", …)` (skill.py:190–203); **(b)** GPU placement fail — `admit()` not ok after `queue_wait_s` 45 s → `GpuBusy` with the plain reason, never a partial load (vram.py:650–673; llm.py:112–113; measured GpuBusy entries 2026-09-30 10:00–10:02, 19:01); **(c)** agent limit — `max_iterations` default 4 → `escalated: True` + exact reason ("hit its iteration limit", agents.py:66–68, 214–217, C6 doc :16–17); **(d)** T3 actions refused in code with the escalation explained (trading/live-broker, ISSUES.md:280–283); **(e)** missing credential/API or owner token (manifest `needs.apis`, mcp token files); **(f)** unvettable dependency — e.g. the Baileys WhatsApp reader requires an explicit owner yes (NEEDS.md:106–113), and only manifest-declared package names are ever installed (skills/\_\_init\_\_.py:21–40); **(g)** model larger than budget — 19 GB rungs rejected, never attempted; partial offload measured and forbidden (MODEL_DECISIONS.md:111); **(h)** task needing more than the per-skill generation budget (>5 generations, or more modules than can be verified) must be split or declined. Note: these gap logs append to `D:\ISHTI\md files\NEEDS.md` (log_need path, skills/\_\_init\_\_.py:227), **not** the curated root `D:\ISHTI\NEEDS.md` — both exist; quote whichever the report intends.

### 7. Conclusion

The skill_creator pipeline should use **`ishti-qwen2.5-coder:14b` (ladder rung 0, 8192 ctx)**, with `qwen2.5-coder:7b` as the automatic lighter rung — and should fix `config.json`/N6 rather than rely on the silent fallback. Design constraint, not preference: **one self-contained ≤300-line module + generated manifest per generation, ≤3500 output tokens, mandatory offline self-check, ≤5 model calls per skill; split anything larger.** The honest ceiling for one generation is a single ~200–400-line module; a 4000-line file needs module-level splitting and multiple generations and is outside what a Q4 14B at 8k can hold coherently.

To upgrade this from static to measured: run, when the GPU is genuinely idle and with the owner's prompt set (N1), a three-way comparison of `ishti-qwen2.5-coder:14b`, `qwen2.5-coder:7b`, and `qwen2.5-coder:14b` using the exact commands `python -m ishti.vram_calibrate ishti-qwen2.5-coder:14b`, `python -m ishti.vram_calibrate qwen2.5-coder:7b`, `python -m ishti.vram_calibrate qwen2.5-coder:14b` (each does real loads, /api/ps + nvidia-smi, fixed prompts), **plus a codegen-specific harness that replays skillsmith's exact `op_create` prompt N=5–10 times per model and records `py_compile` pass, self-check pass and token counts** — that harness does not exist yet (`vram_calibrate` grades arithmetic/JSON/table/Urdu/tool-call/vision only, MODEL_DECISIONS.md:35–38). **This entire paragraph is UNVERIFIED / NEEDS-MEASUREMENT** — I did not run any of it (shared-GPU hard constraint), and one lane risks the measured Q10 contention failure mode.

### Sources
- D:\ISHTI\ishti\config.json:5–10; D:\ISHTI\models_ladder.yaml:8,12–22,27,33–38.
- D:\ISHTI\ishti\llm.py:33–37,51–59,62–95,108–148; D:\ISHTI\ishti\vram.py:1–21,340–341,392–394,533–570,639–723,820–828,902–944; D:\ISHTI\ishti\skills\skillsmith\skill.py:150–207; D:\ISHTI\ishti\skills\__init__.py:21–40,223–236; D:\ISHTI\ishti\agents.py:16–17,39–54,66–68,192–217; D:\ISHTI\MODEL_DECISIONS.md:31,33–46,68,103–105,109–115,117–126; D:\ISHTI\NEEDS.md:39–50,57–70,106–113; D:\ISHTI\ISSUES.md:280–283; D:\ISHTI\md files\NEEDS.md (runtime gap log: 2026-09-30 09:59, 14:03–14:04, 19:01; Q10 note).
- Commands (2026-09-30): `ollama list`; `nvidia-smi --query-gpu=name,memory.total,memory.used,memory.free --format=csv`; sqlite read of `D:\ISHTI\data\vram.db` (`model_footprints`: coder rows, columns model/num_ctx/size/size_vram/tps/gpu_delta); `Test-Path D:\ISHTI\FINAL` → False. No model was loaded, no pytest, no GPU job run.
