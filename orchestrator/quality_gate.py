"""Pre-merge quality gate + review roles (WP-ECC, matrix ECC-04 + ECC-01).

From ECC's post-edit quality-gate hook and its reviewer roster, re-implemented for ISHTI as
deterministic, local checks - no cloud, no npm:

  * lint(targets)         - `ruff check` over the Python files in the target (venv's ruff.exe
                            is preferred; a missing linter is an explicit NOTE, never silent)
  * format_check(targets) - `ruff format --check`
  * review_code(target)   - the code-reviewer role: lint + format + structure (a folder with
                            skill.py must ship a manifest.json)
  * review_security(target) - the security-reviewer role: reuses the security_scan skill's
                            scan_code/scan_manifest rules; high findings block
  * gate(target)          - both roles -> {"verdict": "pass"|"changes-requested", findings}

Used before merging skill drafts (skill_creator) and from the dev/loop tooling. Everything
is injectable (`runner`, `scanner`) so tests prove the behavior offline; never raises.
Self-check: python orchestrator/quality_gate.py
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKIP_DIRS = frozenset({"__pycache__", ".git", ".venv", "venv", "vendor", "node_modules",
                       "dist", "build", ".mypy_cache", ".ruff_cache"})
MAX_FILES = 400
OUT_CAP = 4000
ISSUE_RE = re.compile(r"^(?P<file>.+?):(?P<line>\d+):(?:\d+:)?\s*(?P<code>[A-Z]+\d+)?\s*(?P<msg>.*)$")


def ruff_bin():
    """The venv's ruff.exe when present, else one on PATH, else None."""
    try:
        cand = os.path.join(os.path.dirname(sys.executable), "ruff.exe")
        if os.path.isfile(cand):
            return cand
        return shutil.which("ruff") or shutil.which("ruff.exe")
    except Exception:
        return None


def files_of(targets, exts: tuple = (".py",)) -> list:
    """Python files under the targets (bounded, skip lists applied, sorted). Total."""
    out = []
    try:
        if isinstance(targets, (str, os.PathLike)):
            targets = [targets]
        for t in targets if isinstance(targets, (list, tuple, set)) else []:
            p = os.path.abspath(os.fspath(t)) if isinstance(t, (str, os.PathLike)) else None
            if not p or not os.path.exists(p):
                continue
            if os.path.isfile(p):
                if p.lower().endswith(exts):
                    out.append(p)
                continue
            for dirpath, dirs, names in os.walk(p):
                dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
                for n in sorted(names):
                    if n.lower().endswith(exts):
                        out.append(os.path.join(dirpath, n))
                        if len(out) >= MAX_FILES:
                            return sorted(set(out))
    except Exception:
        pass
    return sorted(set(out))


def _default_runner(argv, timeout: float = 120.0):
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=timeout)
        return int(proc.returncode), (proc.stdout or "") + (proc.stderr or "")
    except Exception as exc:
        return 127, f"{type(exc).__name__}: {exc}"


def _issues(output: str) -> list:
    """ruff output -> findings (bounded). JSON first (modern ruff), then concise text
    (injected runners / older ruff); unparseable lines are simply skipped."""
    try:
        data = json.loads(output)
    except Exception:
        data = None
    if isinstance(data, list):
        out = []
        for item in data[:100]:
            if not isinstance(item, dict):
                continue
            loc = item.get("location") or {}
            out.append({"severity": "medium", "file": str(item.get("filename") or ""),
                        "line": int(loc.get("row") or 0),
                        "rule": str(item.get("code") or "lint"),
                        "detail": str(item.get("message") or "")[:300]})
        return out
    out, pending = [], None
    for raw in (output or "").splitlines():
        line = raw.strip()
        if not line or line.startswith(("warning:", "help:", "Found ", "error:", "|")):
            continue
        m = re.match(r"^-+>\s+(?P<file>.+?):(?P<line>\d+):(?:\d+)?\s*$", line)
        if m:
            code, msg = pending or ("lint", "")
            out.append({"severity": "medium", "file": m.group("file").strip(),
                        "line": int(m.group("line")), "rule": code, "detail": msg[:300]})
            pending = None
            continue
        m = re.match(r"^(?P<code>[A-Z]+\d+)\s*(?:\[\*\])?\s+(?P<msg>.+)$", line)
        if m:
            pending = (m.group("code"), m.group("msg").strip())
            continue
        m = ISSUE_RE.match(line)
        if m:
            out.append({"severity": "medium", "file": m.group("file"), "line": int(m.group("line")),
                        "rule": m.group("code") or "lint", "detail": m.group("msg").strip()[:300]})
        if len(out) >= 100:
            break
    return out


