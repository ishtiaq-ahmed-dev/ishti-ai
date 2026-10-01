# IMPLEMENTATION_PLAN.md — skill_creator + self-knowledge + issue hunt

*(Archived 2026-10-01 when the remote-control mega-request took over the canonical
`IMPLEMENTATION_PLAN.md` name. This is the in-flight skill_creator session's plan, preserved so its
uncommitted work keeps its plan; that work is checkpointed in the WP0 commit and tag
`ishti-pre-remote-20261001`.)*

Written **before any feature code**, per the master prompt. Phase 1 evidence: `RESEARCH.md`
(6 parallel agents, `research/R1..R6.md`). Session 2026-09-30/10-01.

## 1. Goals

| # | Goal | Success looks like |
|---|---|---|
| G1 | ISHTI never refuses or bluffs when no skill fits. It researches, designs, does the task, and saves a proper new skill. | `skill_creator` pipeline runs end-to-end on 3 real tasks; each leaves `research.md` + `SKILL_DESIGN.md` + vetted pins + scan + sandbox run + tests green 3x in `ishti/skills/_drafts/<name>/` |
| G2 | Nothing is enabled without the owner's explicit yes, and "disabled" actually means disabled. | New skill lands `status:"disabled"` and is provably **not** routed to; after a scripted owner "yes" it is enabled and reused from chat without re-researching |
| G3 | ISHTI knows itself from live data, not from its weights. | Self-model answers match live measurements exactly; unknown facts produce "I don't know" |
| G4 | Unsafe/impossible requests are refused honestly. | Exact missing thing is named; no fake success; `NEEDS.md` gets the entry |
| G5 | Generated code cannot widen its own permissions or escape its scope. | Sandbox + scope + static scan block the six escape tests; learning can never lower a tier |
| G6 | The project's own Critical/High issues are fixed or documented. | `ISSUES.md` refreshed with evidence; each fix has a regression test that was red before and green after |

## 2. Non-negotiable constraints inherited from the repo (measured, not assumed)

| Constraint | Evidence | Consequence for the build |
|---|---|---|
| Skills are `ishti/skills/<name>/{manifest.json,skill.py}`; the loader reads only those two names at the folder root | `ishti/skills/__init__.py:168-187` | new skill follows the same shape |
| The loader **skips** any top-level dir starting with `.` or `_` | `ishti/skills/__init__.py:180` | `ishti/skills/_drafts/` is invisible to the registry - safe place for unapproved drafts |
| `scope.py` treats `ROOT/ishti` as PROTECTED; only `skillsmith` has an exception for `ishti/skills` | `ishti/scope.py:31-34, 92-93` | `skill_creator` gets its **own, narrower** exception, and only for `ishti/skills/_drafts` |
| Routing filters **only** `status == "broken"` | `ishti/router.py:53` | **must be fixed first** or `disabled` is cosmetic (see WP-C1) |
| `skills/registry.py:retrieve()` - which *does* honour the Settings `skills.disabled` flag - is never called in the runtime; only `interface/web_ui/server.py:1261-1278` writes the flag | grep across `ishti/` (only match is its own `__main__`) | the dashboard's Disable is currently a no-op for chat: fix in WP-C1, document in `ISSUES.md` |
| The T2 gate is the single `ask_first` yes/no; test mode blocks the side effect at the last step | `ishti/skills/__init__.py:216-220`, `ishti/test_mode.py:53-57` | all new T2 actions route through `ask_first` + `test_mode.guarded()` |
| 120 manifests, **0 non-conformant**; `test_file` present in only 1 | `RESEARCH.md` R4 A | manifest schema is a *superset*; new skills always declare `test_file` |
| Python 3.11.9, psutil 7.2.2, pywin32 312; `bandit`/`pip-audit`/`semgrep` absent | `pip list` 2026-09-30 | vetting must be zero-install (PyPI JSON + OSV + `difflib` + `ast`) |
| Coder resolves to `ishti-qwen2.5-coder:14b` (config names an uninstalled model; `llm.py:89-94` falls back) | `RESEARCH.md` R6 S1 | one <=300-line module per generation, <=3500 tokens, <=5 model calls |

