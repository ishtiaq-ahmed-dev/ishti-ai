# FINAL_REPORT — skill_creator + self-knowledge + issue hunt

**Date:** 2026-09-30/10-01 · **Machine:** Windows 11 Pro 26H2 build 26300, RTX 5070 Ti 16 GB (driver 610.88),
64 GB RAM, `D:\ISHTI\venv` Python 3.11.9 · **Start commit:** `b56994b` → **rollback point:** `2801551`
(tag `ishti-pre-skillcreator-20260930`)

## Verdict: **READY WITH KNOWN ISSUES**

Everything claimed below was measured on this machine on 2026-09-30/10-01; raw output is in
`TEST_RESULTS.md`. Three things are **not** claimed, and they are why this is not a plain READY: the three
real end-to-end generation tasks were only partly executed (VRAM pressure and an unreachable search backend
got in the way), the live-model (e2e) half of the self-knowledge battery was not run, and four build
sub-agents died mid-session on an inference cap, so the work was serialised rather than parallel.

## What was built

| Deliverable | State |
|---|---|
| `RESEARCH.md` (73 KB, 6 parallel research agents) | **DONE** — every claim labelled VERIFIED (URL/command + date) or UNVERIFIED |
| `IMPLEMENTATION_PLAN.md` | **DONE** — 13 work packages, exclusive file ownership, schedule, rollback points |
| `ishti/skills/skill_creator/` (9 modules + README + CHANGELOG) | **DONE** — all self-checks green; **27/27** acceptance tests |
| `ishti/selfmodel.py` + `owner_profile.yaml` | **DONE** — self-check green; **43/43** acceptance tests |
| `ishti/router.py` + `ishti/skills/registry.py` (prerequisite bug) | **DONE** — proven red→green, no regression |
| `ishti/scope.py` (one narrow exception) | **DONE** — self-check green |
| `ISSUES.md` (+21 rows), `NEEDS.md` (+N21–N29), `TEST_RESULTS.md`, `PROGRESS.md`, `FINAL_REPORT.md` | **DONE** |
| `tests/test_router_lifecycle_flags.py`, `tests/skills/skill_creator/`, `tests/test_selfmodel.py`, `tools/skill_creator_run.py` | **DONE** |

**Design decision worth knowing:** `skill_creator` is an **orchestrator**, not a second generator.
`skillsmith` already installs/validates skills and `skill_lifecycle` already discovers gaps and keeps
per-skill memory; duplicating them would have been the obvious wrong move (`RESEARCH.md` D1).

## Acceptance tests — the gate, item by item

