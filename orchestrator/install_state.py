"""Install-state drift detection + ownership-scoped repair (WP-ECC, matrix ECC-12).

From ECC's install-state schema / doctor / repair scripts, mapped onto ISHTI:

  * capture()  - record a baseline of the files ISHTI OWNS: every skill's manifest.json and
                 skill.py plus the known config files (sha256 each) -> data/install_state.json
  * drift()    - compare the tree to the baseline: missing / modified / extra (bounded, fast)
  * repair()   - manual self-heal, restricted by OWNERSHIP:
                   - a skill marked status=working whose skill.py is gone is switched to
                     status=disabled (a config write ISHTI owns) - the only auto-fix
                   - modified files are REPORTED, never reverted: the owner may have edited
                     them on purpose (this repo is the owner's checkout, not a sealed install)
                   - anything under data/ or the owner's folders is report-only, always
  * report()   - plain-language summary for the doctor

CLI: python -m orchestrator.install_state [capture|drift|repair]
Never raises; every function returns a dict.
"""
from __future__ import annotations

import hashlib
import json
import os
import time

try:  # best-effort failure log (never breaks the module if logging is unavailable)
    from ishti.core.logutil import swallowed
except Exception:  # pragma: no cover
    def swallowed(*_a, **_k):
        return None


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
V = 1
STATE_REL = os.path.join("data", "install_state.json")
# paths whose contents the install/upgrade process owns; repair may WRITE only under these
OWNED_PREFIXES = (os.path.join("ishti", "skills") + os.sep, os.path.join("ishti", "skills") + "/")
# paths that are always the owner's; repair is report-only there
OWNER_PREFIXES = ("data" + os.sep, "data/", "reports" + os.sep, "reports/")  # runtime data + generated reports
MAX_FILES = 3000


def _sha(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _tracked(root: str) -> dict:
    """rel path -> sha256 for the files install-state watches (bounded)."""
    out = {}
    try:
        skills = os.path.join(root, "ishti", "skills")
        if os.path.isdir(skills):
            for name in sorted(os.listdir(skills)):
                folder = os.path.join(skills, name)
                if not os.path.isdir(folder):
                    continue
                for fname in ("manifest.json", "skill.py"):
                    f = os.path.join(folder, fname)
                    if os.path.isfile(f):
                        rel = os.path.relpath(f, root)
                        out[rel] = _sha(f)
                        if len(out) >= MAX_FILES:
                            return out
        for rel in (os.path.join("ishti", "config.json"),
                    os.path.join("orchestrator", "config.json"), "config.json"):
            f = os.path.join(root, rel)
            if os.path.isfile(f):
                out[rel] = _sha(f)
    except Exception as _exc:
        swallowed('orchestrator/install_state.py:_tracked', _exc)
    return out


def _state_path(root: str) -> str:
    return os.path.join(root, STATE_REL)


def capture(root: str = ROOT, *, state_path: str | None = None) -> dict:
    """Record the baseline. Returns {"ok", "recorded", "path"} (never raises)."""
    try:
        files = _tracked(root)
        path = state_path or _state_path(root)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"v": V, "ts": time.time(), "root": os.path.abspath(root), "files": files},
                      f, indent=2)
        return {"ok": True, "recorded": len(files), "path": path}
    except Exception as exc:
        return {"ok": False, "recorded": 0, "path": "", "error": f"{type(exc).__name__}: {exc}"}


def _load(path: str):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and data.get("v") == V and isinstance(data.get("files"), dict):
            return data
    except Exception as _exc:
        swallowed('orchestrator/install_state.py:_load', _exc)
    return None


def drift(root: str = ROOT, *, state_path: str | None = None, saved=None) -> dict:
    """{"ok", "baseline": bool, "missing": [...], "modified": [...], "extra": [...], "checked"}"""
    try:
        state = saved if isinstance(saved, dict) else _load(state_path or _state_path(root))
        current = _tracked(root)
        if not state:
            return {"ok": True, "baseline": False, "missing": [], "modified": [],
                    "extra": [], "checked": len(current)}
        files = state.get("files") or {}
        missing = sorted(rel for rel in files if not os.path.isfile(os.path.join(root, rel)))
        modified = []
        for rel, digest in sorted(files.items()):
            p = os.path.join(root, rel)
            if os.path.isfile(p):
                try:
                    if _sha(p) != digest:
                        modified.append(rel)
                except Exception:
                    modified.append(rel)
        extra = sorted(rel for rel in current if rel not in files)
        return {"ok": True, "baseline": True, "missing": missing, "modified": modified,
                "extra": extra, "checked": len(files)}
    except Exception as exc:
        return {"ok": False, "baseline": False, "missing": [], "modified": [], "extra": [],
                "checked": 0, "error": f"{type(exc).__name__}: {exc}"}


