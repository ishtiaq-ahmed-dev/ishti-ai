# Accuracy: what ISHTI has measured, and what it has not

The README says accuracy is "not yet measured". That was true of the *voice and multilingual*
benchmark and of real-world use on owner data. It was **not** true of the task suite, which has been
run and graded. This page states both halves precisely, because the difference matters to anyone
deciding whether to rely on ISHTI.

Nothing on this page is new work. It records numbers the project had already produced into
`reports/` and copied here verbatim, so that a reader can find them without a run. The three raw run
artifacts named in section 1 are themselves tracked in this repository, and they are the files the
numbers below were read from; the rest of `reports/` is ignored (see `.gitignore`), so a fresh run
adds output without adding noise to the tree.

---

## 1. Measured: the 300-task suite

`tests/task_suite/` holds a hand-written list of 300 realistic assistant tasks across 30 categories
(lookup, reminders, files, app control, browser, WhatsApp, email, documents, research, memory,
multilingual, ambiguity, cost awareness, onboarding, and so on). Each task is graded `pass`,
`partial`, `fail` or `blocked`. The per-task verdicts are tracked in
[`tests/task_suite/TASK_INDEX.md`](../tests/task_suite/TASK_INDEX.md).

Two recorded runs from **2026-10-04**:

| Artifact | Mode | Cases | pass | partial | blocked | fail |
|---|---|---:|---:|---:|---:|---:|
| `reports/task_suite250_live_first.json` (10:10) | live | 250 | 177 | 9 | 63 | 1 |
| `reports/task_suite250_live.json` (15:10) | **live** | 300 | **292** | 4 | 4 | 0 |
| `reports/task_suite250_route.json` (14:42) | route | 300 | 272 | — | — | 28 `info` |

- **live** grades the whole turn: which skill ran *and* what it replied.
- **route** grades skill selection only, with no execution.

Headline: **292 of 300 tasks passed in live mode (97.3%)** on the later run.

### Why `blocked` is not a pass, and not a failure either

`tests/task_suite/FIXTURES.md` explains the category: a task is `blocked` when its row describes a
*situation* that cannot be forced inside one isolated run — a multi-hour soak, a second human
replying on WhatsApp, a live market move. The first run left 63 tasks blocked for missing
preconditions; `FIXTURES.md` then defines the real records to seed first, and the later run left
only 4. The four still blocked are ids **5, 8, 22, 226**. The four `partial` are ids **16, 72, 161,
235**. Treat 292/300 as "292 unambiguously passed", not as "296 minus 4 failures".

### What these numbers are not

Read this before quoting them anywhere:

- **Self-graded.** This is the project's own suite, run and scored by the project's own runner. It
  is not an independent benchmark, and nobody outside the project has reproduced it.
- **Dated 2026-10-04.** The tree has changed materially since (bounded boot recovery, document
  overwrite protection, MCP server, eval harness work). A result is only valid for the revision it
  ran against. **A fresh full run is still an outstanding release gate** —
  `docs/RELEASE_READINESS.md` gate 1.
- **The suite is the author's idea of realistic.** It measures whether ISHTI does what its author
  expected, which is weaker than measuring whether it does what a stranger needs.
- **`info` in route mode is not a pass.** 28 rows were informational, not graded pass/fail.

## 2. Measured offline: the eval harness

`tests/eval/` runs five offline suites deterministically — `understanding`, `dead_end`,
`correctness`, `files`, `memory`. Its design is unusually honest: a metric that cannot be measured
is emitted as `{"status": "unmeasured", "reason": "..."}` rather than as a zero or an estimate. Run
it with:

```
venv\Scripts\python tests\eval\run.py all --repeat 3
```

Several metrics in it are deliberately unmeasured and the harness says why, for example:
`argument_accuracy` (the corpus has no structured argument labels), `completion_accuracy` (routing
alone produces no completion), `arch2_profile_client_isolation` (no profile namespace exists to
measure), and `orchestrated_recovery_rate` (no injectable end-to-end recovery seam yet).

Route-only accuracy is also measured here against a labelled set — `docs/ARCHITECTURE.md` §1 quotes
114/120 correct (95%) with **zero model calls**, which is the project's main cost-control claim.

## 3. NOT measured

Stated plainly, because a gap you name is a gap a reader can trust you about:

| Not measured | Why |
|---|---|
| **Voice accuracy (WER/CER), any language** | `reports/multilingual_media_benchmark.json` reports `unmeasured` for every language: the synthetic corpus exists but no real audio was scored. Urdu and Pashto speech accuracy is therefore **claimed, not measured** — despite being ISHTI's clearest differentiator. |
| **Real-world accuracy on owner data** | Every corpus is synthetic. No real chat, file or message has been scored. |
| **Profile / client memory isolation** | The retrieval API exposes no profile or client namespace, so cross-namespace retrieval cannot be tested. This is why the README says memory is not isolated between people. |
| **Completion quality of long tasks** | `workflows.py` plans up to 12 steps; no outcome-quality metric exists for a finished multi-step job. |
| **Anything on hardware other than the author's PC** | Local mode is verified on one NVIDIA card. |
| **Accuracy of the self-written skills** (`skill_creator`, `skillsmith`) | Codegen quality of the local coder model on real tasks is explicitly not measured. |

## 4. How to reproduce

```
venv\Scripts\python tests\task_suite\fixtures.py --check   # verify the seeded preconditions
venv\Scripts\python tests\task_suite\run_250.py --mode live # the task suite (route|live)
venv\Scripts\python tests\eval\run.py all --repeat 3
venv\Scripts\python tools\evidence.py --markdown docs\EVIDENCE.md
```

`run_250.py` loads every case in `tests/task_suite/cases/` — the file name still says 250 but the
directory now holds 300 — and rewrites the status column of `TASK_INDEX.md`, which is why that file
is the tracked record of the last run. How to run it and what each mode does not grade is in
[tests/task_suite/README.md](../tests/task_suite/README.md).

Everything above writes JSON under `reports/`. The directory is ignored, so a new run adds files that
do not appear in `git status` — but the three task-suite artifacts named in section 1 were already
tracked before that rule and stay tracked, which is why their names are still clickable. If you publish
a number from a *new* run, copy it into a tracked file the way this page does — otherwise the evidence
is one `git clean` away from gone. That is not hypothetical: earlier eval artifacts lived in
`docs/audit/eval_*.json`, and the current working tree deletes that whole directory. `tests/eval/run.py`
used to default `--output` there, so a plain run recreated a directory the tree no longer tracks; it now
defaults to `reports/`.

`tools/evidence.py` is the answer to that, for the parts of the suite it covers: it writes one report
in which every metric carries the version and source hash that produced it, and it refreshes the
tracked [EVIDENCE.md](EVIDENCE.md). This page is still the wider picture — it names what has never
been measured at all, which the evidence report cannot see.
