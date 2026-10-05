import os
import subprocess
import sys
from pathlib import Path

from ishti import audit


def test_audit_scope_denial_does_not_deadlock_or_expand_write_scope(tmp_path):
    code = """
import sys
from ishti import audit, scope
audit.DB = sys.argv[1]
scope.ALWAYS_WRITABLE = []
scope._scopes['weather'] = {'write': [], 'net': [], 'apps': False}
scope.install()
with audit.context(skill='weather', initiator='instructed', session='synthetic'):
    audit.record('synthetic action')
    assert audit.current()['skill'] == 'weather'
    try:
        scope.check_write(sys.argv[1])
    except scope.ScopeError:
        pass
    else:
        raise AssertionError('Skill write scope was expanded')
rows = audit.query()
assert any(row[2] == 'weather' and row[3] == 'synthetic action' for row in rows)
assert any(row[2] == 'weather' and row[3].startswith('BLOCKED:') for row in rows)
"""
    result = subprocess.run(
        [sys.executable, "-c", code, str(tmp_path / "audit" / "audit.db")],
        cwd=Path(__file__).resolve().parents[1], env={**os.environ, "ISHTI_TEST_MODE": "1", "ISHTI_SCRATCH": "1"},
        capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, result.stderr


def test_recursive_database_failure_returns_and_restores_guard(monkeypatch):
    def failed_db():
        audit.record("recursive failure")
        raise OSError("Synthetic database failure")
    monkeypatch.setattr(audit, "_db", failed_db)
    audit.record("outer failure")
    assert not audit._local.recording
