# tests/ — QA harness and per-skill suites (QA pass 2026-09-30)

## Per-skill suites (`tests/skills/<skill>/test_<skill>.py`)
Auto-generated from the manifests by `tools/gen_skill_tests.py` — regenerate after adding a skill:

```
venv\Scripts\python tools\gen_skill_tests.py
```

Every suite covers: happy path (English), language variants (Urdu / Pashto / Roman-Urdu / mixed /
misspellings), edge cases (empty, huge, special chars, ambiguous, conflicting), failure handling
(graceful, no crash), safety (manifest tier legal; prompt-injection input stays data), and one
**real-dispatcher** run in a scratch sandbox with `ISHTI_TEST_MODE=1` (real routing and tiers,
nothing real can happen — T2 actions are blocked at the last step and recorded in the audit log).

## Harness (`tests/run_skill.py`)

```
venv\Scripts\python tests\run_skill.py weather                # one skill
venv\Scripts\python tests\run_skill.py weather,calc --runs 2  # several, twice
venv\Scripts\python tests\run_skill.py all --runs 1           # everything (slow)
venv\Scripts\python tests\run_skill.py weather --dry          # what would run, run nothing
venv\Scripts\python tests\run_skill.py all --json             # machine output
```

What it does per run:
- sets `ISHTI_TEST_MODE=1` for the whole run
- per-test timeout (`--timeout`, default 90 s) via pytest-timeout
- GPU/VRAM sample before and after each run (nvidia-smi)
- **audit-log safety assertion**: if the audit log gained a real T2/T3 confirmation during the run
  window, the result becomes `SAFETY-FAIL` (blocks the summary, non-zero exit)
- updates `TEST_STATE.json`: `feature -> {attempts, last_result, consecutive_green, last_error, timestamp}`
- writes machine details to `TEST_STATE_last_run.json`
- exit code 0 only when every target is green

Results mean: `green` = suite passed; `red` = a test failed (see tail lines);
`SAFETY-FAIL` = a real-world T2 action was recorded — treat as a P0 regardless of test outcome.

## Scenarios (`tests/scenarios/*.yaml`)
Prompt-3 inputs: concurrent, multi-skill, mixed-language scenarios with forbidden outcomes and
metrics. Not executed by this harness; the Prompt-2/3 runner consumes them.

## Ground rules
- Everything runs on synthetic data in a scratch sandbox. No real sends, trades, purchases,
  deletes, renames, uploads, or real people. Ever.
- Real components are preferred over mocks for sign-off (each test notes which it uses).
- `--dry` runs no tests.
