"""Offline harness for the backend suite: fresh runs DB + a stubbed run executor.

No test here may hit the real dispatcher/research path (AGENTS.md: offline, synthetic data only).
The real executor path is covered by `python -m ishti.run_executor` (its __main__ self-check runs
one real dispatch) while this suite stubs the executor to stay fast and network-free.
"""
import pytest

from ishti import run_executor, runs


@pytest.fixture(autouse=True)
def _offline_runs(tmp_path, monkeypatch):
    monkeypatch.setattr(runs, "DB_PATH_OVERRIDE", str(tmp_path / "runs.db"))
    runs._conn = None
    runs.set_listener(None)

    def _instant(_run_id):
        """Stub: mark the run done immediately with a synthetic answer (no model, no network)."""
        run = runs.get_run(_run_id)
        if run and run["state"] not in ("done", "failed", "cancelled"):
            runs.finish_run(_run_id, "[stub answer]")

    monkeypatch.setattr(run_executor, "launch", _instant)
    yield
