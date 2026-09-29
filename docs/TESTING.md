# TESTING — how to run every test type (ISHTI)

## 1. The one-command offline suite (default)

```powershell
$env:PYTHONIOENCODING = "utf-8"
venv\Scripts\python -X utf8 -m pytest tests/ -q
```

- Runs everything that runs without live services. Live-backend contract tests
  (`tests/test_lifecycle_contract.py`, marker `e2e`) are **deselected by default**
  (`pytest.ini` addopts `-m "not e2e"`) — they need Ollama + the GPU and start a real
  backend on a spare port.
- `tests/conftest.py` excludes `tests/test_lifecycle.py` from **collection**: it is a
  standalone self-check script (it prints `[self-check] lifecycle OK …` and calls
  `sys.exit()` at import). Run it directly when needed:
  `venv\Scripts\python tests\test_lifecycle.py`

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

Routing eval (expect `329/329 correct, median 0.2 ms`), memory round-trip, one live skill,
one local-LLM answer, one compound planner request.

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
venv\Scripts\python -X utf8 -m ishti.agents
venv\Scripts\python -X utf8 -m ishti.flowise_client
venv\Scripts\python -X utf8 -m ishti.leads
venv\Scripts\python -X utf8 -m ishti.telemetry
venv\Scripts\python -X utf8 -m orchestrator.factcheck
venv\Scripts\python -X utf8 -m ishti.core.architectures
```

Each prints `[self-check] … OK` or fails loudly. The telemetry one also runs the eval set
(expect `eval 5/5`).

## 6. STT accuracy & latency

```powershell
venv\Scripts\python -X utf8 -m pytest tests/test_stt_accuracy.py -q -s
```

Latency budgets are device-aware: CUDA float16 → 3 s (English) / 6 s (Urdu); the documented
CPU int8 fallback (busy GPU) → bounded 15 s / 10 s. Accuracy asserts never relax.

## 7. Skill catalog regeneration

```powershell
venv\Scripts\python -X utf8 tools\gen_skill_catalog.py    # writes docs/SKILL_CATALOG.md
```

## 8. When to run heavy items

Browser-engine tests, wake-echo checks and anything loading whisper/LLM share the GPU with
whatever else is running (builds, the live server). On a busy box a *timing* flake is more
likely; rerun the single test before investigating. The 2026-09-29 verification pass had
exactly one such case (headless browser timed out under a concurrent PyInstaller build;
passed in 2.4 s once the build paused).
