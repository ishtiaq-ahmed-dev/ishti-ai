# TESTING — how to run every test type (ISHTI)

## 0. The evidence report: one command, one file

```powershell
venv\Scripts\python tools\evidence.py --markdown docs\EVIDENCE.md
```

`tools/evidence.py` is the entry point a stranger should start from. It runs the three sources
below — the curated offline set, the `tests/eval` suites, and the per-skill suites — and writes
one JSON report in which every number is either **measured** with a numerator and a denominator or
**unmeasured** with a stated reason. It stamps each result with the version and source hash this
revision produced (`ishti/version.py`), so a number is never detached from the code that made it.

- `--no-skills` is what CI runs: offline, no GPU, a few minutes.
- The per-skill suites need a quiet GPU; without `--force-skills` the tool waits, and refuses
  rather than reporting GpuBusy flakes as failures.
- `--markdown docs\EVIDENCE.md` refreshes the tracked summary. `reports/` is gitignored, so
  **copy a number you intend to publish into a tracked file** or it is one `git clean` from gone.
- `venv\Scripts\python tools\privacy_report.py` produces the sibling artifact for data egress:
  what leaves the machine, what is refused, and what the report does not cover.

## 1. The one-command offline suite (default)

```powershell
$env:PYTHONIOENCODING = "utf-8"
$env:ISHTI_TEST_MODE = "1"
$env:ISHTI_SCRATCH = "1"
$env:ISHTI_OFFLINE_EVAL = "1"
venv\Scripts\python -u -X utf8 -X faulthandler -m pytest tests/ -q --junitxml=reports/full-offline.xml
```

- Runs everything that runs without live services. Live-backend contract tests
  (`tests/test_lifecycle_contract.py`, marker `e2e`) are **deselected by default**
  (`pytest.ini` addopts `-m "not e2e"`) — they need Ollama + the GPU and start a real
  backend on a spare port.
- `tests/conftest.py` excludes `tests/test_lifecycle.py` from **collection**: it is a
  standalone self-check script (it prints `[self-check] lifecycle OK …` and calls
  `sys.exit()` at import). Run it directly when needed:
  `venv\Scripts\python tests\test_lifecycle.py`

### The per-skill suites (excluded from the suite above)

`pytest.ini`'s `norecursedirs` excludes `tests/skills` from collection, so the ~120 per-skill
suites run **only** when something drives them, through `tests/run_skill.py`:

```powershell
venv\Scripts\python tests\run_skill.py weather          # one
venv\Scripts\python tests\run_skill.py all --runs 1     # every tests/skills/<skill>/
venv\Scripts\python tests\run_skill.py all --dry        # what would run, run nothing
```

Each suite is `tests/skills/<skill>/`, matching `ishti/skills/<skill>/`. The runner sets
`ISHTI_TEST_MODE=1`, enforces a per-test timeout, samples VRAM before and after, and reads the
audit log for the run window — a real T2 side effect during a nominally isolated run is reported
as `SAFETY-FAIL`, not as a pass. It exits 3 rather than starting while the GPU is more than 60%
full, because a batch that fights a live instance produces GpuBusy failures that look like defects.
`--json --out <path> --summary <path>` writes the machine-readable form `tools/evidence.py` consumes.
An empty suite folder collects no tests and is scored `no-suite` — it leaves the denominator and
cannot inflate the pass rate.

### Per-test timeouts (2026-10-16)

