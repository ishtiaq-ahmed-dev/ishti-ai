"""Regression for the `owner` doctor timeout bug (prompt 2, skill loop 2026-09-30).

Bug: `owner` skill's diagnostics path (orchestrator/doctor.py) ran live web searches and page fetches
with no overall cap, so on a slow/offline network the "run diagnostics" request hung >90 s (the QA
harness timed it out - see TEST_STATE.json owner=red). Fix: `check_all(budget_s=45)` skips the
remaining LIVE checks when the budget is spent; static checks always complete.
"""
import os
import sys
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def test_doctor_completes_within_budget():
    """The whole diagnostics run must finish well under a 60 s cap even with live checks failing."""
    from orchestrator.doctor import IshtiDoctor
    t0 = time.time()
    report = IshtiDoctor().check_all(budget_s=45)
    took = time.time() - t0
    assert isinstance(report, dict) and "errors" in report and "warnings" in report
    assert took < 60, f"diagnostics took {took:.1f}s - the hang is back"


def test_doctor_budget_respected_when_network_dead(monkeypatch):
    """With every live network call sleeping far past the budget, check_all still returns within it."""
    import orchestrator.doctor as doc

    def slow(*a, **k):
        time.sleep(600)          # would hang forever without the budget
        return {}

    monkeypatch.setattr("orchestrator.doctor.get_search_manager", slow)
    from orchestrator.doctor import IshtiDoctor
    t0 = time.time()
    report = IshtiDoctor().check_all(budget_s=5)
    took = time.time() - t0
    assert took < 30, f"budget not enforced: {took:.1f}s"
    assert "errors" in report


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