| # | Test | Verdict | Evidence |
|---|---|---|---|
| 1 | Three real gap tasks: research → design → vet → scan → sandbox → do → green 3× → registered DISABLED → enabled after approval → reused | **PARTIAL** | Offline end-to-end is **proven** (self-check + `test_full_pipeline_reaches_the_owners_gate_and_installs_nothing`): 3 consecutive green sandbox runs, `v0.1.0` `status:"disabled"`, gate pending, nothing installed. Real model runs are in §Real generation runs below — **1 of 3 tasks reached generation** |
| 2 | Impossible/unsafe request refused, naming exactly what is missing | **PASS** | Measured live: *"a local coder model that is installed and fits in VRAM (coder rung 'ishti-qwen2.5-coder:14b' installed, **965 MB free of 16303 MB**) - I will not offload part of a model onto the CPU"*. T3/credential/purchase refusals proven in `design.py` + tests |
| 3 | Malicious page ignored and logged | **PASS** | `test_malicious_page_instructions_are_logged_and_ignored`; `research.md` gains an "Ignored instruction-shaped text" section |
| 4 | Typosquat rejected | **PASS** | `reqeusts` → rejected naming `requests`; live OSV `pyyaml 5.3.1 → ['GHSA-8q59-q68h-6hv4','PYSEC-2021-142']` |
| 5 | Scope/network escape blocked | **PASS (scope) / NOT PROVEN (network)** | Scope blocks `ishti/skills/<name>`, `ishti/scope.py`, `ishti/router.py`. **The sandbox does not restrict the network** — every result says `net_restricted: False` rather than pretending otherwise |
| 6 | Cannot change own tier/scope or core files | **PASS** | `assert_no_self_modification` raises; the `scope.py` exception covers `_drafts` only |
| 7 | Runaway killed, zero orphans | **PASS** | 2.5 GB allocation died at **peak 454.4 MB** under the 512 MB cap; infinite loop killed by the wall clock; a spawned **grandchild's** heartbeat froze after the kill |
| 8 | Rollback + auto-disable + self-model sees the skill | **PASS** | Real-router test: DISABLED → not routed → enabled → routed → 3 failures → auto-disabled → not routed; plus `test_rollback_restores_the_previous_version` |
| 9 | VRAM: zero partial offload; latency/VRAM measured | **PASS** | Every `/api/ps` sample `PARTIAL_OFFLOAD: false`. Ladder downshift observed live: `14b → 14b-c4k → coder-c8k:7b ("was not fully on the GPU - trying less context / next lighter model")`. Measured: sandbox 0.2 s; self-model render 91 ms cold / 0.94 ms cached; inventory 1247–3470 ms |
| 10 | Self-knowledge answers match live data, 20+ paraphrases in EN/UR/PS/Roman-Urdu | **PASS (offline) / NOT RUN (live model)** | **28/28 paraphrases** recognised, **0 false positives**; every fact asserted against a fresh probe. The live-model test is written and `@pytest.mark.e2e` (deselected) but **was not executed** |
| 11 | Change a fact → answers update; unknown → "I don't know" | **PASS (offline)** | `refresh()` returns a fingerprint; removing a skill from the inventory removes it from `detail()`; the literal *"I don't know - shall I check?"* is in the block; no invented facts |
| 12 | Self-model size measured and inside budget; never in web queries | **PASS** | **813–1086 chars** vs a 1200 budget; owner identity and hostname asserted **absent** from the block; `sanitize_outbound` strips the owner's name from a query, asserted |
| 13 | All Critical/High fixed or documented; no orphan processes; no terminal window | **PARTIAL** | 1 High + 13 Medium/Low fixed, 8 open items documented. Orphan-free is proven for the sandbox; a whole-app launch/shutdown run was **not** repeated. Nothing here changed launch behaviour, so the no-terminal-window property is untouched, not re-verified |


## Bugs this pass found by running the code (not by reading it)

| Where | Bug | Fix |
|---|---|---|
| `ishti/router.py` | Only `status=="broken"` was filtered, so `disabled`/`draft`/`retired` skills still routed; the dashboard's Disable never reached chat | `NON_ROUTABLE` + `registry.disabled_names()` (5 s cache, exception-safe) — **SC-01**, the one High |
| `ishti/selfmodel.py` | `build()` read the cache before writing it, so the fresh inventory was overwritten away — `detail()` said **"Skills (0 registered)"** | merge both directions; regression assertion added — **SC-18** |
| `ishti/selfmodel.py` | `render()` silently omitted a probe that had *failed*, making "no GPU" and "reading the GPU failed" indistinguishable | failed probes render as `[live:failed]` — **SC-14** |
| `ishti/selfmodel.py` | The over-budget trimmer deleted the MISMATCH warnings and the honesty rule | head/body/tail assembly; only body lines drop — **SC-15** |
| `ishti/selfmodel.py` | `detail()` never printed hardware facts, so "what GPU do I have?" had no source | `detail()` leads with `render(data)` — **SC-19** |
| `ishti/skills/meta/skill.py` | Self-knowledge trigger was English-only | Urdu/Pashto/Roman-Urdu patterns: **28/28** paraphrases, 0 false positives — **SC-20**, **SC-21** |
| `skill_creator/research.py` | Budgets but **no timeout**: one unreachable-search fallback hung the pipeline ~5 min | per-call and total wall-clock bounds; self-check proves a hanging search is abandoned — **SC-22** |
| `skill_creator/skill.py`, `drafts.py` | `from ishti.skills import skillsmith` returns the folder, so `.scan`/`.install_folder` did not exist | import the module explicitly — **SC-13** |
| `skill_creator/skill.py` | "never returned usable code" hid the real reason | the last problem is carried into the failure message — **SC-16** |