The runner's default per-test wall is **150 s** (`--timeout`): live browser/LLM rows run 40-90 s
warm on this machine, and the old 90 s wall raced the MCP client's own 90 s call timeout with zero
margin (a wedged browser bridge killed the test a second before the client would have errored
gracefully). Suites whose rows are intrinsically slow (live fetch + LLM extraction, the contradictory
fetch row at 143-284 s, `meta`'s op_health at ~141 s first pass) carry a module-level
`pytestmark = pytest.mark.timeout(300)` — the marker overrides the CLI `--timeout`, and the comment
on each names the measured worst row. A red in one of those suites is a real regression, not a
budget artifact.

`tools/evidence.py` sets `ISHTI_OFFLINE_EVAL=1` for its core+eval sections and **must clear it
before the per-skill section**: with it still set, every live skill suite fails inside `vram.admit`
("GPU residency could not be verified") because the offline blocker refuses the loopback `/api/ps`
call — every skills number the tool had ever written (100-102/121) was that artifact, not the
product. After the fix the same command measures 121/121.

## 2. Live backend contract tests (explicit)

```powershell
$env:PYTHONIOENCODING = "utf-8"
venv\Scripts\python -X utf8 -m pytest tests/test_lifecycle_contract.py -m e2e -s
```

Starts `python -m ishti <port>`, waits for `/api/ready`, and checks every response, the
`/ws/events` stream and graceful shutdown against `docs/API_CONTRACT.md`.

## 3. Live core loop

```powershell
venv\Scripts\python -X utf8 -m ishti.check
```

Routing eval over the labelled set in `ishti/check.py` (331 requests on 2026-10-16, bar 85% — the router is
`ishti/tool_routing.py`, which makes no model call, and the eval prints its own count, median time and
accuracy, so read those rather than this number: the list grows in batches and any count written down here
goes stale, as 120 and 212 both did), memory
round-trip, one live skill, one local-LLM answer, one compound request. The last four need Ollama;
the routing eval does not.

Two loop expectations still need a live run. `ishti/check.py`
now *reports* drift in its stored loop labels instead of asserting them — it prints how many turns
still match the list and flags each one that does not. The `plan` expectation this paragraph used to
explain has been replaced by `brain`: a continued workflow is routed as
`{"skill": "brain", "source": "workflow"}` (`ishti/dispatcher.py:1074`), which is the only producer of
that turn. `planners` is a different skill (flashcards, trip planning), not a rename of this path. What
is asserted is the smoke invariant: every turn returns a non-empty answer. That is weaker than the
per-turn assertions it replaced — nothing now checks that the memory, weather or LLM turns still answer
what they are supposed to — and re-baselining the labels from a live run, then restoring an assertion on
at least the `memory` turns, is still outstanding.
Separately, `recency_tiebreak`'s unit assertion exercises a decision source the live router no longer
emits; it is kept deliberately, because it pins the function's contract rather than a live path.

## 4. Live-stack acceptance scenarios (no mocks)

```powershell
venv\Scripts\python -X utf8 orchestrator\test_e2e.py            # all (A–L)
venv\Scripts\python -X utf8 orchestrator\test_e2e.py A B C D E G H J K L
```

Real Ollama + real internet; memory writes go to a scratch copy. `F` (visible browser) and
`I` (audible TTS) touch the real desktop — run those alone when the owner is at the machine.

## 5. Module self-checks

```powershell
venv\Scripts\python -X utf8 -m ishti.skills.registry
venv\Scripts\python -X utf8 -m ishti.resilience
venv\Scripts\python -X utf8 -m ishti.taskmgr
venv\Scripts\python -X utf8 -m ishti.leads
venv\Scripts\python -X utf8 -m ishti.telemetry
venv\Scripts\python -X utf8 -m orchestrator.factcheck
```

Each prints `[self-check] … OK` or fails loudly. The telemetry one also runs the eval set
(expect `eval 5/5`).

## 6. STT accuracy & latency

```powershell
venv\Scripts\python -X utf8 -m pytest tests/test_stt_accuracy.py -q -s
```

Latency budgets are device-aware: CUDA float16 → 3 s (English) / 6 s (Urdu); the documented
CPU int8 fallback (busy GPU) → bounded 15 s / 10 s. Accuracy asserts never relax.

## 7. Current catalogue and portable guides

The dashboard Skills view and `GET /api/skills` use the loaded registry. Export portable instruction
guides with `python tools/ishti_mcp.py --export <new-empty-directory>`; see [interoperability](INTEROPERABILITY.md).

## 8. When to run heavy items

Browser-engine tests, wake-echo checks and anything loading whisper/LLM share the GPU with
whatever else is running (builds, the live server). On a busy box a *timing* flake is more
likely; rerun the single test before investigating. The 2026-09-29 verification pass had
exactly one such case (headless browser timed out under a concurrent PyInstaller build;
passed in 2.4 s once the build paused).

## International readiness regressions

Use `ISHTI_TEST_MODE=1` and `ISHTI_SCRATCH=1`. The full suite tests default per-process scratch paths; leave `ISHTI_SCRATCH_DIR` unset for that suite. An explicit disposable path is suitable for standalone synthetic probes.
Run `tests/test_market_readiness.py` and `tests/test_workflow_continuation.py` for false completion,
provider scope, Unicode memory and restart/approval continuation checks. Tools/providers are synthetic.
These do not establish live account, clean-machine installer, voice or market readiness.

## Reliable evidence and CI

Require process exit status, nonempty JUnit XML, zero failures/errors, and stable source hashes.
Unbuffered stdout helps, but a missing summary is not itself a failed test when complete XML exists.
The task qualification runner also requires proof that `pytest.main()` returned and rejects a changed source tree.
The push/PR workflow is the fast API-profile gate: it runs `tools/evidence.py --no-skills`, which
covers the curated offline files and the `tests/eval` suites, and asserts a complete successful JUnit
record. `nightly.yml` has two jobs — the entire collected offline suite, and a separate, longer job
for the per-skill suites plus `tools/privacy_report.py`, uploading their evidence as an artifact.
The per-skill job is separate on purpose: it is slow and independent, and it cannot hide the
collected suite's result. Standalone scripts and e2e remain separate gates.
The remote workflow must actually run successfully before CI readiness can be claimed — the
workflows in this tree have not yet had a green run on GitHub.

For process/Python/system RAM attribution, explicitly enable `ISHTI_MEMORY_PROFILE=1` before
starting ISHTI. The bounded local `memory_growth.json` records sizes and allocator file/line counts,
never prompts or object contents. Profiling changes timing; compare cold start and warmed sustained
load separately. Use `tools/qa/memory_growth.py` only for its documented synthetic thread-churn test.


## Offline provider isolation

Set `$env:ISHTI_OFFLINE_EVAL="1"` for the collected offline pytest suite. The non-e2e
socket fixture rejects real remote/provider and configured Ollama connections, while
owned loopback test servers and SDK/HTTP doubles remain usable. CI and nightly enable it.
An offline memory test previously called live reconciliation and could load/switch models;
its response is now a deterministic fixture, with unavailable-model fallback tested directly.
The flag is a pytest evaluation boundary; it does not change the owner's runtime provider
or disable explicit inference profiles in `tools/load_ramp.py`. E2e-marked tests keep their
explicit live setup and are excluded from normal offline qualification.
