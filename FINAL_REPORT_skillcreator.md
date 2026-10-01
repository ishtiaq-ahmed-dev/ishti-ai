# FINAL_REPORT_skillcreator.md — skill_creator + self-knowledge + issue hunt

> **Why this file has a suffix:** while this session was running, **another agent session worked in the same
> workspace and took over the canonical filenames** — it committed 5 of its own commits
> (`0f1bb99 … cfd005b`: perf/browser, perf/llm, docs), which swept my in-flight files into them, archived
> my plan to `IMPLEMENTATION_PLAN_skillcreator.md`, and **overwrote `RESEARCH.md` and `FINAL_REPORT.md`**
> with its own "Remote Control (Telegram + WhatsApp)" work. My code, tests, and my sections in `PROGRESS.md`,
> `ISSUES.md`, `TEST_RESULTS.md` and `NEEDS.md` all survived intact. This report and `RESEARCH_skillcreator.md`
> are restored copies under distinct names; **the other session's files were left untouched** (see N30).

**Date:** 2026-09-30/10-01 · **Machine:** Windows 11 Pro 26H2 build 26300, RTX 5070 Ti 16 GB (driver 610.88),
64 GB RAM, `D:\ISHTI\venv` Python 3.11.9 · **Rollback point:** commit `2801551`, tag
`ishti-pre-skillcreator-20260930`

## Verdict: **READY WITH KNOWN ISSUES**

Everything claimed below was measured on this machine on 2026-09-30/10-01; raw output is in
`TEST_RESULTS.md`. Three things are **not** claimed, and they are why this is not a plain READY: the three
real end-to-end generation tasks were only partly executed (VRAM pressure and an unreachable search backend
got in the way), the live-model (e2e) half of the self-knowledge battery was not run, and four build
sub-agents died mid-session on an inference cap so the work was serialised.

## What was built

| Deliverable | State |
|---|---|
| `RESEARCH_skillcreator.md` (72,634 B, 6 parallel research agents) | **DONE** — every claim VERIFIED (URL/command + date) or UNVERIFIED |
| `IMPLEMENTATION_PLAN_skillcreator.md` (my plan; renamed by the other session) | **DONE** — 13 work packages, exclusive file ownership, rollback points |
| `ishti/skills/skill_creator/` (9 modules + README + CHANGELOG) | **DONE** — all self-checks green; **27/27** acceptance tests |
| `ishti/selfmodel.py` + `owner_profile.yaml` | **DONE** — self-check green; **43/43** acceptance tests |
| `ishti/router.py` + `ishti/skills/registry.py` (prerequisite bug) | **DONE** — proven red→green, no regression |
| `ishti/scope.py` (one narrow exception) · `ishti/llm.py` (`style()`) · `meta` skill (4 languages) | **DONE** — self-checks green |
| `ISSUES.md` (+SC-01..SC-24), `NEEDS.md` (+N21..N30), `TEST_RESULTS.md`, `PROGRESS.md` | **DONE** |
| `tests/test_selfmodel.py`, `tests/skills/skill_creator/`, `tests/test_router_lifecycle_flags.py`, `tools/skill_creator_run.py`, `tools/check_paraphrases.py` | **DONE** |

**Design decision worth knowing:** `skill_creator` is an **orchestrator**, not a second generator.
`skillsmith` already installs/validates skills and `skill_lifecycle` already discovers gaps and keeps
per-skill memory; duplicating them would have been the obvious wrong move (`RESEARCH_skillcreator.md` D1).

## Acceptance gate, item by item

| # | Verdict | Evidence |
|---|---|---|
| 1 | **PARTIAL** | Offline end-to-end **proven**: 3 consecutive green sandbox runs, `v0.1.0` `status:"disabled"`, owner gate pending, nothing installed. Real model runs: **1 of 3 tasks reached generation** |
| 2 | **PASS** | Measured live: *"a local coder model that is installed and fits in VRAM (… 965 MB free of 16303 MB) - I will not offload part of a model onto the CPU"*. T3/credential/purchase refusals proven in `design.py` + tests |
| 3 | **PASS** | `test_malicious_page_instructions_are_logged_and_ignored`; `research.md` gains an "Ignored instruction-shaped text" section |
| 4 | **PASS** | `reqeusts` rejected naming `requests`; live OSV `pyyaml 5.3.1 → ['GHSA-8q59-q68h-6hv4','PYSEC-2021-142']` |
| 5 | **PASS (scope) / NOT PROVEN (network)** | Scope blocks `ishti/skills/<name>`, `ishti/scope.py`, `ishti/router.py`. **The sandbox does not restrict the network** — every result says `net_restricted: False` rather than pretending |
| 6 | **PASS** | `assert_no_self_modification` raises; the `scope.py` exception covers `_drafts` only |
| 7 | **PASS** | 2.5 GB allocation died at **peak 454.4 MB** under the 512 MB cap; infinite loop killed by the wall clock; a spawned **grandchild's** heartbeat froze after the kill |
| 8 | **PASS** | Real-router test: DISABLED → not routed → enabled → routed → 3 failures → auto-disabled → not routed; plus a rollback test |
| 9 | **PASS** | Every `/api/ps` sample `PARTIAL_OFFLOAD: false`. Ladder downshift observed live: `14b → 14b-c4k → coder-c8k:7b ("was not fully on the GPU - trying less context / next lighter model")` |
| 10 | **PASS (offline) / NOT RUN (live model)** | **28/28 paraphrases** across EN/Urdu/Pashto/Roman-Urdu, **0 false positives**; the e2e live-model test is written and `@pytest.mark.e2e` (deselected) but **was not executed** |
| 11 | **PASS (offline)** | `refresh()` returns a fingerprint; removing a skill from the inventory removes it from `detail()`; the literal *"I don't know - shall I check?"* is in the block |
| 12 | **PASS** | **813–1086 chars** vs a 1200 budget; owner identity and hostname asserted **absent** from the block; `sanitize_outbound` strips the owner's name from a query |
| 13 | **PARTIAL** | 1 High + 13 Medium/Low fixed, 10 open items documented. Orphan-free proven for the sandbox; a whole-app launch/shutdown run was **not** repeated, and nothing here changed launch behaviour |