## 3. Architecture per work package

### WP-A - `ishti/skills/skill_creator/` (self-extending ISHTI)

**Decision D1: orchestrate, do not duplicate.** `skillsmith` already installs and validates skills;
`skill_lifecycle` already discovers gaps, keeps per-skill memory, report cards and retire/restore.
`skill_creator` is the pipeline around them. Reused verbatim: `skillsmith.validate`, `skillsmith.scan`,
`skillsmith.install_folder`, `skill_lifecycle.discover_gaps`, `ishti.llm.ask(role="coder")`,
`ishti.vram.admit/heavy`, `ishti.audit.record`, `ishti.test_mode.guarded`, `ishti.scope`.

```
owner request
  |
  +- 1 gap.py       no skill matched (route confidence < floor) or a skill failed after retries?
  |                 -> at most ONE clarifying question if ambiguous, else proceed
  +- 2 research.py  T0 read-only web via existing web_search/web_fetch; writes research.md
  |                 (sources + dates; recursion capped; sources deduped)
  +- 3 design.py    feasibility + risk: T2 side effects? credentials? money? VRAM/latency?
  |                 -> cannot be done locally/safely/within limits = SAY SO + log_need(); write SKILL_DESIGN.md
  +- 4 vetting.py   exact-name + typosquat (difflib vs curated popular list) -> PyPI JSON
  |                 (licence, release age, sha256, requires_python) -> OSV.dev (CVE + MAL-*)
  |                 -> ast scan (R3 node table) -> pins (name==version, hashes)
  +- 5 implement    one <=300-line module + manifest inside the draft folder (coder model,
  |                 under vram.admit; <=5 calls; <=3500 out-tokens per call)
  +- 6 sandbox.py   Job-Object run: 512 MB proc / 1 GB job / <=8 procs / 90 s CPU / 120 s wall
  |                 + cleaned env + pinned cwd + CREATE_NO_WINDOW; no orphan on crash
  +- 7 test loop    fix -> retest until 3 consecutive green; after 8 failed attempts on ONE issue:
  |                 stop, write diagnosis + exactly what is missing to NEEDS.md
  +- 8 do the task  dry-run first; any T2 side effect needs ask_first yes with an exact recap
  +- 9 package      manifest v0.1.0, status "disabled", author "ISHTI skill_creator",
  |                 created date, source_links, deps pinned, rollback record, README+CHANGELOG
  +- 10 register    copy into ishti/skills/ via skillsmith.install_folder -> DISABLED
                    enabling = owner's explicit yes (T2) -> status "working" + registry.set_enabled(name,True)
                    monitor failures -> auto-disable after 3 consecutive failures; 1-command rollback
```

Hard limits enforced in code, not prose: one draft in flight per task; <=12 files and <=256 KB per draft;
generated skills may not write to `ishti/` (except their own draft folder), may not touch core files, the
tier table, the kill switch or the audit log; a skill can never lower its own tier or widen its scope.

**Sandbox placement deviation (documented):** R2 suggested `ishti/sandbox.py`; it is built as
`ishti/skills/skill_creator/sandbox.py` instead so the capability lives with its owner and no new core
module is created. Content follows R2's tested recommendation exactly (same Job-Object limit flags).

### WP-B - self-model (`ishti/selfmodel.py`, `owner_profile.yaml`)

`build(force=False)` reads live probes only, caches with a TTL, and renders a <=1,200-char block with a
provenance tag per line (`[live:psutil]`, `[live:nvidia-smi]`, `[declared]`). Sources are the ones ISHTI
already owns: `ishti/gpu.py:free_mb`, `ishti/vram.py:snapshot/ps/tags`, `psutil`, `winreg`, `platform`,
`nvidia-smi`, `GET /api/tags`, `GET /api/ps`, and `ishti.skills.load_skills()` for the capability list.
`refresh()` is exposed as "refresh your self-knowledge"; change detection compares a cheap fingerprint
(GPU name, model-set digest, skill-name set, profile mtime) so hardware/model/skill changes re-render it.
The resident block is inserted **once** in `ishti.llm.style()`; the long tail sits behind the existing
`meta` skill (`self_check`). Privacy: the block never becomes an argument to `web_search`/`web_fetch`;
a test asserts that. Honesty: absent facts are answered with the literal hedge, and a failed probe
reports "that read failed", never "none".

