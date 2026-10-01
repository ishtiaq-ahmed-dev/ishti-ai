"""Draft lifecycle: everything happens in `ishti/skills/_drafts/<name>/` until the owner says yes.

Why that folder: `load_skills()` skips top-level names starting with `_` (`ishti/skills/__init__.py:180`),
so a draft is invisible to the router by construction - two independent guards (this AND `status:"draft"`).
`scope.py` was given a deliberately narrow exception so `skill_creator` may write ONLY into `_drafts`;
a finished draft is installed by `skillsmith.install_folder` (owner-gated), so a draft can never promote
itself into a live skill by writing files.

State lives in `data/skill_creator_state.json` (always-writable per `scope.ALWAYS_WRITABLE`): status,
consecutive green runs, failure count for auto-disable, and the version history used for rollback.
"""
from __future__ import annotations

import json
import os
import re
import time

from ishti import ROOT

FOLDER = os.path.join(ROOT, "ishti", "skills", "_drafts")     # module-level so tests can repoint it
BACKUPS = os.path.join(ROOT, "data", "skill_backups")
STATE = os.path.join(ROOT, "data", "skill_creator_state.json")
MAX_FILES = 12
MAX_BYTES = 256 * 1024
MAX_GREEN = 3              # consecutive green runs before a draft counts as tested
AUTO_DISABLE_AT = 3        # consecutive live failures before a registered skill is switched off
PROTECTED_SUFFIXES = (os.path.join("ishti", "scope.py"),)


def name_for(request) -> str:
    """A safe snake_case skill name from the request: <=30 chars, must start with a letter."""
    words = re.findall(r"[a-z][a-z0-9]+", str(request or "").lower())
    name = "_".join(words[:4])[:30].rstrip("_")
    if not name or not name[0].isalpha():
        name = "learned_" + re.sub(r"\W+", "_", str(request or "")[:20]).strip("_").lower()
    return name[:30]


def draft_dir(name) -> str:
    return os.path.join(FOLDER, name)


def exists(name) -> bool:
    return os.path.isfile(os.path.join(draft_dir(name), "manifest.json"))


def new_draft(name, request="") -> str:
    """Create a clean draft folder (one draft in flight per task is enforced by the caller's ask)."""
    folder = draft_dir(name)
    os.makedirs(folder, exist_ok=True)
    _write(os.path.join(folder, "manifest.json"), {
        "name": name, "version": "0.0.0", "description": request[:300],
        "route": request[:300], "needs": {"python": [], "models": [], "mcp": [], "apis": []},
        "priority": 45, "status": "draft", "tier": "T2", "laya": False,
        "scope": {"write": ["data"], "net": [], "apps": False},
        "category": "Development & Code", "author": "ISHTI skill_creator",
        "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source": "generated", "source_links": []})
    return folder


def _write(path, obj) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2)


def caps(name) -> dict:
    """File/size budget for a draft. `ok` is False past the cap - enforced, not advisory."""
    folder = draft_dir(name)
    files, total = [], 0
    for root, _dirs, fs in os.walk(folder):
        for f in fs:
            p = os.path.join(root, f)
            try:
                files.append(os.path.relpath(p, folder))
                total += os.path.getsize(p)
            except OSError:
                pass
    return {"ok": len(files) <= MAX_FILES and total <= MAX_BYTES, "files": len(files),
            "bytes": total, "max_files": MAX_FILES, "max_bytes": MAX_BYTES,
            "reason": "" if (len(files) <= MAX_FILES and total <= MAX_BYTES)
            else f"draft over budget: {len(files)} files / {total} bytes (max {MAX_FILES} / {MAX_BYTES})"}


