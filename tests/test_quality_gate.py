"""WP-ECC acceptance: quality gate + review roles (matrix rows ECC-04, ECC-01).

T-ECC-04 / T-ECC-01, real ruff from the venv plus injected runners:
  - lint/format_check pass on clean code and fail with parsed findings on seeded issues
  - an injected runner proves the exact argv composition and output parsing offline
  - a missing linter is an explicit note ("lint skipped"), never a silent pass
  - code-reviewer role: skill.py without manifest.json blocks; lint/format problems block
  - security-reviewer role: eval() (dyn-code) blocks with a high finding; clean code passes
  - gate() returns one verdict; junk targets and missing paths never raise
  - the module's own __main__ self-check stays green

Run: venv\\Scripts\\python -m pytest tests/test_quality_gate.py -q
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from orchestrator import quality_gate as qg  # noqa: E402


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def test_lint_and_format_on_real_ruff(tmp_path):
    clean = _write(str(tmp_path / "clean.py"), "x = 1\n")
    dirty = _write(str(tmp_path / "dirty.py"), "import os\n\n\nx=1\n")
    r = qg.lint([clean])
    assert r["ok"] is True and r["files"] == 1 and r["notes"] == []
    assert qg.format_check([clean])["ok"] is True
    r = qg.lint([dirty])
    assert r["ok"] is False and r["findings"], "unused import must be caught"
    assert any(f["rule"] == "F401" and f["line"] == 1 for f in r["findings"]), r["findings"]


def test_injected_runner_proves_argv_and_parsing(tmp_path):
    dirty = _write(str(tmp_path / "dirty.py"), "import os\n")
    seen = []

    def fake(argv):
        seen.append(list(argv))
        return 1, f"{dirty}:3:8: F401 'os' imported but unused\nFound 1 error."

    r = qg.lint([dirty], runner=fake)
    assert seen and seen[0][1] == "check" and dirty in seen[0], seen
    assert "--output-format=json" in seen[0] and (seen[0][0] == qg.ruff_bin() or seen[0][0] == "ruff")
    assert r["ok"] is False and r["findings"] == [
        {"severity": "medium", "file": dirty, "line": 3, "rule": "F401",
         "detail": "'os' imported but unused"}], r["findings"]
    seen.clear()
    qg.format_check([dirty], runner=fake)
    assert seen[0][1] == "format" and "--check" in seen[0] and "--isolated" in seen[0], seen


def test_missing_linter_is_an_honest_note(tmp_path, monkeypatch):
    clean = _write(str(tmp_path / "clean.py"), "x = 1\n")
    monkeypatch.setattr(qg, "ruff_bin", lambda: None)
    r = qg.lint([clean])
    assert r["ok"] is True and r["notes"] == ["ruff not installed - lint skipped"]
    assert qg.format_check([clean])["notes"] == ["ruff not installed - format check skipped"]


def test_code_reviewer_blocks_missing_manifest_and_lint(tmp_path):
    skill = tmp_path / "my_skill"
    _write(str(skill / "skill.py"), "def run(text, session):\n    return None\n")
    rev = qg.review_code(str(skill))
    assert rev["role"] == "code-reviewer" and rev["verdict"] == "changes-requested"
    assert any(f["rule"] == "missing-manifest" for f in rev["findings"]), rev["findings"]
    _write(str(skill / "manifest.json"), '{"name": "my_skill", "status": "disabled"}\n')
    rev = qg.review_code(str(skill))
    assert rev["verdict"] == "pass", rev
    bad = _write(str(tmp_path / "bad.py"), "import os\n\n\nx=1\n")
    assert qg.review_code(bad)["verdict"] == "changes-requested"


def test_security_reviewer_blocks_dangerous_code(tmp_path):
    evil = _write(str(tmp_path / "evil.py"), "def run(src):\n    return eval(src)\n")
    rev = qg.review_security(evil)
    assert rev["role"] == "security-reviewer" and rev["verdict"] == "changes-requested"
    assert any(f["severity"] == "high" and f["rule"] == "dyn-code" for f in rev["findings"]), rev
    clean = _write(str(tmp_path / "clean.py"), "x = 1\n")
    assert qg.review_security(clean)["verdict"] == "pass"


def test_gate_returns_one_verdict_and_is_total(tmp_path):
    clean = _write(str(tmp_path / "pkg" / "clean.py"), "x = 1\n")
    assert qg.gate(str(tmp_path / "pkg"))["verdict"] == "pass"
    evil = _write(str(tmp_path / "pkg" / "evil.py"), "eval('1')\n")
    g = qg.gate(str(tmp_path / "pkg"))
    assert g["ok"] is False and g["verdict"] == "changes-requested" and len(g["reviews"]) == 2
    assert any(f["rule"] == "dyn-code" for f in g["findings"])
    # injected scanner: a high finding from anywhere blocks
    assert qg.gate(clean, scanner=lambda t: [{"severity": "high", "file": t, "line": 0,
                                              "rule": "seeded", "detail": "x", "fix": "y"}])["ok"] is False
    assert qg.gate(clean, scanner=lambda t: [])["verdict"] == "pass"
    for junk in (None, 42, os.path.join(str(tmp_path), "nope"), [None, 7, ""]):
        g = qg.gate(junk)
        assert isinstance(g, dict) and g["verdict"] in ("pass", "changes-requested"), junk


def test_skill_creator_gate_blocks_dangerous_draft(tmp_path, monkeypatch):
    """ECC-01 wiring: a generated skill with dynamic code never installs."""
    from ishti.skills.skill_creator import drafts
    monkeypatch.setattr(drafts, "FOLDER", str(tmp_path / "_drafts"))
    monkeypatch.setattr(drafts, "STATE", str(tmp_path / "state.json"))
    evil = drafts.new_draft("evil_draft", "demo")

    def _skill(path, body):
        with open(os.path.join(path, "skill.py"), "w", encoding="utf-8") as f:
            f.write(body)

    _skill(evil, "def run(text, session):\n    return eval(text)\n")
    rev = drafts.review_gate("evil_draft")
    assert rev["ok"] is False and any(f["rule"] == "dyn-code" for f in rev["high"]), rev
    assert drafts.package("evil_draft")["ok"] is True
    r = drafts.register("evil_draft")
    assert r["ok"] is False and "security review" in r["reason"], r
    clean = drafts.new_draft("clean_draft", "demo")
    _skill(clean, "def run(text, session):\n    return None\n")
    assert drafts.review_gate("clean_draft")["ok"] is True
    assert drafts.review_gate("no_such_draft")["ok"] is True, "a missing draft has no findings"


def test_selfcheck_still_passes():
    mod = os.path.join(ROOT, "orchestrator", "quality_gate.py")
    env = {**os.environ,
           "PYTHONPATH": ROOT + (os.pathsep + os.environ["PYTHONPATH"] if os.environ.get("PYTHONPATH") else "")}
    r = subprocess.run([sys.executable, mod], capture_output=True, text=True, timeout=180, cwd=ROOT, env=env)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "[self-check] quality_gate OK" in r.stdout
