"""Guard: every skill manifest must be self-consistent and point at a real test file.

Drift found 2026-10-02 (recheck pass): `ishti/skills/skill_creator/manifest.json` declared
`test_file: tests/test_skill_creator.py`, a file that was never created - the real suite is
`tests/skills/skill_creator/test_skill_creator_core.py`. Nothing caught it because the suite
harness loads manifests but never resolves their `test_file`.

Invariants (all 124 manifests at the time of writing):

  1. `test_file` (when present) exists on disk.
  2. `name` equals the skill's folder name.
  3. `path` points at that same folder.

Cheap and offline: it reads JSON plus `os.path.exists`, never imports skill code.
"""
import glob
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILLS = os.path.join(ROOT, "ishti", "skills")


def _manifests():
    return sorted(glob.glob(os.path.join(SKILLS, "*", "manifest.json")))


def _load(man):
    with open(man, encoding="utf-8") as fh:
        return json.load(fh)


def test_detector_still_finds_the_manifest_set():
    assert len(_manifests()) >= 120, "manifest detector broke - no manifests found"


def test_every_manifest_points_at_a_test_file_that_exists():
    missing = []
    for man in _manifests():
        tf = _load(man).get("test_file")
        if tf and not os.path.exists(os.path.join(ROOT, tf)):
            missing.append((os.path.relpath(man, ROOT).replace("\\", "/"), tf))
    assert not missing, f"manifests point at test files that do not exist: {missing}"


def test_name_and_path_match_the_skill_folder():
    bad = []
    for man in _manifests():
        folder = os.path.basename(os.path.dirname(man))
        data = _load(man)
        if data.get("name") != folder:
            bad.append((folder, "name", data.get("name")))
        declared = (data.get("path") or "").replace("\\", "/").rstrip("/")
        actual = os.path.relpath(os.path.dirname(man), ROOT).replace("\\", "/")
        if declared and declared != actual:
            bad.append((folder, "path", declared))
    assert not bad, f"manifest identity drift (folder, field, value): {bad}"