def lint(targets, *, runner=None) -> dict:
    """ruff check over the target's Python files."""
    files = files_of(targets)
    if not files:
        return {"ok": True, "notes": ["no Python files to lint"], "findings": [], "output": "", "files": 0}
    binary = ruff_bin()
    if runner is None and not binary:
        return {"ok": True, "notes": ["ruff not installed - lint skipped"], "findings": [],
                "output": "", "files": len(files)}
    code, output = (runner or _default_runner)([binary or "ruff", "check", "--output-format=json", *files])
    return {"ok": code == 0, "notes": [] if code == 0 else [f"ruff check exited {code}"],
            "findings": _issues(output), "output": output[-OUT_CAP:], "files": len(files)}


def format_check(targets, *, runner=None) -> dict:
    """ruff format --check over the target's Python files."""
    files = files_of(targets)
    if not files:
        return {"ok": True, "notes": ["no Python files to format-check"], "findings": [], "files": 0}
    binary = ruff_bin()
    if runner is None and not binary:
        return {"ok": True, "notes": ["ruff not installed - format check skipped"], "findings": [],
                "files": len(files)}
    code, output = (runner or _default_runner)([binary or "ruff", "format", "--check", *files])
    findings = [] if code == 0 else [{"severity": "low", "file": (files[0] if files else ""),
                                      "line": 0, "rule": "format",
                                      "detail": "file(s) would be reformatted by ruff format"}]
    return {"ok": code == 0, "notes": [] if code == 0 else ["ruff format --check failed"],
            "findings": findings, "output": output[-OUT_CAP:], "files": len(files)}


def _skill_folders(target) -> list:
    """Directories inside the target that contain skill.py (need a manifest.json)."""
    out = []
    try:
        paths = [target] if isinstance(target, (str, os.PathLike)) else list(target or [])
        for p in paths:
            p = os.path.abspath(os.fspath(p))
            if os.path.isfile(p):
                if os.path.basename(p).lower() == "skill.py":   # a skill file: its folder
                    out.append(os.path.dirname(p))
                continue
            if os.path.isdir(p):
                if os.path.isfile(os.path.join(p, "skill.py")):
                    out.append(p)
                for d in sorted(os.listdir(p)):
                    f = os.path.join(p, d)
                    if os.path.isdir(f) and os.path.isfile(os.path.join(f, "skill.py")):
                        out.append(f)
    except Exception:
        pass
    return out


def review_code(target, *, runner=None) -> dict:
    """The code-reviewer role: lint + format + skill structure."""
    lint_r = lint(target, runner=runner)
    fmt_r = format_check(target, runner=runner)
    findings = list(lint_r["findings"]) + list(fmt_r["findings"])
    for folder in _skill_folders(target):
        if not os.path.isfile(os.path.join(folder, "manifest.json")):
            findings.append({"severity": "medium", "file": folder, "line": 0,
                             "rule": "missing-manifest",
                             "detail": "skill.py without manifest.json - the skill cannot load",
                             "fix": "add a manifest.json before merging"})
    verdict = "changes-requested" if (not lint_r["ok"] or not fmt_r["ok"] or findings) else "pass"
    return {"role": "code-reviewer", "verdict": verdict, "findings": findings,
            "notes": lint_r["notes"] + fmt_r["notes"] + [f"{lint_r['files']} file(s) checked"]}


def _default_scanner(target) -> list:
    """security_scan rules over a file or folder (read-only)."""
    findings = []
    try:
        from ishti.skills.security_scan import skill as sec
        paths = [target] if isinstance(target, (str, os.PathLike)) else list(target or [])
        for p in paths:
            p = os.path.abspath(os.fspath(p))
            if os.path.isfile(p) and p.lower().endswith(".py"):
                findings += sec.scan_code(p, root=os.path.dirname(p))
            elif os.path.isfile(p) and p.lower().endswith(".json"):
                findings += sec.scan_manifest(p)
            elif os.path.isdir(p):
                for dirpath, dirs, names in os.walk(p):
                    dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
                    for n in sorted(names):
                        if n.endswith(".py"):
                            findings += sec.scan_code(os.path.join(dirpath, n), root=p)
                        elif n == "manifest.json":
                            findings += sec.scan_manifest(os.path.join(dirpath, n))
    except Exception as exc:
        return [{"severity": "low", "file": str(target), "line": 0, "rule": "scanner-unavailable",
                 "detail": f"security_scan unavailable ({type(exc).__name__})", "fix": "run security_scan"}]
    return findings


