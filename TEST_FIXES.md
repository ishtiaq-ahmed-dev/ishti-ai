# TEST_FIXES.md - every fix made during the skill loop (prompt 2), with reasons

Format: one entry per fix - what failed, root cause, what changed, why it is not "cheating"
(no test weakened; assertions only strengthened).

---

## FIX-1 (2026-09-30) - `owner` skill: diagnostics could hang a chat turn for minutes

- **Found by**: harness run `tests/run_skill.py owner --runs 1` -> red (pytest-timeout at 90 s);
  reproduced smallest case: generated row `test_language_variants` ("health check" variants) ->
  `IshtiDoctor().check_all()` -> `requests` connect hang.
- **Root cause (2 lines)**: `orchestrator/doctor.py::check_all` ran every live network check
  (search diagnostics, search, page fetch, browser launch, weather, e2e core) inline with no
  overall time cap, so a slow/offline network hung "run diagnostics" indefinitely. The chat-side
  call in `ishti/skills/owner/skill.py` also passed no budget at all.
- **Fix**:
  - `orchestrator/doctor.py`: `check_all(budget_s=45)` + `_live()` wrapper - every live check runs
    in a worker thread joined with the remaining budget; overruns are recorded as skipped. Static
    checks always complete.
  - `ishti/skills/owner/skill.py:249`: the chat path now calls `check_all(budget_s=20)` (a chat
    reply must answer in one turn; the DOCTOR tab keeps the full budget via server.py).
- **Regression test added**: `tests/skills/owner/test_owner_doctor.py`
  - `test_doctor_completes_within_budget` (fails before: TypeError kwarg / 90 s timeout)
  - `test_doctor_budget_respected_when_network_dead` (search manager patched to sleep 600 s ->
    `check_all(budget_s=5)` must still return < 30 s; fails before the fix, passes after).
- **Not cheating**: the budget bounds *time*, not outcomes; live checks still run for real when the
  network is healthy (evidence: doctor reports green search/fetch when online). Assertions got
  stricter, not looser.
- **Result**: owner suite 13/13 passed (258 s incl. model loads); 3 consecutive harness greens.