def _owned(rel: str) -> bool:
    r = str(rel).replace("\\", "/")
    owner = any(r.startswith(p.replace("\\", "/")) for p in OWNER_PREFIXES)
    owned = any(r.startswith(p.replace("\\", "/")) for p in OWNED_PREFIXES)
    return owned and not owner


def repair(drift_result=None, root: str = ROOT, *, state_path: str | None = None,
           apply: bool = True) -> dict:
    """Manual self-heal. The ONLY write is switching a skill's manifest status from
    \"working\" to \"disabled\" when its skill.py is missing and the manifest is owned.
    Everything else is reported. Never raises."""
    d = drift_result if isinstance(drift_result, dict) else drift(root, state_path=state_path)
    actions = []
    try:
        for rel in d.get("missing") or []:
            if not _owned(rel):
                actions.append({"action": "report", "path": rel,
                                "why": "outside the install-owned tree - never touched"})
                continue
            if not str(rel).replace("\\", "/").endswith("/skill.py"):
                actions.append({"action": "report", "path": rel,
                                "why": "missing file is not a skill entrypoint"})
                continue
            man_rel = os.path.join(os.path.dirname(rel), "manifest.json")
            man_path = os.path.join(root, man_rel)
            try:
                with open(man_path, encoding="utf-8") as f:
                    man = json.load(f)
            except Exception:
                actions.append({"action": "report", "path": rel,
                                "why": "manifest unreadable - left for the owner"})
                continue
            if man.get("status") == "working":
                if apply:
                    man["status"] = "disabled"
                    with open(man_path, "w", encoding="utf-8") as f:
                        json.dump(man, f, indent=2)
                    actions.append({"action": "disabled", "path": man_rel,
                                    "why": "skill.py is gone - switched off instead of failing at load"})
                else:
                    actions.append({"action": "would-disable", "path": man_rel,
                                    "why": "dry run"})
            else:
                actions.append({"action": "report", "path": rel, "why": "already not working"})
        for rel in d.get("modified") or []:
            actions.append({"action": "report", "path": rel,
                            "why": ("owner-modified since the baseline - not reverted" if _owned(rel)
                                    else "outside the install-owned tree - never touched")})
        for rel in d.get("extra") or []:
            actions.append({"action": "note", "path": rel, "why": "new since the baseline (fine)"})
        changed = sum(1 for a in actions if a["action"] in ("disabled", "would-disable"))
        return {"ok": True, "applied": bool(apply and changed), "actions": actions, "changed": changed}
    except Exception as exc:
        return {"ok": False, "applied": False, "actions": actions,
                "changed": 0, "error": f"{type(exc).__name__}: {exc}"}


def report(d: dict | None = None) -> str:
    """Plain-language summary for the doctor / CLI."""
    d = d if isinstance(d, dict) else drift()
    try:
        if not d.get("baseline"):
            return ("No install baseline yet - run `python -m orchestrator.install_state capture` "
                    "to start drift detection.")
        bits = [f"{d.get('checked', 0)} tracked file(s)"]
        if d.get("missing"):
            bits.append(f"{len(d['missing'])} missing")
        if d.get("modified"):
            bits.append(f"{len(d['modified'])} modified")
        if d.get("extra"):
            bits.append(f"{len(d['extra'])} new")
        head = "Install state: " + ", ".join(bits) + "."
        if not (d.get("missing") or d.get("modified")):
            return head + " Owned surfaces match the baseline."
        r = repair(d, apply=False)
        fixes = [a["path"] for a in r["actions"] if a["action"] == "would-disable"]
        return head + (f" Repair would switch off: {', '.join(fixes)}." if fixes
                       else " Repair has nothing to auto-fix; see actions.")
    except Exception:
        return "Install state: could not be summarised."


if __name__ == "__main__":
    import sys
    cmd = (sys.argv[1] if len(sys.argv) > 1 else "drift").lower()
    if cmd == "capture":
        print(capture())
    elif cmd == "repair":
        print(repair())
    else:
        d = drift()
        print(report(d))
        print(json.dumps({k: d.get(k) for k in ("baseline", "missing", "modified", "extra", "checked")},
                         indent=2))
