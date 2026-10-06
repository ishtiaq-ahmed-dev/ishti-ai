import json
from unittest.mock import Mock

from ishti import skills


def test_discovery_never_installs_missing_dependencies(tmp_path, monkeypatch):
    folder = tmp_path / "synthetic"
    folder.mkdir()
    (folder / "manifest.json").write_text(json.dumps({
        "name": "synthetic", "needs": {"python": ["audit_missing_package"]}
    }))
    installer = Mock(side_effect=AssertionError("discovery installed code"))
    monkeypatch.setattr(skills, "HERE", str(tmp_path))
    monkeypatch.setattr(skills, "install_missing", installer)
    loaded = skills.Skill("synthetic")
    assert loaded.status == "broken"
    assert "owner-approved install required" in loaded.problem
    installer.assert_not_called()


def test_installer_requires_explicit_approval(monkeypatch):
    monkeypatch.delenv("ISHTI_TEST_MODE", raising=False)
    runner = Mock()
    missing = ["audit_missing_package"]
    assert skills.install_missing("synthetic", missing, runner=runner) == missing
    assert skills.install_missing("synthetic", missing, runner=runner, approved="yes") == missing
    runner.assert_not_called()


def test_approved_install_is_blocked_in_test_mode(monkeypatch):
    monkeypatch.setenv("ISHTI_TEST_MODE", "1")
    runner = Mock()
    assert skills.install_missing("synthetic", ["audit_missing_package"],
                                  runner=runner, approved=True) == ["audit_missing_package"]
    runner.assert_not_called()


def test_invalid_packages_never_reach_an_approved_installer(monkeypatch):
    monkeypatch.delenv("ISHTI_TEST_MODE", raising=False)
    runner = Mock()
    bad = ["package;command"]
    assert skills.install_missing("synthetic", bad, runner=runner, approved=True) == bad
    runner.assert_not_called()