### WP-C - issue hunt (evidence-first)

Scan `ISSUES.md`, `TEST_STATE.json`, `FEATURE_CATALOG.md`, skills, configs, logs and the learning logs.
Findings written only with `ID | Area | Severity | Evidence | Repro | Root cause`. Fix order:
crashes/data-loss/security -> VRAM/lifecycle -> routing/STT -> browser/WhatsApp -> UI -> polish.
Every fix ships a regression test that was **red before** and green after; anything not fixable safely
goes to `NEEDS.md` with exactly what is missing.

## 4. Ordered work packages (owner, files, dependencies, proof, risk, rollback)

**File-ownership rule: no two agents edit the same file.** Any fixer that needs a file must claim it in the
shared task list first; the integrator rejects overlapping claims.

| WP | Deliverable | Owner-agent | Files it may touch (exclusive) | Depends on | Proof that it works | Risk | Rollback |
|---|---|---|---|---|---|---|---|
| **WP0** | Rollback point | Integrator | git only | - | `git rev-parse` of the tag + `git status` snapshot | none | the tag itself |
| **WP-C1** | Routing honours `disabled`/`draft`/`retired` + the Settings `skills.disabled` flag | Fixer-1 | `ishti/router.py`, `tests/test_router_lifecycle_flags.py` | WP0 | new test: **red before** (status disabled still routed), green after; existing `tests/test_dispatcher_*` still green | **Medium** - routing is the hot path for all 120 skills. Mitigation: additive filter only, lazy + exception-safe flag read, existing suite re-run | `git checkout -- ishti/router.py` |
| **WP-A1** | `skill_creator` core: manifest, routing, gap detection, sandbox, draft lifecycle | Builder-A | `ishti/skills/skill_creator/{manifest.json,skill.py,gap.py,sandbox.py,drafts.py}` | WP0 | per-module `__main__` self-checks (repo convention) + sandbox kills a memory hog | Low - all new files | delete the folder |
| **WP-A2** | Research, risk/feasibility, vetting, design doc, test loop | Builder-A | `ishti/skills/skill_creator/{research.py,design.py,vetting.py}` | WP-A1 | self-checks + typosquat rejection + "impossible request" path | Medium - network calls in vetting; mitigation: T0 scope declared, offline fails closed | delete the files |
| **WP-A3** | The 3 real draft skills + README/CHANGELOG | Builder-A | `ishti/skills/_drafts/{images_to_pdf,pdf_tables_csv,price_watch}/`, `ishti/skills/skill_creator/{README.md,CHANGELOG.md}` | WP-A2, WP-C1 | acceptance tests 1a/1b/1c | **High** - real coder model output may be unusable. Mitigation: 8-attempt loop then honest NEEDS.md entry, never a fake pass | delete the drafts |
| **WP-B1** | `ishti/selfmodel.py` + `owner_profile.yaml` | Builder-B | `ishti/selfmodel.py`, `owner_profile.yaml` | WP0 | `tests/test_selfmodel.py` + measured char size | Low | delete both |
| **WP-B2** | Wire the self-model into the system prompt + "refresh your self-knowledge" | Builder-B | `ishti/llm.py`, `ishti/skills/meta/skill.py` | WP-B1 | paraphrase battery + privacy assertion | **Medium** - `llm.style()` is on every request. Mitigation: additive, wrapped so a probe failure can never break a reply | `git checkout -- ishti/llm.py ishti/skills/meta/skill.py` |
| **WP-C2** | `ISSUES.md` refreshed from a fresh scan | Issue-Hunter | `ISSUES.md` | WP0 | every row has Evidence + Repro + Root cause | Low | `git checkout -- ISSUES.md` |
| **WP-C3** | Top Critical/High issues fixed, each with a regression test | Fixer-2 | claimed files + `tests/test_fix_*.py` | WP-C2 | each fix: test red before, green after | Medium | per-fix `git checkout` |
| **WP-D1** | `tests/test_skill_creator.py` (acceptance 1-9) | Tester | `tests/test_skill_creator.py` | WP-A3 | 3 consecutive green runs | Medium | delete the file |
| **WP-D2** | `tests/test_selfmodel.py` (acceptance 10-12) | Tester | `tests/test_selfmodel.py` | WP-B2 | green + measured numbers | Low | delete the file |
| **WP-D3** | Full offline suite + acceptance run, real numbers | Tester | `TEST_RESULTS.md` | all | raw command output in the report | Low | - |
| **WP-E** | `FINAL_REPORT.md`, `NEEDS.md`, `PROGRESS.md` | Integrator | those three files | all | verdict table with evidence | Low | - |