## Final verification (one run, 5 suites)

`pytest tests/test_selfmodel.py tests/skills/skill_creator tests/test_router_lifecycle_flags.py tests/test_prompt3_skills.py -q`
→ **`[100%]`, 106 tests, 0 failed** (43 + 27 + 14 + 22).

## Bugs found by running the code (not by reading it)

| Where | Bug | Fix |
|---|---|---|
| `ishti/router.py` | Only `status=="broken"` was filtered, so `disabled`/`draft`/`retired` skills still routed and the dashboard's Disable never reached chat | `NON_ROUTABLE` + `registry.disabled_names()` — **SC-01**, the one High |
| `ishti/selfmodel.py` | `build()` read the cache before writing it, so the fresh inventory was overwritten away — `detail()` said **"Skills (0 registered)"** | merge both directions — **SC-18** |
| `ishti/selfmodel.py` | `render()` silently omitted a probe that had *failed* | failed probes render as `[live:failed]` — **SC-14** |
| `ishti/selfmodel.py` | The over-budget trimmer deleted the MISMATCH warnings and the honesty rule | head/body/tail; only body lines drop — **SC-15** |
| `ishti/selfmodel.py` | `detail()` never printed hardware facts, so "what GPU do I have?" had no source | `detail()` leads with `render(data)` — **SC-19** |
| `ishti/skills/meta/skill.py` | Self-knowledge trigger was English-only | Urdu/Pashto/Roman-Urdu: **28/28**, 0 false positives — **SC-20**, **SC-21** |
| `skill_creator/research.py` | Budgets but **no timeout**: one unreachable-search fallback hung the pipeline ~5 min | per-call + total wall-clock bounds; self-check proves a hanging search is abandoned — **SC-22** |
| `skill_creator/skill.py`, `drafts.py` | `from ishti.skills import skillsmith` returns the folder, so `.scan`/`.install_folder` did not exist | import the module explicitly — **SC-13** |
| `skill_creator/skill.py` | "never returned usable code" hid the real reason | the last problem is carried into the failure message — **SC-16** |
| `skill_creator/drafts.py` | `register()` backed up *before* installing, so a first registration had nothing to roll back to | snapshot after install — **SC-24** |

**Two were bugs I introduced during this pass**, caught by tests rather than shipped: an invented 0.35 gap
floor that contradicted the router's own `min_confidence` (0.2) — it now defers to the router — and treating a
**router failure** as proof that no skill existed, which would have created duplicates; it now means
*unknown* and builds nothing.


## Open items (all in `NEEDS.md`, none hidden)

| Item | Severity | Why open |
|---|---|---|
| Real codegen quality of the local coder model | — | blocked by VRAM (**SC-23**): the pinned planner leaves 3.1–5.3 GB free vs a 9922 MiB coder need |
| `config.json` names an uninstalled coder model | Medium | works via fallback; it is a system config file — the owner's call |
| 119/121 manifests without `test_file`; 10 skills without a self-check | Medium | ~129 mechanical edits across nearly every manifest |
| 52/111 subprocess sites without a timeout; 145 `except: pass`; 6 `shell=True`; 16 hardcoded `D:\ISHTI` | Medium/Low | per-site judgement, not bulk-fixable |
| Laya confidences uncalibrated (its own warning) | Medium | mitigated, not fixed |
| Two competing `NEEDS.md` files, and a duplicate `N21` numbering | Low | owner's decision which is canonical |
| Another session committing into this workspace concurrently | **Process** | caused the overwritten/lost deliverables — see N30 |

## How to verify any of this

```powershell
venv\Scripts\python -m pytest tests\test_selfmodel.py tests\test_router_lifecycle_flags.py -q
venv\Scripts\python tests\run_skill.py skill_creator
venv\Scripts\python -m ishti.selfmodel                  # self-model self-check (prints measured numbers)
venv\Scripts\python -m ishti.skills.skill_creator.skill  # pipeline self-check, fully offline
venv\Scripts\python tools\check_paraphrases.py           # the 4-language battery
venv\Scripts\python tools\skill_creator_run.py "make yourself able to combine a folder of images into one PDF"
git checkout -- .          # or: git checkout ishti-pre-skillcreator-20260930
```

