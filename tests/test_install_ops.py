"""WP-COUCOU acceptance: install operations (matrix row COU-15).

T-C15, scratch files only:
  - diff/plan_write: identical content is "no change"; changed content shows a unified diff
  - apply_write: backup first, then write; confirm(diff) can veto (file untouched); writing
    a missing file needs no backup
  - merge_entries: absent keys added, ours updated, foreign entries and nested containers
    never touched (reported skipped)
  - scoped_uninstall: removes exactly the entries tagged as ours; foreign entries and plain
    values survive; a second uninstall is a no-op
  - the module's own __main__ self-check stays green

Run: venv\\Scripts\\python -m pytest tests/test_install_ops.py -q
"""
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from orchestrator import install_ops as io  # noqa: E402


def test_diff_and_plan_write(tmp_path):
    cfg = tmp_path / "hooks.json"
    cfg.write_text(json.dumps({"a": 1}), encoding="utf-8")
    same = io.plan_write(str(cfg), cfg.read_text(encoding="utf-8"))
    assert same["ok"] and same["changed"] is False and same["diff"] == ""
    changed = io.plan_write(str(cfg), json.dumps({"a": 2}))
    assert changed["changed"] is True
    assert "-" in changed["diff"] and "+" in changed["diff"] and "hooks.json" in changed["diff"]
    assert changed["backup"], "an existing file would be backed up"
    missing = io.plan_write(str(tmp_path / "new.json"), "{}")
    assert missing["changed"] and missing["backup"] == "", "nothing to back up yet"


def test_apply_write_backs_up_and_honors_confirm(tmp_path):
    cfg = tmp_path / "config.json"
    cfg.write_text("OLD", encoding="utf-8")
    bk = str(tmp_path / "backups")
    veto = io.apply_write(str(cfg), "NEW", confirm=lambda d: False, backup_dir=bk)
    assert veto["vetoed"] is True and veto["written"] is False
    assert cfg.read_text(encoding="utf-8") == "OLD"
    assert not os.path.isdir(bk) or not os.listdir(bk), "a vetoed write creates no backup"
    seen = []
    ok = io.apply_write(str(cfg), "NEW", confirm=lambda d: seen.append(d) or True, backup_dir=bk)
    assert ok["written"] is True and seen and "+NEW" in seen[0]
    assert open(ok["backup"], encoding="utf-8").read() == "OLD", "backup holds the old content"
    assert cfg.read_text(encoding="utf-8") == "NEW"
    again = io.apply_write(str(cfg), "NEW", backup_dir=bk)
    assert again["changed"] is False and again["written"] is False
    born = io.apply_write(str(tmp_path / "fresh.json"), "{}", backup_dir=bk)
    assert born["written"] is True and born["backup"] == ""


def test_merge_entries_never_touches_foreign_or_nested():
    existing = {"nested": {"user-hook": {"command": "u"}},
                "old-hook": {"command": "old", "_owner": "ishti"},
                "foreign-hook": {"command": "u", "_owner": "user"},
                "scalar": 1}
    merged, report = io.merge_entries(existing, {"nested": {"hijack": 1},
                                                 "old-hook": {"command": "new"},
                                                 "foreign-hook": {"command": "hijack"},
                                                 "fresh-hook": {"command": "ours"},
                                                 "scalar": 2})
    assert merged["nested"] == {"user-hook": {"command": "u"}}
    assert merged["foreign-hook"]["command"] == "u"
    assert merged["old-hook"]["command"] == "new" and merged["fresh-hook"]["_owner"] == "ishti"
    assert merged["scalar"] == 2
    assert report["added"] == ["fresh-hook"] and report["skipped"] == ["nested", "foreign-hook"]
    assert report["updated"] == ["old-hook", "scalar"]


def test_merge_entries_list_semantics_and_collisions():
    foreign = [{"id": "a", "command": "user", "_owner": "user"}]
    merged, report = io.merge_entries(foreign, [{"id": "a", "command": "hijack"},
                                               {"id": "b", "command": "ours"}])
    assert merged[0] == {"id": "a", "command": "user", "_owner": "user"}
    assert merged[1] == {"id": "b", "command": "ours", "_owner": "ishti"}
    assert report["skipped"] == ["a"] and report["added"] == ["b"]
    merged2, report2 = io.merge_entries(merged, [{"id": "b", "command": "ours v2"}])
    assert len(merged2) == 2 and merged2[1]["command"] == "ours v2" and report2["updated"] == ["b"]


def test_scoped_uninstall_removes_only_ours():
    rows = [{"id": "u1", "_owner": "user"}, {"id": "i1", "_owner": "ishti"},
            {"id": "plain"}, {"_owner": "ishti", "id": "i2"}]
    remaining, removed = io.scoped_uninstall(rows)
    assert removed == 2 and [r["id"] for r in remaining] == ["u1", "plain"]
    again, removed2 = io.scoped_uninstall(remaining)
    assert removed2 == 0 and again == remaining
    d = {"a": {"_owner": "ishti"}, "b": {"_owner": "user"}, "c": 1}
    left, n = io.scoped_uninstall(d)
    assert n == 1 and left == {"b": {"_owner": "user"}, "c": 1}


def test_junk_is_total(tmp_path):
    for junk in (None, 42, b"\xff\xfe", [], {}):
        assert io.merge_entries(junk, junk)[0] == junk
        assert io.scoped_uninstall(junk)[1] == 0
        assert isinstance(io.plan_write(junk, "x"), dict)
        assert isinstance(io.diff(junk, junk), str)
    # apply_write on unusable paths returns a dict and writes nothing
    for junk in (None, 42, str(tmp_path / "nope" / "f.json")):
        out = io.apply_write(junk, "x", backup_dir=str(tmp_path / "bk"))
        assert isinstance(out, dict) and out["ok"] in (True, False)
    assert io.load_json(str(tmp_path / "missing.json"), default={"d": 1}) == {"d": 1}


def test_selfcheck_still_passes():
    mod = os.path.join(ROOT, "orchestrator", "install_ops.py")
    env = {**os.environ,
           "PYTHONPATH": ROOT + (os.pathsep + os.environ["PYTHONPATH"] if os.environ.get("PYTHONPATH") else "")}
    r = subprocess.run([sys.executable, mod], capture_output=True, text=True, timeout=120, cwd=ROOT, env=env)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "[self-check] install_ops OK" in r.stdout
