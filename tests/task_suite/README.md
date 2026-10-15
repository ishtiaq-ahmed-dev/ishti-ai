# The 300-task suite

300 hand-written, realistic assistant requests across 30 categories — lookup, reminders, files,
app control, browser, WhatsApp, email, documents, research, memory, multilingual, ambiguity, cost
awareness, onboarding. Each task is graded `pass`, `partial`, `fail`, `blocked` or `info`, and the
per-task verdicts are kept in [TASK_INDEX.md](TASK_INDEX.md).

This file is the reader-facing account of how to run it and what it grades. The results it has
produced, and the caveats that belong next to them, are in [../../docs/ACCURACY.md](../../docs/ACCURACY.md).

## Run it

From the project root:

```
venv\Scripts\python tests\task_suite\fixtures.py --check     # are the seeded preconditions there?
venv\Scripts\python tests\task_suite\fixtures.py             # seed them (additive, idempotent)
venv\Scripts\python tests\task_suite\run_250.py --mode route # skill selection only, offline
venv\Scripts\python tests\task_suite\run_250.py --mode live  # the whole turn (needs Ollama + a GPU)
```

The file name still says 250 while the directory holds 300 cases; `run_250.py` loads whatever is in
`cases/` and grades all of it. Narrow a run with `--ids 5,22,226`, raise the per-case limit with
`--timeout 400`, and skip the tracker rewrite with `--no-index`.

`--mode route` needs no model and no GPU: it asks `ishti.tool_routing.route()` which skill each
request picks and grades that choice alone. `--mode live` runs each request through the real
dispatcher in its own subprocess with `ISHTI_TEST_MODE=1`, so a T2 side effect is stopped at the
last step and recorded rather than performed.

## What each mode actually grades

| Mode | Grades | Does not grade |
|---|---|---|
| `route` | Which skill the deterministic rules select | The reply, the tool result, whether the request was understood |
| `live` | The whole turn: which skill ran **and** what it replied | Whether the reply was *true*, beyond the case's cue phrases |

A `live` verdict comes from the case's own `pass_criteria`: a `bad_any` phrase in the reply fails
it, a missing `ok_any` cue makes it `partial`, and a skill that raised makes it `fail`. That is
keyword grading against cues the author wrote — it catches regressions and lies, and it cannot tell
a correct answer from a plausible one.

## The five verdicts

- **pass** — the case's criteria were met.
- **partial** — the right shape, the wrong specifics: the expected cue is missing, or a different
  skill handled it.
- **fail** — a real defect: a forbidden phrase appeared, or a skill raised.
- **blocked** — the row describes a *situation* that one isolated run cannot force: a multi-hour
  soak, a second human replying on WhatsApp, a live market move. Also used for two environment
  causes that are not code defects — the per-case timeout on a long operation, and the local search
  backend (SearXNG) being down. [FIXTURES.md](FIXTURES.md) records which fixture each row needs.
- **info** — `route` mode only: the request is free-form, so there is no routing expectation to
  grade. **An `info` row is not a pass.**

`blocked` is deliberately neither: a blocked row is not evidence of working code, and counting it
as a failure would hide the real pass rate. Read a headline as "N unambiguously passed", never as
"everything minus the failures".

## Where results go

`reports/task_suite250_<mode>.{json,md}` — and `reports/` is gitignored, so **a number you publish
must be copied into a tracked file** or it is one `git clean` from gone. Two tracked files already
do this: `docs/ACCURACY.md` (the 2026-10-04 runs, quoted verbatim) and `TASK_INDEX.md`, which
`run_250.py` rewrites with the status of the run that just finished.

A run also reseeds the fixtures first, so `TASK_INDEX.md` changing is expected — review it before
you commit it.

## What this suite is not

- **Not an independent benchmark.** It was written, run and scored by the project's own author with
  the project's own runner. Nobody outside the project has reproduced it.
- **Not valid across revisions.** A result belongs to the revision it ran against. The tree has
  changed materially since the 2026-10-04 runs; a fresh full run is an outstanding release gate
  (`docs/RELEASE_READINESS.md`).
- **Not real user data.** Every fixture is synthetic. Nothing here has been scored against a real
  chat, file or message.
- **Not the author's own idea of *your* work.** It measures whether ISHTI does what its author
  expected, which is weaker than measuring whether it does what a stranger needs.

## Files

| File | What it is |
|---|---|
| `cases/<id>.json` | One task: prompt, category, difficulty, `pass_criteria`, and whether it needs an external condition |
| `run_250.py` | The runner: loads `cases/`, grades, writes `reports/task_suite250_<mode>.*`, updates `TASK_INDEX.md` |
| `run_tasks.py` | The grading harness `run_250.py` reuses (`run_route` / `run_live`) |
| `fixtures.py` | Seeds the people and graph edges the tasks refer to by name (`--check`, default seed, `--clean`) |
| `FIXTURES.md` | Which task needs which fixture, and why each `blocked` row is blocked |
| `TASK_INDEX.md` | The tracked tracker: the status column is the record of the last run |
| `cases.py`, `build_master.py`, `MASTER_300.json` | How the cases were generated and the master list |
