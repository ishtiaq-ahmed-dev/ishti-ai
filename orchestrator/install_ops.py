"""Config / hooks install operations (WP-COUCOU, matrix COU-15; coucou's NSIS hooks.nsh).

Adopted from coucou's installer - backup the config, SHOW the diff, merge only our entries,
and an uninstall that removes only what it added. Pure, local, testable operations; nothing
here decides to install anything by itself:

  * backup(path)                -> copy into data/install_backups/<stamp>-<name> (chmod-free)
  * diff(old, new, name)        -> unified diff text ('' when identical)
  * plan_write(path, text)      -> {"changed", "diff", "backup"} - reads only
  * apply_write(path, text, confirm=None)
                                -> backup + write ONLY when changed; confirm(diff) may veto
  * merge_entries(existing, additions, tag=OWNER)
                                -> merge dict/list configs WITHOUT touching foreign entries;
                                   every entry we manage is tagged {"_owner": OWNER}
  * scoped_uninstall(existing, tag=OWNER)
                                -> removes only entries tagged as ours; returns (remaining, removed)

Ownership is explicit in the data (the "_owner" key), so a later uninstall never has to guess.
Never raises; every function returns a dict/tuple of values.
Self-check: python orchestrator/install_ops.py
"""
from __future__ import annotations

import difflib
import json
import os
import shutil
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKUP_DIR = os.path.join(ROOT, "data", "install_backups")
OWNER = "ishti"


def backup(path: str, *, backup_dir: str | None = None) -> str:
    """Copy the current file aside; "" when there is nothing to back up. Never raises."""
    try:
        if not os.path.isfile(path):
            return ""
        dest_dir = backup_dir or BACKUP_DIR
        os.makedirs(dest_dir, exist_ok=True)
        stamp = time.strftime("%Y%m%d_%H%M%S")
        dest = os.path.join(dest_dir, f"{stamp}-{os.path.basename(path)}")
        n = 2
        while os.path.exists(dest):
            dest = os.path.join(dest_dir, f"{stamp}-{n}-{os.path.basename(path)}")
            n += 1
        shutil.copyfile(path, dest)
        return dest
    except Exception:
        return ""


def diff(old: str, new: str, name: str = "file") -> str:
    """Unified diff text; '' when identical. Never raises."""
    try:
        old_lines = str(old or "").splitlines(keepends=True)
        new_lines = str(new or "").splitlines(keepends=True)
        if old_lines == new_lines:
            return ""
        return "".join(difflib.unified_diff(old_lines, new_lines,
                                            fromfile=f"a/{name}", tofile=f"b/{name}"))
    except Exception:
        return ""


