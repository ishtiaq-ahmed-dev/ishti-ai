"""WP-ECC acceptance: install-state drift + repair (matrix row ECC-12).

T-ECC-12, against a synthetic install tree (scratch root):
  - capture() records skill manifests + skill entrypoints + known config files with hashes
  - drift() reports missing / modified / extra against that baseline; no baseline is an
    honest {"baseline": False}, never an error
  - repair() auto-fixes EXACTLY ONE thing: a skill marked working whose skill.py is gone is
    switched to disabled; modified files are reported, never reverted
  - ownership: anything under data/ (the owner's tree) is report-only - repair never writes,
    never deletes, and the file content proves it
  - the doctor's install-state check passes without a baseline and warns on drift (offline)
  - junk input and missing paths never raise

Run: venv\\Scripts\\python -m pytest tests/test_install_state.py -q
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from orchestrator import install_state as ist  # noqa: E402


def _tree(tmp_path):
    """A tiny fake install: one skill, one broken-to-be skill, one config, owner data."""
    skills = tmp_path / "ishti" / "skills"
    (skills / "alpha").mkdir(parents=True)
    (skills / "alpha" / "manifest.json").write_text(
        json.dumps({"name": "alpha", "status": "working"}), encoding="utf-8")
    (skills / "alpha" / "skill.py").write_text("def run(text, session):\n    return None\n",
                                               encoding="utf-8")
    (skills / "broken").mkdir()
    (skills / "broken" / "manifest.json").write_text(
        json.dumps({"name": "broken", "status": "working"}), encoding="utf-8")
    (skills / "broken" / "skill.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "ishti" / "config.json").write_text("{}", encoding="utf-8")
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "owner_notes.txt").write_text("the owner's file", encoding="utf-8")
    return tmp_path


def _state(tmp_path):
    return str(tmp_path / "data" / "install_state.json")


def test_capture_and_clean_drift(tmp_path):
    root = _tree(tmp_path)
    c = ist.capture(str(root), state_path=_state(root))
    assert c["ok"] and c["recorded"] == 5, c     # 2 manifests + 2 skill.py + config.json
    d = ist.drift(str(root), state_path=_state(root))
    assert d["ok"] and d["baseline"] is True
    assert d["missing"] == [] and d["modified"] == [] and d["extra"] == []
    assert "match" in ist.report(d)


def test_no_baseline_is_honest_not_an_error(tmp_path):
    root = _tree(tmp_path)
    d = ist.drift(str(root), state_path=_state(root))
    assert d["ok"] and d["baseline"] is False and d["missing"] == []
    assert "capture" in ist.report(d)


def test_drift_reports_missing_modified_extra(tmp_path):
    root = _tree(tmp_path)
    ist.capture(str(root), state_path=_state(root))
    (root / "ishti" / "skills" / "alpha" / "skill.py").write_text("x = 2\n", encoding="utf-8")
    (root / "ishti" / "skills" / "broken" / "skill.py").unlink()
    (root / "ishti" / "skills" / "brand_new").mkdir()
    (root / "ishti" / "skills" / "brand_new" / "manifest.json").write_text("{}", encoding="utf-8")
    d = ist.drift(str(root), state_path=_state(root))
    assert os.path.join("ishti", "skills", "alpha", "skill.py") in d["modified"]
    assert os.path.join("ishti", "skills", "broken", "skill.py") in d["missing"]
    assert os.path.join("ishti", "skills", "brand_new", "manifest.json") in d["extra"]


def test_repair_disables_only_the_broken_working_skill(tmp_path):
    root = _tree(tmp_path)
    ist.capture(str(root), state_path=_state(root))
    skill = root / "ishti" / "skills" / "alpha" / "skill.py"
    skill.write_text("x = 2\n", encoding="utf-8")               # owner-modified
    (root / "ishti" / "skills" / "broken" / "skill.py").unlink()  # entrypoint gone
    r = ist.repair(root=str(root), state_path=_state(root))
    assert r["ok"] and r["changed"] == 1, r
    disabled = [a for a in r["actions"] if a["action"] == "disabled"]
    assert disabled and disabled[0]["path"].endswith(os.path.join("broken", "manifest.json"))
    man = json.loads((root / "ishti" / "skills" / "broken" / "manifest.json").read_text(encoding="utf-8"))
    assert man["status"] == "disabled", "the broken skill is switched off, not deleted"
    # the modified (owner-edited) skill.py is reported, never reverted
    assert skill.read_text(encoding="utf-8") == "x = 2\n"
    reported = [a for a in r["actions"] if a["action"] == "report" and "alpha" in a["path"]]
    assert reported and "not reverted" in reported[0]["why"]
    # dry run changes nothing
    (root / "ishti" / "skills" / "broken2").mkdir()
    (root / "ishti" / "skills" / "broken2" / "manifest.json").write_text(
        json.dumps({"name": "broken2", "status": "working"}), encoding="utf-8")
    d2 = ist.drift(str(root), state_path=_state(root))
    assert not any("broken2" in rel for rel in d2["missing"]), \
        "a skill that never had skill.py is not baseline drift"
    dry = ist.repair({"missing": [os.path.join("ishti", "skills", "ghost", "skill.py")]},
                     root=str(root), apply=False)
    assert dry["changed"] == 0 and all(a["action"] != "disabled" for a in dry["actions"])


def test_ownership_never_touches_the_owners_tree(tmp_path):
    root = _tree(tmp_path)
    owner_file = root / "data" / "owner_notes.txt"
    before = owner_file.read_text(encoding="utf-8")
    r = ist.repair({"missing": [os.path.join("data", "owner_notes.txt")],
                    "modified": [os.path.join("data", "owner_notes.txt")]}, root=str(root))
    assert r["changed"] == 0
    assert owner_file.read_text(encoding="utf-8") == before
    assert all(a["action"] in ("report", "note") for a in r["actions"])
    assert all("never touched" in a["why"] for a in r["actions"] if "data" in a["path"])
    assert not os.path.exists(_state(root)), "repair never creates or deletes state files"


def test_repair_is_total_on_junk(tmp_path):
    for junk in (None, 42, [], {"missing": [None, 7]}, {"modified": "not-a-list"}):
        r = ist.repair(junk, root=str(tmp_path))
        assert isinstance(r, dict) and "ok" in r
    assert ist.drift(str(tmp_path), state_path=str(tmp_path / "nope.json"))["ok"] is True


def test_doctor_install_check_offline(tmp_path, monkeypatch, capsys):
    from orchestrator.doctor import IshtiDoctor
    doc = IshtiDoctor()
    monkeypatch.setattr(ist, "drift", lambda *a, **k: {"ok": True, "baseline": False,
                                                       "missing": [], "modified": [], "extra": [], "checked": 0})
    doc.check_install_state()
    assert doc.errors == [] and doc.warnings == []
    monkeypatch.setattr(ist, "drift", lambda *a, **k: {"ok": True, "baseline": True,
                                                       "missing": ["ishti/skills/x/skill.py"],
                                                       "modified": [], "extra": [], "checked": 3})
    monkeypatch.setattr(ist, "report", lambda d=None: "Install state: 1 missing.")
    doc.check_install_state()
    assert doc.warnings and "drift" in doc.warnings[0].lower()