def review_security(target, *, scanner=None) -> dict:
    """The security-reviewer role: high findings block, mediums are notes."""
    findings = (scanner or _default_scanner)(target) or []
    highs = [f for f in findings if f.get("severity") == "high"]
    return {"role": "security-reviewer", "verdict": "changes-requested" if highs else "pass",
            "findings": findings,
            "notes": [f"{len(findings)} finding(s), {len(highs)} high"] if findings else ["no findings"]}


def gate(target, *, runner=None, scanner=None) -> dict:
    """Pre-merge gate: both review roles -> one verdict. Never raises."""
    try:
        code = review_code(target, runner=runner)
        security = review_security(target, scanner=scanner)
        verdict = "changes-requested" if "changes-requested" in (code["verdict"], security["verdict"]) else "pass"
        return {"ok": verdict == "pass", "verdict": verdict, "target": str(target),
                "reviews": [code, security], "findings": code["findings"] + security["findings"]}
    except Exception as exc:
        return {"ok": False, "verdict": "changes-requested", "target": str(target),
                "reviews": [], "findings": [{"severity": "high", "file": str(target), "line": 0,
                                             "rule": "gate-error",
                                             "detail": f"the gate itself failed ({type(exc).__name__})",
                                             "fix": "fix the gate before merging"}]}


if __name__ == "__main__":
    import tempfile
    tmp = tempfile.mkdtemp()
    clean = os.path.join(tmp, "clean.py")
    with open(clean, "w", encoding="utf-8") as f:
        f.write("x = 1\n")
    r = lint([clean])
    assert r["ok"] and r["files"] == 1 and r["notes"] == [], r
    assert format_check([clean])["ok"]
    dirty = os.path.join(tmp, "dirty.py")
    with open(dirty, "w", encoding="utf-8") as f:
        f.write("import os\n\n\nx=1\n")
    r = lint([dirty])
    assert r["ok"] is False and r["findings"], r
    assert any(f["rule"] == "F401" for f in r["findings"]), r["findings"]
    # a fake runner is used verbatim (argv visible) and findings parse from its output
    seen = []
    def fake(argv):
        seen.append(argv)
        return 1, f"{dirty}:1:8: F401 'os' imported but unused"
    r = lint([dirty], runner=fake)
    assert r["ok"] is False and seen[0][1] == "check" and dirty in seen[0], seen
    assert "--output-format=json" in seen[0], "modern ruff output is requested for parsing"
    assert r["findings"][0]["rule"] == "F401" and r["findings"][0]["line"] == 1
    # security reviewer: eval() is a high finding; a clean file is not
    evil = os.path.join(tmp, "evil.py")
    with open(evil, "w", encoding="utf-8") as f:
        f.write("def run(src):\n    return eval(src)\n")
    sec = review_security(evil)
    assert sec["verdict"] == "changes-requested" and any(f["rule"] == "dyn-code" for f in sec["findings"]), sec
    assert review_security(clean)["verdict"] == "pass"
    # structure: skill.py without a manifest is a finding
    skill = os.path.join(tmp, "my_skill")
    os.makedirs(skill)
    with open(os.path.join(skill, "skill.py"), "w", encoding="utf-8") as f:
        f.write("def run(text, session):\n    return None\n")
    code_rev = review_code(skill)
    assert code_rev["verdict"] == "changes-requested" and any(
        f["rule"] == "missing-manifest" for f in code_rev["findings"])
    assert gate(skill)["verdict"] == "changes-requested"
    assert gate(clean)["verdict"] == "pass"
    # junk targets never raise; missing linter is an honest note
    saved = globals()["ruff_bin"]
    globals()["ruff_bin"] = lambda: None
    try:
        assert lint([clean])["notes"] == ["ruff not installed - lint skipped"]
    finally:
        globals()["ruff_bin"] = saved
    for junk in (None, 42, os.path.join(tmp, "nope"), [None, 7]):
        g = gate(junk)
        assert isinstance(g, dict) and g["verdict"] in ("pass", "changes-requested")
    print("[self-check] quality_gate OK - ruff lint/format, code-reviewer structure checks, "
          "security-reviewer rules, one pre-merge verdict, injectable and total")
