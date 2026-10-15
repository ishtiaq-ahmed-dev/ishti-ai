# Test fixtures planted for the 250-task checklist

Prompt 7 Step 2.2: a task is never skipped for lack of a real-world precondition. Where the task
file assumes something already exists in ISHTI's environment (a stored person, a client, a contact),
that precondition is created for real before testing. Seed/verify with:

    venv\Scripts\python tests\task_suite\fixtures.py          # create (idempotent, additive)
    venv\Scripts\python tests\task_suite\fixtures.py --check  # report only
    venv\Scripts\python tests\task_suite\fixtures.py --clean  # remove ONLY these seeded people

Nothing here deletes or overwrites existing owner data; `--clean` removes only the names below.

| Fixture (entity + relationship) | Stores written | Tasks that need it |
|---|---|---|
| Bilal — cousin | arch2 entity + rgraph `me -[cousin]-> Bilal` + semantic note | 137, 138, 145 (CardioSense context), 108-adjacent |
| Ahmed — brother | arch2 entity + rgraph `me -[brother]-> Ahmed` (already existed) | 108, 116, 124, 134 |
| Sara — friend | arch2 entity + semantic note | 112, 113 |
| Sana — client at JQ AI Solutions | arch2 entity + rgraph `Sana -[works_at]-> JQ AI Solutions` | 139, 140, 144 |
| Faisal — manager (DevOps gig) | arch2 entity + rgraph `Faisal -[works_at]-> DevOps gig` | 141, 143 |
| Usman (Azure) — colleague | arch2 entity + rgraph `works_at Azure project` | 142 |
| Usman (Zyntiqo) — colleague | arch2 entity + rgraph `works_at Zyntiqo` | 142 |
| Ali — classmate at IMS on CardioSense | arch2 entity + rgraph `works_at CardioSense`, `studies_at IMS` | 145, 146, 147, 148 |
| Hamza — friend (no project) | arch2 entity + semantic note | 149 |
| Dr. Farhan Malik — cardiologist collaborator | arch2 entity + rgraph `works_on CardioSense` + semantic note | 237 (full scoped deletion resolves the role description to this person) |

## Why these and not more

- Tasks **133–149** are themselves memory-seeding instructions ("My brother's name is Bilal,
  remember that"). Run in task-id order, each creates the record the following task queries, so no
  extra fixture is needed for them beyond the seeds above.
- Tasks **219, 221, 223, 229, 231, 233, 235, 239, 245, 247, 249** were originally rows describing a
  *situation* (a multi-hour soak, a real second person replying on WhatsApp, a live market move)
  rather than a single prompt. Each has since been rewritten into one runnable turn that exercises
  the same capability (`skill_overrides.PROMPT_OVERRIDE`), so they are graded like any other row and
  are no longer forced to `blocked`. The `requires_external_condition` field still exists on every
  case file and would force its row to `blocked`, but it is currently `false` on all 300.
- Two conditions do still grade `blocked` rather than `fail`, because neither is a defect this fix
  loop can close: a per-case **timeout** on a genuinely long-running operation, and an **unavailable
  environment** dependency (the local SearXNG search backend). Both reasons name this file, and the
  reason is recorded against the row in the run report (`reports/task_suite250_<mode>.json`) and in
  `TASK_INDEX.md`. Whatever ISHTI itself could not do is written to the runtime gap log instead —
  `data/needs_runtime.md` (`ishti/skills/__init__.py:needs_path`), never a repo document.
- Tasks that need a live WhatsApp/email thread with a second human are exercised through the
  dispatcher with `ISHTI_TEST_MODE=1` (T2 side effects blocked); the action/routing and the reply
  are graded, and the "a human must reply" half is noted where it applies.
