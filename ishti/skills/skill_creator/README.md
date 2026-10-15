# skill_creator — ISHTI's self-extending pipeline

> "When nothing I have can do what you asked, I research it, design it, build it, test it in a sandbox,
> do the task, and save it as a real skill — registered OFF until you say yes."

This is the **orchestrator**, not a second skill generator. `skillsmith` already installs and validates
skills, and `skill_lifecycle` already discovers gaps, keeps per-skill memory and writes report cards.
`skill_creator` is the pipeline that drives them and adds the parts neither had: research, an honest
feasibility check, dependency vetting, a sandbox, a 3-green test loop, and DISABLED-then-approved
registration.

## What triggers it (and what does not)

| You say | What happens |
|---|---|
| "make yourself able to …", "teach yourself to …", "add the ability to …", "you can't do that yet", "skill creator" | the full pipeline runs |
| "create a skill that …", "learn to …", "install the skill from \<github url\>" | **not** this skill — `skillsmith` owns those (priority 9 vs 8; asserted in the self-check so the two can never steal each other's requests) |
| "enable / disable / rollback the `<name>` skill", "list drafts" | the admin verbs, handled here |

## The pipeline

| # | Stage | Module | What it guarantees |
|---|---|---|---|
| 1 | Gap detection | `gap.py` | Only builds when nothing owns the request. "Enough confidence" is **the router's own** `min_confidence` (0.2) — not a private threshold, because Laya's scores are uncalibrated. A router *failure* counts as *unknown*, never as *missing*. At most **one** clarifying question. |
| 2 | Research (T0) | `research.py` | Read-only via the existing `web_search`/`web_fetch` skills. Hard budgets (6 searches / 6 fetches / 40 sources), dedup by URL. Everything fetched is **data**: an injected instruction is logged, never obeyed. No web ⇒ an honest empty result, never an invented source. |
| 3 | Risk & feasibility | `design.py` | Deterministic keyword rules, not an LLM judgement, so a refusal is auditable. T3 is refused outright; credentials/purchases name exactly what is missing. The tier is **never** lowered automatically. Also checks the coder rung against the VRAM budget and the ladder. |
| 4 | Design doc | `design.py` | Writes `SKILL_DESIGN.md`: goal, I/O, data flow, pinned libraries, scope + proposed tier, failure modes, timeouts, tests. |
| 5 | Dependency vetting | `vetting.py` | Zero installs. Exact-name + typosquat (`difflib`) → PyPI JSON (licence, release age, sha256, `requires_python`, yanked) → OSV.dev (CVE **and** `MAL-*` malware) → stdlib `ast` risky-call table, merged with `skillsmith.scan`. **Fails closed**: a failed lookup is not vetted. Records everything in `vetting.json`. |
| 6 | Implement | `skill.py` | One self-contained module (≤300 lines) + manifest, ≤3500 out-tokens per call, ≤5 coder calls, under `vram.admit`. On failure it reports *why* ("the module is N lines (limit 300)"), never just "it failed". |
| 7 | Sandbox | `sandbox.py` | One `Popen` inside a per-run Windows **Job Object**: 512 MB process / 1 GB job / ≤8 processes / 90 s CPU / 120 s wall, cleaned env, pinned cwd, `CREATE_NO_WINDOW`. `KILL_ON_JOB_CLOSE` means even a crashed supervisor leaves no orphan. |
| 8 | Test loop | `drafts.py` | Fix → retest until **3 consecutive** greens (a failure resets the counter). 8 attempts on one issue, then stop and `log_need` to the runtime gap log (`data/needs_runtime.md`). |
| 9 | Package + register | `drafts.py` | `v0.1.0`, `status: "disabled"`, `author: "ISHTI skill_creator"`, pinned deps, rollback record, README + CHANGELOG. Installation is T2 and goes through `skillsmith.install_folder`. |

## The rules it cannot break

- A generated skill may **not** change its own `tier` or `scope` — `drafts.assert_no_self_modification()` raises.
- `skill_creator` may write **only** into `ishti/skills/_drafts/` (`ishti/scope.py` has one deliberately narrow
  exception). It cannot write the live skill tree, `ishti/scope.py`, `ishti/router.py`, the kill switch or the
  audit log.
- Drafts are invisible to the router by construction: `load_skills()` skips `_`-prefixed folders, *and* a draft's
  manifest says `status: "draft"`.
- A new skill arrives **DISABLED**, and the router honours that (`ishti/router.py` `NON_ROUTABLE`); enabling needs
  the owner's explicit yes, and T2 actions go through `ask_first` + `test_mode.guarded`.
- After 3 consecutive live failures a skill **auto-disables**; `rollback()` restores the last backup.
- The sandbox is a **resource governor, not a security boundary**: it does not restrict the network (the result
  says so explicitly) and it cannot see writes made by a subprocess. Filesystem/network policy stays with the
  manifest `scope`.

## Layout

```
ishti/skills/skill_creator/
  manifest.json   tier T2, priority 8, laya:false, scope.write = [data, ishti/skills/_drafts]
  skill.py        match/run, the pipeline, code generation, owner ops, self-check
  gap.py          stage 1   · research.py stage 2 · design.py stages 3-4 · vetting.py stage 5
  sandbox.py      stage 7 (Job Object)   · drafts.py stages 8-9
ishti/skills/_drafts/<name>/   research.md, SKILL_DESIGN.md, vetting.json, skill.py, manifest.json
```

## Running it

```powershell
# module self-checks (offline - no model, no web)
venv\Scripts\python -m ishti.skills.skill_creator.skill     # routing, budgets, the full pipeline
venv\Scripts\python -m ishti.skills.skill_creator.gap       # gap detection
venv\Scripts\python -m ishti.skills.skill_creator.research  # budgets + injection logging
venv\Scripts\python -m ishti.skills.skill_creator.design    # feasibility + honest refusals
venv\Scripts\python -m ishti.skills.skill_creator.vetting   # typosquat, licences, live PyPI/OSV
venv\Scripts\python -m ishti.skills.skill_creator.drafts    # caps, the 3-green loop, rollback
venv\Scripts\python -m ishti.skills.skill_creator.sandbox   # proves the 512 MB cap really kills

# the acceptance suite (pywin32 needed for the hard-cap cases)
venv\Scripts\python tests\run_skill.py skill_creator
```

## Limits

- One draft in flight per task; ≤12 files and ≤256 KB per draft.
- Real code generation needs the `ishti-qwen2.5-coder:14b` rung to fit in VRAM beside the planner; if it does
  not, ISHTI says so rather than offloading part of a model onto the CPU.
- Research quality depends on the web being reachable; without it the pipeline continues and states that
  research was not possible.
- Codegen quality of the local coder model is **not yet measured** for real tasks — recorded as an
  explicit measurement gap in [`docs/ACCURACY.md`](../../../docs/ACCURACY.md) §3.

See `CHANGELOG.md` for the version history and for the measured evidence behind these limits.

