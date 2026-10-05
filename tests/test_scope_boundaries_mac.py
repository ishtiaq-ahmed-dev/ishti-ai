"""Synthetic path-boundary regressions; no owner data or OS app control."""
import os
import subprocess
import sys
from pathlib import Path

import pytest

from ishti import scope


@pytest.fixture
def scoped_paths(tmp_path, monkeypatch):
    allowed, denied = tmp_path / "allowed", tmp_path / "denied"
    allowed.mkdir()
    denied.mkdir()
    monkeypatch.setattr(scope, "ALWAYS_WRITABLE", [])
    monkeypatch.setattr(scope, "PROTECTED", [str(denied)])
    monkeypatch.setattr(scope, "current", lambda: {"skill": "synthetic"})
    monkeypatch.setattr(scope, "record", lambda *_a, **_k: None)
    monkeypatch.setitem(scope._scopes, "synthetic", {"write": [str(allowed)]})
    return allowed, denied


def test_existing_and_new_leaf_symlink_escape_is_denied(scoped_paths):
    allowed, denied = scoped_paths
    (denied / "existing.txt").touch()
    link = allowed / "escape"
    try:
        link.symlink_to(denied, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"OS symlink permission unavailable: {exc}")
    for name in ("existing.txt", "new.txt"):
        with pytest.raises(scope.ScopeError):
            scope.check_write(link / name)
    scope.check_write(allowed / "new.txt")


@pytest.mark.parametrize("name,operands,positions", [
    ("remove", {"path": "denied"}, (0,)),
    ("unlink", {"path": "denied"}, (0,)),
    ("rmdir", {"path": "denied"}, (0,)),
    ("mkdir", {"path": "denied"}, (0,)),
    ("rmtree", {"path": "denied"}, (0,)),
    ("rename", {"src": "allowed", "dst": "denied"}, (0, 1)),
    ("replace", {"src": "denied", "dst": "allowed"}, (0, 1)),
    ("move", {"src": "allowed", "dst": "denied"}, (0, 1)),
    ("copyfile", {"src": "allowed", "dst": "denied"}, (1,)),
    ("copy2", {"src": "allowed", "dst": "denied"}, (1,)),
])
def test_keyword_operands_checked_before_mutation(scoped_paths, name, operands, positions):
    paths = dict(zip(("allowed", "denied"), scoped_paths))
    called = []
    wrapped = scope._wrap_path_fn(name, lambda **kw: called.append(kw), positions)
    with pytest.raises(scope.ScopeError):
        wrapped(**{k: paths[v] / "file" for k, v in operands.items()})
    assert not called


@pytest.mark.parametrize("key", ["dir_fd", "src_dir_fd", "dst_dir_fd"])
def test_descriptor_relative_mutations_fail_closed(scoped_paths, key):
    allowed, _ = scoped_paths
    calls = []
    wrapped = scope._wrap_path_fn("rename", lambda *a, **kw: calls.append((a, kw)), (0, 1))
    with pytest.raises(scope.ScopeError, match="descriptor-relative"):
        wrapped(allowed / "old", allowed / "new", **{key: 42})
    assert calls == []
    wrapped(src=allowed / "old", dst=allowed / "new", **{key: None})
    assert len(calls) == 1


def test_unscoped_descriptor_call_is_preserved(monkeypatch):
    monkeypatch.setattr(scope, "current", lambda: {})
    seen = []
    wrapped = scope._wrap_path_fn("remove", lambda **kw: seen.append(kw))
    wrapped(path="relative", dir_fd=42)
    assert seen == [{"path": "relative", "dir_fd": 42}]


def test_installed_hooks_protect_pathlib_and_keyword_delete(tmp_path):
    code = r'''
import sys
from pathlib import Path
from ishti import audit, scope
root = Path(sys.argv[1])
allowed, denied = root / "allowed", root / "denied"
allowed.mkdir(); denied.mkdir()
victim = denied / "victim.txt"
victim.write_text("unchanged")
scope.ALWAYS_WRITABLE = []
scope.PROTECTED = [str(denied)]
scope._scopes["synthetic"] = {"write": [str(allowed)]}
audit.DB = str(root / "audit" / "audit.db")
scope.install()
with audit.context(skill="synthetic"):
    (allowed / "ok.txt").write_text("permitted")
    for mutation in (lambda: victim.write_text("bad"), lambda: scope.os.remove(path=victim)):
        try:
            mutation()
        except scope.ScopeError:
            pass
        else:
            raise AssertionError("protected mutation accepted")
assert victim.read_text() == "unchanged"
assert (allowed / "ok.txt").read_text() == "permitted"
'''
    result = subprocess.run(
        [sys.executable, "-c", code, str(tmp_path)],
        cwd=Path(__file__).resolve().parents[1],
        env={**os.environ, "ISHTI_TEST_MODE": "1", "ISHTI_SCRATCH": "1"},
        capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, result.stderr
