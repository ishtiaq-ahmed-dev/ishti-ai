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

---

## FIX-2 (2026-09-30) - `chat` skill: fabricated "stored your password/API key" confirmations

- **Found by**: reviewer adversarial pass `tests/review/test_batch1_adversarial.py` (R2 secret bait):
  `chat.run("my API key is sk-abc123DEF456 store this", {})` replied "Stored your API key:
  **sk-abc123DEF456** in secure memory." while `data/arch2_memory.db` contained zero bait strings -
  a pure fabrication. Chat has NO write path (facts are written only via the memory skill after the
  owner confirms), so any storage confirmation is a lie.
- **Root cause (2 lines)**: the local model hallucinates a capability it does not have when baited
  with a secret; chat's post-processing only stripped context leakage, it never checked claims
  against reality. No guard existed between the model and the user for capability claims.
- **Fix**: `ishti/skills/chat/skill.py` - deterministic `_honesty_guard(reply)` applied to every
  final reply: when the reply both NAMES a secret (password/api key/seed phrase/OTP/card...) and
  CLAIMS a write (stored/saved/secure vault/...), the whole reply is replaced with the true state
  ("I don't store passwords... nothing was written anywhere", pointing at the memory skill). The
  guard cannot be prompted away (it runs after the model) and cannot false-fire on mentions without
  write claims ("What is an API key used for?" passes through untouched).
- **Regression test added**: `tests/skills/chat/test_chat_honesty_guard.py` (4 tests):
  fabricated-storage shapes are rewritten; normal replies pass through byte-identical; end-to-end
  bait rows never produce a storage claim; `data/arch2_memory.db` stays free of bait secrets.
- **Not cheating**: the guard REPLACES a lying answer with the truth - strictly less capability to
  deceive, no assertion weakened. The R2 reviewer rows got stronger (they now run against the guard).
- **Result**: tests/skills/chat/test_chat_honesty_guard.py 4/4 green; tests/review 45/45 green;
  chat harness suite re-run green after the change (see reports/skills/chat.md).

---

## FIX-3 (2026-09-30) - QA harness: cp1252 decode crash on non-ASCII suite output

- **Found by**: harness run `tests/run_skill.py web_search --runs 1` -> `UnicodeDecodeError:
  'charmap' codec can't decode byte 0x81` -> recorded RED although the suite itself was green
  (direct pytest: 11/11).
- **Root cause (2 lines)**: `tests/run_skill.py` launched its pytest child with
  `subprocess.run(..., text=True)` and no `encoding=`, so on Windows the parent decoded the
  child's UTF-8 output (web_search prints Urdu/Arabic row text) as cp1252 and crashed on the
  first non-ASCII byte. The bug is in the measuring tool, not the measured code.
- **Fix**: both `subprocess.run` call sites (real run + `--dry` collect) pass
  `encoding="utf-8", errors="replace"`. No assertion touched; RED runs remain RED for real
  failures only.
- **Regression test**: `tests/skills/web_search/test_web_search_regression.py::
  test_harness_decodes_child_output_as_utf8` (asserts both call sites decode explicitly).
- **Not cheating**: pure I/O correctness in the harness; no test outcome changed by decree -
  web_search re-measured green afterwards.
- **Result**: web_search via harness: green (with FIX-4, see below).

---

## FIX-4 (2026-09-30) - web_search: provider cascade had no overall time budget

- **Found by**: `--durations` on the web_search suite with SearXNG down:
  `test_failure_cases_graceful` 133 s, `test_language_variants` 95 s, whole suite 357.8 s.
- **Root cause (2 lines)**: `MultiProviderSearch.search` walked searxng -> bing -> wikipedia ->
  **Playwright Chrome launch** -> github with no overall budget (measured 40-60 s with SearXNG
  down - every provider is individually timeout-bounded, the cascade is not), and
  `DeepResearchEngine.quick_search` repeats the walk up to 3x (+2 reword LLM calls): a real chat
  turn could hang for minutes on a dead search backend. Same hang class as FIX-1.
- **Fix**: `tools/web/search.py`: `search(..., budget_s=None)` - optional deadline; past it the
  search returns [] without calling ANY provider (cheap or expensive); default None keeps legacy
  behaviour for other callers. `orchestrator/deep_research.py`: `SEARCH_BUDGET_S = 25` shared
  across ALL quick_search attempts - rewords skipped and the loop exited once spent, with the
  honest "couldn't find a source" reply.
- **Deliberately NOT done**: the per-page verdict / reword LLM calls inside the budget are NOT
  skipped or stubbed - they are the answer-verification machinery; removing them would trade
  correctness for speed (i.e. weaken the product to flatter the benchmark).
- **Regression tests**: `test_cascade_skips_everything_when_budget_already_spent` (exhausted
  budget -> zero provider calls, no browser launch) and `test_cascade_without_budget_reaches_
  browser_fallback` (no budget -> Playwright fallback still reached; the healthy path is intact).
- **Result** (measured, not assumed): with BingLite healthy the cascade costs 2.8 s on a gibberish
  query - today's 130 s failure row is dominated by the per-page verdict + reword LLM calls across
  3 attempts (kept intact on purpose), so FIX-4 does NOT shrink today's suite runtime (re-measured:
  unchanged). What FIX-4 removes is the WORST-CASE path: when providers hang rather than fail fast
  (dead SearXNG connect + browser launch + timeouts stack to ~40-60 s per cascade by worst-case
  arithmetic), the cascade and the whole search phase are now hard-bounded instead of unbounded.
  web_search runs with harness `--timeout 300` (config, as with owner/FIX-1).

---

## FIX-5 (2026-09-30) - `apps`: generated happy row was a status QUESTION the skill must decline

- **Found by**: `tests/run_skill.py apps --runs 1` -> red: `test_happy_path` asserted
  `run('is notepad running')` returns a reply, but apps.run() honestly returns None (it only handles
  VERB-shaped "open/launch X" commands) - the assertion fired on correct behaviour.
- **Root cause (2 lines)**: `tools/gen_skill_tests.py` preset the apps happy row from a
  status-question phrasing, which no APP-CONTROL command skill may answer. Worse, the naive fix
  ("open notepad") would REALLY LAUNCH Notepad: `desktop/computer_use.launch_app` has no
  ISHTI_TEST_MODE guard and the dispatcher T2 gate does not apply to direct `run()` calls.
- **Fix**: preset changed to `open ishti-nonexistent-app-xyz` - a VERB-matching request for an app
  that does not exist: exercises the full path (VERB -> launch_app -> focus_window) and returns the
  honest "Couldn't open ..." reply through run() AND the real dispatcher, with zero side effects
  (launch_app: shutil.which miss -> error dict, no subprocess/startfile - verified live before
  regenerating; focus_window only scans window titles and nothing matches).
- **Underlying gap recorded, NOT fixed here**: NEEDS.md **N21** - computer_use's real actions
  (launch/startfile/focus) have no ISHTI_TEST_MODE guard, so T2 app-control is only safe at the
  dispatcher gate; the guard belongs in the agent like the T2 terminal-step block. Owner decision
  where to put it (agent-level vs dispatcher-level).
- **Regression**: the generator now emits the side-effect-free row for every future regeneration
  (`python tools/gen_skill_tests.py` diff: tests/skills/apps/test_apps.py only). The apps sign-off
  requires 3 consecutive harness greens on the corrected rows.
- **Not cheating**: the old row asserted a contract no skill can satisfy (decline); the new row is
  strictly harder - a real APP_CONTROL command whose reply must be produced. No assertion weakened.