## 5. Schedule - what is parallel and what is strictly sequential

```
SEQUENTIAL (must be first, everything branches from it)
  WP0 rollback point
  WP-C1 router fix .................... unblocks any "disabled means disabled" test
      |
      +-- PARALLEL BAND 1 (no shared files, no GPU) --------------------------------
      |     Builder-A : WP-A1 -> WP-A2
      |     Builder-B : WP-B1 -> WP-B2
      |     Issue-Hunter : WP-C2
      |         |
      |         +-- PARALLEL BAND 2 (no shared files) -----------------------------
      |               Fixer-2 : WP-C3   (after WP-C2 produced the ranked list)
      |               Tester  : WP-D2   (after WP-B2)
      |               Tester  : WP-D1 offline cases (after WP-A2)
      |
      +-- SERIALISED GPU WINDOW (only one model load at a time - measured rule)
      |     WP-A3 code generation for the 3 acceptance tasks, VRAM measured at each step
      |
      +-- SEQUENTIAL MERGE (integrator, dependency order, full suite after each merge)
            merge WP-C1 -> WP-D1 -> WP-A3 -> WP-B2 -> WP-C3
            anything that breaks a previously green test is reverted
      |
      +-- WP-D3 full suite + acceptance run -> WP-E report
```

GPU serialisation is a measured requirement, not caution: `md files/NEEDS.md` records two real `GpuBusy`
failures on 2026-09-30 caused by a second model load during a run, and `RESEARCH.md` R6 S3 quotes the
80% budget (13042 MiB) against a 9922 MiB coder footprint. No two agents load a model at once.

## 6. Acceptance criteria (the gate)

READY requires **all** of these, each with log output in `TEST_RESULTS.md`:

1. acceptance tests 1-13 green (three real gap tasks; impossible request; malicious page; typosquat;
   scope/network escape; tier/scope self-modification; runaway limits + zero orphans; rollback +
   auto-disable + self-model sees the new skill; VRAM with zero partial offload);
2. zero observations of `/api/ps` `size_vram != size` during the run;
3. zero ungated T2 actions in the audit log for the whole session;
4. sandbox escape tests blocked, with the blocking recorded;
5. no secret or personal data in any web query or exported report (`owner_profile.yaml` stays local);
6. every Critical/High issue fixed, or in `NEEDS.md` with exactly what is missing;
7. the full offline suite green after the final merge.

## 7. Owner approval needed (asked in `NEEDS.md`, never assumed)

- Confirming the exact owner name / preferred address / tone in the new `owner_profile.yaml`
  (seeded as `confirmed: false`, so the default is honest rather than guessed).
- `config.json` still names `qwen3-coder:30b-a3b`, which is **not installed** (R6 S1). Fixing it is a
  config change to a system file - owner decision, listed not applied.
- Anything requiring a purchase, data deletion, or a T2/T3 rule change: none in this plan.

## 8. Rollback points

| Point | What it restores | How |
|---|---|---|
| R0 | the whole tree as it was before this session | `git checkout <tag-from-WP0>` |
| R1 | routing behaviour | `git checkout -- ishti/router.py` |
| R2 | the self-model (inert) | delete `ishti/selfmodel.py`, `owner_profile.yaml`; `git checkout -- ishti/llm.py ishti/skills/meta/skill.py` |
| R3 | a single generated skill | `skill_creator` rollback command -> restore `data/skill_backups/<name>-<version>/` |
| R4 | any generated skill entirely | `skillsmith` "remove the <name> skill" |
