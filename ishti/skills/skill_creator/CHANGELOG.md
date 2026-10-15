# Changelog — skill_creator

## 1.0.0 — 2026-09-30

First release. Built in one pass by the integrator after the research sub-agents' inference quota was
exhausted. The briefs it was built from (`IMPLEMENTATION_PLAN.md`, `RESEARCH.md`) and the needs log
(`NEEDS.md`) have since been retired; this README and the module docstrings now carry that content.

### Added
- Nine-module self-extending pipeline: gap detection → research → risk/feasibility → design doc →
  dependency vetting → code generation → sandbox → 3-green test loop → package/register.
- `gap.py`: gap detection against the router's own `min_confidence`; recurring-gap signal from
  `skill_lifecycle.discover_gaps()`; one clarifying question maximum; a router failure is *unknown*, not
  *missing*.
- `research.py`: T0 read-only research through the existing `web_search`/`web_fetch` skills, hard budgets,
  URL dedup, `research.md` with dated sources, prompt-injection attempts logged and ignored, honest
  no-network result.
- `design.py`: deterministic T3/credential/purchase/side-effect rules, VRAM + coder-ladder check,
  `SKILL_DESIGN.md` writer, `refuse()` that names exactly what is missing, tier never lowered.
- `vetting.py`: zero-install vetting — `difflib` typosquat rejection, PyPI JSON, OSV.dev (CVE + malware),
  licence allow-list that fails closed, `ast` risky-call table, `vetting.json`. No bandit/pip-audit/semgrep.
- `sandbox.py`: per-run Windows Job Object (512 MB process / 1 GB job / ≤8 processes / 90 s CPU / 120 s wall),
  cleaned env, `CREATE_NO_WINDOW`, `KILL_ON_JOB_CLOSE`, honest `hard_limits=False` fallback without pywin32,
  and an explicit statement that the network is **not** restricted.
- `drafts.py`: draft lifecycle — 12-file/256 KB caps, one draft in flight, **3 consecutive** green runs with
  reset on failure, packaging to `v0.1.0` `status:"disabled"`, registration via `skillsmith.install_folder`,
  owner-gated enable/disable, auto-disable after 3 failures, one-command rollback from a backup.
- `skill.py`: `match()` limited to capability-gap phrasings (never stealing `skillsmith`'s), the pipeline
  with injectable `llm_fn`/`research_fn`/`gap_fn`, generation budgets (≤300 lines, ≤3500 tokens, ≤5 calls)
  that report *why* they failed, and the owner verbs enable/disable/rollback/status.
- `tests/skills/skill_creator/test_skill_creator_core.py`: 27 acceptance tests.

### Changed
- `ishti/scope.py`: added a **narrow** write exception for `ishti/skills/_drafts` (deliberately narrower than
  `skillsmith`'s, which may write the whole skills tree).

### Fixed (found by this work's own tests)
- `generate()` reported "never returned usable code" with no reason; it now carries the last problem.
- `from ishti.skills import skillsmith` returns the folder, not the module — `.scan`/`.install_folder` did not
  exist on it. Now imported explicitly.
- `ask_first`'s reply dropped the pipeline's audit facts; they are now carried through the gate.

### Not in this release
- No real end-to-end code generation has been measured on the three acceptance tasks (still true —
  recorded as a measurement gap in [`docs/ACCURACY.md`](../../../docs/ACCURACY.md) §3).
- No `owner_profile.yaml` confirmation (it ships `confirmed: false` on purpose).