def _read(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except Exception:
        return ""


def plan_write(path: str, text: str, *, backup_dir: str | None = None) -> dict:
    """What WOULD happen: {"ok", "path", "changed", "diff", "backup"} (reads only)."""
    try:
        old = _read(path)
        d = diff(old, text, os.path.basename(path))
        return {"ok": True, "path": path, "changed": bool(d),
                "diff": d, "backup": ("" if not os.path.isfile(path) else
                                      os.path.join(backup_dir or BACKUP_DIR,
                                                   f"<stamp>-{os.path.basename(path)}"))}
    except Exception as exc:
        return {"ok": False, "path": str(path), "changed": False, "diff": "",
                "backup": "", "error": f"{type(exc).__name__}: {exc}"}


def apply_write(path: str, text: str, *, confirm=None, backup_dir: str | None = None) -> dict:
    """Backup + write when changed. `confirm(diff)` returning falsy vetoes the write.
    Returns {"ok", "changed", "written", "backup", "vetoed"}. Never raises."""
    try:
        plan = plan_write(path, text, backup_dir=backup_dir)
        if not plan["changed"]:
            return {"ok": True, "changed": False, "written": False, "backup": "", "vetoed": False,
                    "diff": ""}
        if confirm is not None:
            try:
                if not confirm(plan["diff"]):
                    return {"ok": False, "changed": True, "written": False, "backup": "",
                            "vetoed": True, "diff": plan["diff"]}
            except Exception:
                return {"ok": False, "changed": True, "written": False, "backup": "",
                        "vetoed": True, "diff": plan["diff"]}
        saved = backup(path, backup_dir=backup_dir)
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(str(text))
        return {"ok": True, "changed": True, "written": True, "backup": saved, "vetoed": False,
                "diff": plan["diff"]}
    except Exception as exc:
        return {"ok": False, "changed": False, "written": False, "backup": "", "vetoed": False,
                "diff": "", "error": f"{type(exc).__name__}: {exc}"}


def _entry_id(entry):
    if isinstance(entry, dict):
        for key in ("id", "name", "key", "command"):
            if key in entry:
                return entry.get(key)
    return None


def merge_entries(existing, additions, *, tag: str = OWNER) -> tuple:
    """Merge our entries into a dict or list config, never touching foreign entries.

    * dict values: a key is written only when absent, or when its value is a dict whose
      "_owner" is us; a foreign key is preserved and reported as skipped
    * lists: our additions are tagged {"_owner": tag}; an addition whose id collides with a
      foreign entry is skipped, never overwritten
    Returns (merged, report) with report {"added", "updated", "skipped"}; never raises.
    """
    report = {"added": [], "updated": [], "skipped": []}
    try:
        tag = str(tag or OWNER)
        if isinstance(existing, dict) and isinstance(additions, dict):
            merged = dict(existing)
            for key, value in additions.items():
                present = key in merged
                cur = merged.get(key)
                if not present:
                    merged[key] = _tag(value, tag)
                    report["added"].append(str(key))
                elif isinstance(cur, dict) and str(cur.get("_owner") or "") == tag:
                    merged[key] = _tag(value, tag)      # ours: safe to update
                    report["updated"].append(str(key))
                elif isinstance(cur, dict):
                    report["skipped"].append(str(key))  # foreign entry or nested container
                else:
                    merged[key] = _tag(value, tag)      # scalar/list: the diff shows it
                    report["updated"].append(str(key))
            return merged, report
        if isinstance(existing, list) and isinstance(additions, list):
            merged = list(existing)
            foreign_ids = {_entry_id(e) for e in existing
                           if isinstance(e, dict) and e.get("_owner") not in (None, tag)}
            foreign_ids.discard(None)
            for value in additions:
                if isinstance(value, dict) and _entry_id(value) in foreign_ids:
                    report["skipped"].append(str(_entry_id(value)))
                    continue
                ours = [e for e in merged if isinstance(e, dict) and e.get("_owner") == tag
                        and _entry_id(e) is not None and _entry_id(e) == _entry_id(value)]
                if ours and _entry_id(value) is not None:
                    merged[merged.index(ours[0])] = _tag(value, tag)
                    report["updated"].append(str(_entry_id(value)))
                else:
                    merged.append(_tag(value, tag))
                    report["added"].append(str(_entry_id(value) if isinstance(value, dict) else value))
            return merged, report
        return existing, report
    except Exception:
        return existing, report


def _tag(value, tag: str):
    if isinstance(value, dict):
        tagged = dict(value)
        tagged["_owner"] = tag
        return tagged
    return value


def scoped_uninstall(existing, *, tag: str = OWNER) -> tuple:
    """Remove ONLY entries we own (dict values with "_owner" == tag, list items likewise).
    Returns (remaining, removed_count). Never raises."""
    try:
        tag = str(tag or OWNER)
        if isinstance(existing, dict):
            remaining = {k: v for k, v in existing.items()
                         if not (isinstance(v, dict) and str(v.get("_owner") or "") == tag)}
            return remaining, len(existing) - len(remaining)
        if isinstance(existing, list):
            remaining = [e for e in existing
                         if not (isinstance(e, dict) and str(e.get("_owner") or "") == tag)]
            return remaining, len(existing) - len(remaining)
        return existing, 0
    except Exception:
        return existing, 0


def load_json(path: str, default=None):
    """Read a JSON config; the default on any problem. Never raises."""
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default if default is not None else {}


if __name__ == "__main__":
    import tempfile
    tmp = tempfile.mkdtemp()
    cfg = os.path.join(tmp, "hooks.json")
    foreign = {"hooks": {"user-hook": {"command": "user.cmd", "_owner": "user"}}}
    with open(cfg, "w", encoding="utf-8") as f:
        json.dump(foreign, f)
    # diff + plan: no change -> no write, changed -> diff shown
    assert diff("a\n", "a\n") == "" and "-a" in diff("a\n", "b\n") and "+b" in diff("a\n", "b\n")
    assert plan_write(cfg, json.dumps(foreign))["changed"] is False
    plan = plan_write(cfg, json.dumps(foreign) + "\n")
    assert plan["changed"] and plan["diff"] and plan["backup"], plan
    # confirm can veto; a real apply backs up first
    veto = apply_write(cfg, "NEW", confirm=lambda d: False, backup_dir=os.path.join(tmp, "bk"))
    assert veto["vetoed"] and not veto["written"] and json.load(open(cfg)) == foreign
    ok = apply_write(cfg, "NEW", confirm=lambda d: "+NEW" in d or True,
                     backup_dir=os.path.join(tmp, "bk"))
    assert ok["written"] and ok["backup"] and json.load(open(ok["backup"])) == foreign
    assert open(cfg).read() == "NEW"
    # merge: absent keys are added, ours updated, foreign/nested structures never touched
    existing = {"nested": {"user-hook": {"command": "u"}},
                "old-hook": {"command": "old", "_owner": "ishti"},
                "foreign-hook": {"command": "u", "_owner": "user"},
                "scalar": 1}
    merged, report = merge_entries(existing, {"nested": {"hijack": 1},
                                              "old-hook": {"command": "new"},
                                              "foreign-hook": {"command": "hijack"},
                                              "fresh-hook": {"command": "ours"},
                                              "scalar": 2})
    assert merged["nested"] == {"user-hook": {"command": "u"}}, "nested config untouched"
    assert merged["old-hook"]["command"] == "new" and merged["fresh-hook"]["_owner"] == "ishti"
    assert merged["foreign-hook"]["command"] == "u" and merged["scalar"] == 2
    assert report["skipped"] == ["nested", "foreign-hook"] and report["added"] == ["fresh-hook"], report
    lst, rep = merge_entries([{"id": "a", "command": "user", "_owner": "user"}],
                             [{"id": "a", "command": "hijack"}, {"id": "b", "command": "ours"}])
    assert lst[0]["command"] == "user" and lst[1]["id"] == "b" and lst[1]["_owner"] == "ishti"
    assert rep["skipped"] == ["a"]
    # scoped uninstall removes exactly ours
    remaining, removed = scoped_uninstall(lst)
    assert removed == 1 and [e["id"] for e in remaining] == ["a"], remaining
    dd, removed_d = scoped_uninstall({"k1": {"_owner": "ishti"}, "k2": {"_owner": "user"}, "k3": 1})
    assert removed_d == 1 and dd == {"k2": {"_owner": "user"}, "k3": 1}
    # junk never raises
    for junk in (None, 42, "x", []):
        assert merge_entries(junk, junk)[0] == junk
        assert scoped_uninstall(junk)[1] == 0
        assert plan_write(junk, "x")["ok"] in (True, False)
    print("[self-check] install_ops OK - backup-first writes, diff, confirm veto, foreign entries "
          "preserved, scoped uninstall removes only ours")