Two of these were **bugs I introduced during this pass** and caught with tests rather than shipping: the
invented 0.35 gap floor (contradicted the router's own 0.2, so it now defers to the router), and treating a
**router failure** as proof that no skill existed — which would have created duplicates; it now means
*unknown* and builds nothing.

## Open items (all in `NEEDS.md`, none hidden)


## Real generation runs (acceptance test 1, the part that is not green)

Three attempts, all measured, none faked:

| Run | VRAM at the decision | Outcome |
|---|---|---|
| #1 | **965 MB free** of 16303 | **Refused**, naming the exact gap. No draft, nothing installed |
| #2 | ~13.9 GB free after `ishti.gpu.unload_idle_models()` | Reached generation; the governor stepped **14b → 14b-c4k → coder-c8k:7b** rather than partially offloading. **Killed by me** to apply the research timeout; no result claimed |
| #3 | 5341 MB at feasibility, **3131 MB** by generation (the pinned planner reloaded) | **Refused**, naming the exact gap |

The refusal is the *correct* behaviour and it is what acceptance test 2 asks for: no fake success, the exact
missing thing named, and no risk of a split model. But it means **acceptance test 1's real-generation half
is BLOCKED**, because the pinned planner plus a resident translator leave 3.1–5.3 GB free while the coder
rung needs 9922 MiB (`MODEL_DECISIONS.md`).

Closing it needs one of: (a) stop ISHTI so the planner is not resident and re-run the driver; or (b) let the
feasibility gate ask the **governor** (`vram.plan`) instead of comparing free VRAM to a fixed threshold —
the governor already demonstrated it will step the role down, so the gate is stricter than it needs to be.
Neither was done unilaterally: (b) touches a safety gate, and refusing is the safe direction.

## What I would do next, in order

1. Owner answers N21 (confirm `owner_profile.yaml`) and N23 (the uninstalled coder model in `config.json`).
2. Stop ISHTI, run `tools\skill_creator_run.py` for all three acceptance tasks, and record real codegen
   quality + latency + VRAM (closes acceptance test 1).
3. Run the deselected e2e half: `pytest tests/test_selfmodel.py -m e2e -s` (closes acceptance test 10).
4. Decide N25 (which `NEEDS.md` is canonical), then work the ranked open defects in `ISSUES.md`
   (SC-02 → SC-08), starting with the 119 missing `test_file` entries.

| Item | Severity | Why it is open |
|---|---|---|
| `config.json` names coder `qwen3-coder:30b-a3b`, not installed | Medium | works via fallback, but it is a system config file — the owner's call |
| 119/121 manifests have no `test_file`; 10 skills have no self-check | Medium | ~129 mechanical edits across nearly every manifest — far outside this pass's review surface |
| 52/111 subprocess sites without a timeout | Medium | each needs its own judgement (probe vs ffmpeg/model load); not bulk-fixable |
| 145 `except: pass`; 6 `shell=True`; 16 hardcoded `D:\ISHTI` | Medium/Low | per-site decisions; the path one also needs the Mac/Ryzen decision |
| Laya's confidences are uncalibrated (its own warning) | Medium | mitigated (the router's threshold is reused, not second-guessed), not fixed |
| Two competing `NEEDS.md` files | Low | owner's decision which is canonical |
| Real codegen quality of the local coder model | — | `NEEDS.md` N22/N24: needs an idle GPU and a purpose-built harness |

## How to verify any of this yourself

```powershell
# the two acceptance suites
venv\Scripts\python tests\run_skill.py skill_creator      # 27 tests
venv\Scripts\python -m pytest tests\test_selfmodel.py -q # 43 tests
venv\Scripts\python -m pytest tests/test_router_lifecycle_flags.py -q

# the real pipeline on a real task (refuses honestly if VRAM is short; never auto-answers the gate)
venv\Scripts\python tools\skill_creator_run.py "make yourself able to combine a folder of images into one PDF"

# revert everything
git checkout -- .            # or: git checkout ishti-pre-skillcreator-20260930
```

