"""Pytest collection config (Prompt 4 QA).

Most of `tests/` is a mix of two kinds of file:

  * real pytest modules - they define `def test_*` functions and are what `python -m pytest` runs;
  * STANDALONE self-check scripts - they assert at module level, end with a `[self-check] ...` line and
    are meant to be run directly (`venv\\Scripts\\python tests/test_x.py`), the way `tools/verify_all.py`
    runs them.

pytest *imports* every `test_*.py` it finds, so without this list the standalone scripts were executed
during the collection phase. That was measured on 2026-09-29 and broke the one-command suite that
pytest.ini documents:

  * the checks ran twice - once as an import side effect of pytest, once as a subprocess of
    `tools/verify_all.py`;
  * `tests/test_whatsapp_context.py` loaded the 9 GB planner into VRAM at import time, so a supposedly
    OFFLINE unit run needed Ollama and a GPU (`[models] LOAD: ishti-qwen2.5:14b` appeared in the
    collection output);
  * `tests/test_stt_threads.py` runs up to ~390 s of whisper self-checks in subprocesses at import time,
    and one collection run died with exit code 127 (= 0xC0000409, the very fail-fast that file exists to
    catch). `python -m pytest --collect-only` exceeded 180 s and then 300 s without finishing.

Standalone scripts are therefore ignored here and run by the harness that is built for them:

    python tools/verify_all.py --only test:      # every tests/*.py script, raw exit codes inspected

`tests/test_selfcheck_hygiene.py` fails if a new script-only module is added without being listed here
(so this cannot silently regress), and also fails if an ignored module defines pytest tests that its own
`__main__` block does not run (so nothing is quietly dropped from the suite).

tests/test_lifecycle.py is a special case: it calls sys.exit() at import time, which aborts the whole run
with INTERNALERROR. Its contract-style sibling tests/test_lifecycle_contract.py is a real pytest module
(e2e-marked) and still runs.
"""
collect_ignore = [
    # --- call sys.exit() / abort the run at import time ---------------------------------------------
    "test_lifecycle.py",
    # --- standalone self-check scripts (no `def test_*`; run by tools/verify_all.py) -----------------
    "test_browser_tabs.py",
    "test_client_isolation.py",
    "test_dispatcher_followups.py",
    "test_multitopic.py",
    "test_planner_messenger.py",
    "test_shutdown_cleanliness.py",
    "test_stt_threads.py",
    "test_vram_governor_pressure.py",
    # --- script-first with 3 pytest tests: its `__main__` block runs all three, and importing it
    #     loads the planner model and hardcodes the checkout path. Run it as a script, not as a module. --------
    "test_whatsapp_context.py",
]