def _state() -> dict:
    try:
        with open(STATE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save_state(st) -> None:
    _write(STATE, st)


def record(name, **kw) -> dict:
    """Merge `kw` into this draft's state row. Used for status, greens, failures and version history."""
    st = _state()
    row = st.setdefault(name, {"status": "draft", "consecutive_green": 0, "failures": 0,
                               "versions": [], "updated": 0})
    row.update(kw)
    row["updated"] = time.time()
    _save_state(st)
    return row


def state_of(name) -> dict:
    return _state().get(name) or {"status": "none", "consecutive_green": 0, "failures": 0, "versions": []}


def run_tests(name, *, runs=MAX_GREEN, script=None) -> dict:
    """Run the draft's own test `runs` times, requiring MAX_GREEN CONSECUTIVE greens.

    A failure resets the counter to zero (that is the point: 3 green in a row, not 3 green ever).
    Execution goes through sandbox.run_script, so a runaway draft is killed by the Job-Object caps.
    """
    from ishti.skills.skill_creator import sandbox
    folder = draft_dir(name)
    script = script or next((os.path.join(folder, f) for f in ("selftest.py", "skill.py")
                             if os.path.isfile(os.path.join(folder, f))), "")
    if not script:
        return {"ok": False, "green": 0, "reason": "no test script (selftest.py / skill.py) in the draft",
                "runs": []}
    results, green = [], 0
    for i in range(max(1, runs)):
        res = sandbox.run_script(script, timeout_s=120)
        results.append({"run": i + 1, "rc": res["rc"], "killed": res["killed"],
                        "hard_limits": res["hard_limits"],
                        "tail": ((res["stderr"] or res["stdout"]).strip().splitlines()[-1:]
                                 if (res["stderr"] or res["stdout"]).strip() else [])})
        green = green + 1 if (res["rc"] == 0 and not res["killed"]) else 0   # reset on failure
        if green >= MAX_GREEN:
            break
    ok = green >= MAX_GREEN
    record(name, consecutive_green=green, last_runs=results, status="tested" if ok else "testing")
    return {"ok": ok, "green": green, "runs": results,
            "reason": "" if ok else f"only {green} consecutive green run(s) out of {runs} attempts"}


def assert_no_self_modification(before, after) -> None:
    """A generated skill may never widen its own permission surface (RESEARCH.md R1: never let the
    agent raise its own bar). Raises ValueError if a tier or scope changed."""
    if not isinstance(before, dict) or not isinstance(after, dict):
        return
    for key in ("tier", "scope"):
        if before.get(key) != after.get(key):
            raise ValueError(f"a generated skill may not change its own {key}: "
                             f"{before.get(key)!r} -> {after.get(key)!r}")


def package(name) -> dict:
    """Promote a tested draft to a v0.1.0 package: status stays 'disabled' until the owner says yes."""
    folder = draft_dir(name)
    mpath = os.path.join(folder, "manifest.json")
    if not os.path.isfile(mpath):
        return {"ok": False, "reason": "no manifest in the draft"}
    limit = caps(name)
    if not limit["ok"]:
        return {"ok": False, "reason": limit["reason"]}
    with open(mpath, encoding="utf-8") as f:
        m = json.load(f)
    m.update({"name": name, "version": "0.1.0", "status": "disabled", "tier": m.get("tier", "T2"),
              "author": "ISHTI skill_creator",
              "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
              "source": "generated", "category": m.get("category") or "Development & Code",
              "test_file": m.get("test_file") or f"tests/skills/{name}/test_{name}.py"})
    m["rollback"] = {"previous_version": None, "backup": None,
                     "history": state_of(name).get("versions", [])}
    _write(mpath, m)
    record(name, status="disabled", version="0.1.0")
    return {"ok": True, "manifest": m, "path": mpath}



def register(name) -> dict:
    """Install a packaged draft into ishti/skills/ as DISABLED - via skillsmith, never a hand copy."""
    from ishti.skills.skillsmith import skill as skillsmith   # the MODULE inside the folder, not the folder
    folder = draft_dir(name)
    mpath = os.path.join(folder, "manifest.json")
    if not os.path.isfile(mpath):
        return {"ok": False, "reason": "draft has no manifest to register"}
    with open(mpath, encoding="utf-8") as f:
        m = json.load(f)
    if m.get("status") != "disabled":
        return {"ok": False, "reason": f"refusing to register with status {m.get('status')!r} "
                                       f"(a new skill must arrive DISABLED)"}
    if not os.path.isfile(os.path.join(folder, "skill.py")):
        return {"ok": False, "reason": "draft has no skill.py"}
    backup = _backup(name, m.get("version", "0.1.0"))
    out = skillsmith.install_folder(folder, m, "ISHTI skill_creator")
    src = os.path.join(ROOT, "ishti", "skills", name, "manifest.json")
    if not os.path.isfile(src):
        return {"ok": False, "reason": str(out)}
    record(name, status="disabled", installed=True, backup=backup)
    return {"ok": True, "message": str(out), "installed_to": src, "backup": backup,
            "status": "disabled", "note": "registered DISABLED - enabling needs the owner's explicit yes"}


def enable(name) -> dict:
    """Owner-approved activation (T2). Flips the manifest AND the Settings flag the dashboard reads."""
    src = os.path.join(ROOT, "ishti", "skills", name, "manifest.json")
    if not os.path.isfile(src):
        return {"ok": False, "reason": "that skill is not installed"}
    with open(src, encoding="utf-8") as f:
        m = json.load(f)
    m["status"] = "working"
    _write(src, m)
    try:
        from ishti.skills import registry
        registry.set_enabled(name, True)
        registry.reset_cache()
    except Exception as exc:
        print(f"[skill_creator] could not clear the Settings flag ({type(exc).__name__}: {exc})")
    record(name, status="working", enabled_at=time.time())
    return {"ok": True, "status": "working", "message": f"{name} is enabled"}


def disable(name, reason="") -> dict:
    """Switch a skill off (auto-disable after repeated failures, or the owner's command)."""
    src = os.path.join(ROOT, "ishti", "skills", name, "manifest.json")
    if not os.path.isfile(src):
        return {"ok": False, "reason": "that skill is not installed"}
    with open(src, encoding="utf-8") as f:
        m = json.load(f)
    m["status"] = "disabled"
    m["disabled_reason"] = reason[:200]
    _write(src, m)
    try:
        from ishti.skills import registry
        registry.set_enabled(name, False)
        registry.reset_cache()
    except Exception as exc:
        print(f"[skill_creator] could not persist the disable flag ({type(exc).__name__}: {exc})")
    record(name, status="disabled", disabled_reason=reason[:200])
    return {"ok": True, "status": "disabled", "reason": reason[:200]}


def note_failure(name, error="") -> dict:
    """One live failure recorded; at AUTO_DISABLE_AT consecutive failures the skill switches itself off."""
    failures = int(state_of(name).get("failures") or 0) + 1
    record(name, failures=failures, last_error=str(error)[:200], consecutive_green=0)
    if failures >= AUTO_DISABLE_AT:
        out = disable(name, f"auto-disabled after {failures} consecutive failures: {str(error)[:120]}")
        return {"failures": failures, "auto_disabled": out.get("ok", False), "reason": out.get("reason", "")}
    return {"failures": failures, "auto_disabled": False, "reason": ""}



def _backup(name, version) -> str:
    """Copy the installed skill to data/skill_backups/<name>-<version> BEFORE anything replaces it."""
    import shutil
    src = os.path.join(ROOT, "ishti", "skills", name)
    if not os.path.isdir(src):
        return ""
    dst = os.path.join(BACKUPS, f"{name}-{version}")
    shutil.rmtree(dst, ignore_errors=True)
    os.makedirs(BACKUPS, exist_ok=True)
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc"))
    return dst


def rollback(name) -> dict:
    """One command back to the last backed-up version (RESEARCH.md R1: versioning + rollback)."""
    import shutil
    row = state_of(name)
    src = os.path.join(ROOT, "ishti", "skills", name)
    backup = row.get("backup") or ""
    if not backup or not os.path.isdir(backup):
        cands = []
        if os.path.isdir(BACKUPS):
            cands = sorted((os.path.join(BACKUPS, d) for d in os.listdir(BACKUPS)
                            if d.startswith(name + "-")), key=os.path.getmtime, reverse=True)
        backup = cands[0] if cands else ""
    if not backup or not os.path.isdir(backup):
        return {"ok": False, "reason": "no backup exists for that skill yet"}
    if os.path.isdir(src):
        shutil.rmtree(src)
    shutil.copytree(backup, src, ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc"))
    record(name, status="rolled_back", restored_from=backup)
    return {"ok": True, "restored_from": backup, "message": f"{name} rolled back to {backup}"}


if __name__ == "__main__":
    import shutil
    import tempfile

    base = tempfile.mkdtemp(prefix="ishti_drafts_")
    FOLDER = os.path.join(base, "_drafts")          # repointed: the real skill tree is never touched
    STATE = os.path.join(base, "state.json")
    BACKUPS = os.path.join(base, "backups")

    # naming + the 12-file / 256 KB cap
    assert name_for("combine a sandbox folder of images into one PDF")[:3].isalpha()
    assert name_for("!!!").startswith("learned_"), name_for("!!!")
    folder = new_draft("demo_draft", "a demo")
    open(os.path.join(folder, "skill.py"), "w", encoding="utf-8").write("print(1)\n")
    assert caps("demo_draft")["ok"], caps("demo_draft")
    for i in range(MAX_FILES + 1):
        open(os.path.join(folder, f"f{i}.py"), "w", encoding="utf-8").write("x")
    assert caps("demo_draft")["ok"] is False, caps("demo_draft")

    # 3 CONSECUTIVE greens: a failure resets the counter to zero
    shutil.rmtree(folder, ignore_errors=True)
    folder = new_draft("green_demo", "demo")
    open(os.path.join(folder, "selftest.py"), "w", encoding="utf-8").write("assert True\nprint('ok')\n")
    r = run_tests("green_demo", runs=3)
    assert r["ok"] and r["green"] == 3, r
    flaky = os.path.join(folder, "flaky.py")
    open(flaky, "w", encoding="utf-8").write("import sys\nsys.exit(1)\n")
    r2 = run_tests("green_demo", runs=4, script=flaky)
    assert r2["ok"] is False and r2["green"] == 0, r2

    # a generated skill may not widen its own tier/scope
    assert_no_self_modification({"tier": "T0"}, {"tier": "T0"})
    try:
        assert_no_self_modification({"tier": "T0"}, {"tier": "T3"})
        raise SystemExit("self-modification was allowed!")
    except ValueError:
        pass

    # failure counting + honest failure when there is no backup
    record("flaky_skill", failures=0)
    out = {"failures": 0}
    for i in range(AUTO_DISABLE_AT):
        out = note_failure("flaky_skill", f"boom {i}")
    assert out["failures"] == AUTO_DISABLE_AT, out
    assert rollback("never_installed")["ok"] is False, rollback("never_installed")

    shutil.rmtree(base, ignore_errors=True)
    print("[self-check] skill_creator.drafts OK - naming, 12-file/256KB cap, 3 CONSECUTIVE greens with "
          "reset on failure, self-modification blocked, failure counting, honest rollback failure")

