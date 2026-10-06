"""Repo-wide hygiene guards (offline, no models, no network). Each test fails if a class of defect comes back.

  1. no hard-coded "<drive>:\\ISHTI" checkout path in shipped code (the project must run from any folder/machine)
  2. no `shell=True` call anywhere in shipped code
  3. silent `except ...: pass` handlers can only go DOWN (ratchet against tests/fixtures/silent_except_baseline.json)
  4. shipped code does not read/write/cite the retired report documents (the docs may be deleted)
  5. every skill manifest declares an existing test_file and every skill.py carries a runnable self-check
  6. runtime gaps go to data/needs_runtime.md (ISHTI_NEEDS_LOG override), never to a repo document
  7. every subprocess call in shipped code has a timeout or is explicitly allow-listed as a long-lived child
"""
import ast
import json
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHIPPED_DIRS = ("ishti", "orchestrator", "desktop", "tools", "browser", "voice", "remote", "interface/web_ui")
SKIP_PARTS = ("/venv/", "/vendor/", "/node_modules/", "/__pycache__/", "/_drafts/", "/tools/flowise/", "/tools/obscura/",
              "/interface/app/", "/dist/", "/build/")
BASELINE = os.path.join(ROOT, "tests", "fixtures", "silent_except_baseline.json")


def _files(exts):
    for d in SHIPPED_DIRS:
        for base, _dirs, names in os.walk(os.path.join(ROOT, d)):
            norm = base.replace("\\", "/") + "/"
            if any(p in norm for p in SKIP_PARTS):
                continue
            for n in names:
                if n.endswith(exts):
                    yield os.path.join(base, n)


def _rel(p):
    return os.path.relpath(p, ROOT).replace("\\", "/")


def _tree(path):
    with open(path, encoding="utf-8") as f:
        return ast.parse(f.read())


# ---- 1. hard-coded checkout paths ---------------------------------------------------------------------------------
CHECKOUT_PATH = re.compile(r"(?<![A-Za-z])[A-Za-z]:[\\/]+ISHTI\b", re.I)
# ishti/config.json carries legacy absolute paths; ishti/__init__.py rebases them onto the running checkout in memory
# (_relocate, see test_config_json_paths_are_relocated), and tools/install.py migrates the file once at install time.
PATH_ALLOW = {"ishti/config.json", "ishti/__init__.py", "tools/install.py"}


def test_no_hardcoded_checkout_paths_in_shipped_code():
    hits = []
    for p in _files((".py", ".json", ".yaml", ".yml", ".html", ".bat", ".ps1", ".spec")):
        if _rel(p) in PATH_ALLOW:
            continue
        with open(p, encoding="utf-8", errors="replace") as f:
            for i, line in enumerate(f, 1):
                if CHECKOUT_PATH.search(line):
                    hits.append(f"{_rel(p)}:{i}")
    assert not hits, "hard-coded checkout path (derive it from __file__ / ishti.ROOT): " + ", ".join(hits[:20])


def test_config_json_paths_are_relocated(monkeypatch):
    import ishti
    with open(os.path.join(ROOT, "ishti", "config.json"), encoding="utf-8") as f:
        raw = json.load(f)
    elsewhere = os.path.join(os.path.abspath(os.sep), "somewhere", "else")
    monkeypatch.setattr(ishti, "ROOT", elsewhere)
    out = json.dumps(ishti._relocate(raw))
    assert not CHECKOUT_PATH.search(out.replace("\\\\", "\\")), "a legacy absolute path survived _relocate()"
    assert os.path.basename(elsewhere) in out


# ---- 2. shell=True ------------------------------------------------------------------------------------------------
def test_no_shell_true_in_shipped_code():
    bad = []
    for p in _files((".py",)):
        for node in ast.walk(_tree(p)):
            if isinstance(node, ast.Call):
                for kw in node.keywords:
                    if kw.arg == "shell" and isinstance(kw.value, ast.Constant) and kw.value.value is True:
                        bad.append(f"{_rel(p)}:{node.lineno}")
    assert not bad, "shell=True is a command-injection surface; pass an argv list: " + ", ".join(bad)


# ---- 3. silent except ratchet -------------------------------------------------------------------------------------
def silent_except_counts():
    counts = {}
    for p in _files((".py",)):
        n = sum(1 for node in ast.walk(_tree(p)) if isinstance(node, ast.ExceptHandler)
                and len(node.body) == 1 and isinstance(node.body[0], ast.Pass))
        if n:
            counts[_rel(p)] = n
    return counts


def test_silent_except_pass_can_only_go_down():
    with open(BASELINE, encoding="utf-8") as f:
        base = json.load(f)
    worse = {f: (base.get(f, 0), n) for f, n in silent_except_counts().items() if n > base.get(f, 0)}
    assert not worse, ("new silent `except ...: pass` (baseline, now) - log it with "
                       f"ishti.core.logutil.swallowed(where, exc) or handle it: {worse}")


def test_swallowed_logs_once_then_rate_limits(tmp_path):
    from ishti.core import logutil
    path = str(tmp_path / "log.jsonl")
    logutil.configure(log_path=path, enabled=True)
    logutil._swallowed_seen.clear()
    for _ in range(4):
        logutil.swallowed("hygiene.site", ValueError("boom"))
    lines = [json.loads(x) for x in open(path, encoding="utf-8").read().splitlines()]
    assert len(lines) == 1 and "hygiene.site" in lines[0]["msg"] and lines[0]["level"] == "WARNING", lines
    logutil.configure(log_path="", enabled=True)


# ---- 4. retired report documents ----------------------------------------------------------------------------------
RETIRED = ("FEATURE_CATALOG.md", "REPO_FEATURE_MATRIX.md", "IMPLEMENTATION_PLAN.md", "PROGRESS_repo_integration.md",
           "CUA_FEASIBILITY.md", "ISSUES.md", "NEEDS.md", "TEST_RESULTS.md", "SKILL_CATALOG.md", "FINAL_REPORT.md",
           "TEST_STATE.json", "md files/", "md files\\")
# ishti/dispatcher.py still tells the owner "I've logged it in NEEDS.md" (file owned by another change set).
RETIRED_ALLOW = set()


def _string_constants_outside_docstrings(tree):
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            first = node.body[0] if node.body else None
            if isinstance(first, ast.Expr) and isinstance(getattr(first, "value", None), ast.Constant):
                docstrings.add(id(first.value))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
            yield node


def test_shipped_code_does_not_depend_on_retired_report_documents():
    bad = []
    for p in _files((".py",)):
        if _rel(p) in RETIRED_ALLOW:
            continue
        for node in _string_constants_outside_docstrings(_tree(p)):
            hit = [d for d in RETIRED if d in node.value]
            if hit:
                bad.append(f"{_rel(p)}:{node.lineno} {hit}")
    assert not bad, "code/messages must not cite or read retired report documents: " + "; ".join(bad[:15])


# ---- 5. manifests: test_file + self-check -------------------------------------------------------------------------
def test_every_skill_declares_a_test_file_and_has_a_selfcheck():
    skills = os.path.join(ROOT, "ishti", "skills")
    no_test, no_check = [], []
    for name in sorted(os.listdir(skills)):
        man = os.path.join(skills, name, "manifest.json")
        if not os.path.isfile(man):
            continue
        with open(man, encoding="utf-8") as f:
            tf = json.load(f).get("test_file")
        if not tf or not os.path.exists(os.path.join(ROOT, tf)):
            no_test.append(name)
        code = os.path.join(skills, name, "skill.py")
        if os.path.isfile(code) and "__main__" not in open(code, encoding="utf-8").read():
            no_check.append(name)
    assert not no_test, f"manifest without a real test_file: {no_test}"
    assert not no_check, f"skill.py without a runnable self-check (if __name__ == '__main__'): {no_check}"


# ---- 6. the runtime needs log -------------------------------------------------------------------------------------
def test_log_need_writes_to_the_runtime_log_once(tmp_path, monkeypatch):
    from ishti import skills
    log = tmp_path / "sub" / "needs.md"          # directory does not exist yet: log_need must create it
    monkeypatch.setenv("ISHTI_NEEDS_LOG", str(log))
    assert skills.needs_path() == str(log)
    skills.log_need("gap", "demo", "do the thing", "no skill can do it")
    skills.log_need("gap", "demo", "do the thing again", "no skill can do it")   # same kind+skill+detail -> once
    body = log.read_text(encoding="utf-8")
    assert body.count("[gap] demo: no skill can do it") == 1 and 'asked: "do the thing"' in body
    monkeypatch.delenv("ISHTI_NEEDS_LOG")
    # Scratch mode (P9 isolation fix) moves the runtime log into the scratch dir - still the runtime
    # log, never a repo document; production (scratch off) keeps the real data/ path.
    from ishti import test_mode
    expected = os.path.join(ROOT, "data", "needs_runtime.md")
    if test_mode.scratch_dir():
        expected = os.path.join(test_mode.scratch_dir(), "needs_runtime.md")
    assert skills.needs_path() == expected


def test_meta_needs_op_reads_the_same_log(tmp_path, monkeypatch):
    from ishti import skills
    from ishti.skills.meta import skill as meta
    monkeypatch.setenv("ISHTI_NEEDS_LOG", str(tmp_path / "n.md"))
    assert meta.op_needs("what can't you do", {}) == "Nothing logged as missing."
    skills.log_need("gap", "demo", "x", "missing thing")
    assert "missing thing" in meta.op_needs("what can't you do", {})


# ---- 7. subprocess timeouts ---------------------------------------------------------------------------------------
_RUNNERS = {"run", "check_output", "check_call", "call"}
# Popen is a long-lived child by nature; the three blocking helpers below must always carry a timeout.
# Sites that legitimately wait forever (an interactive tool the owner is using) are listed explicitly.
# tools/install.py hands the console to its own phase-2 installer (interactive, as long as the install takes).
TIMEOUT_ALLOW = {"tools/install.py"}   # the whole file: it is the installer


def test_blocking_subprocess_calls_have_a_timeout():
    bad = []
    for p in _files((".py",)):
        rel = _rel(p)
        for node in ast.walk(_tree(p)):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in _RUNNERS
                    and isinstance(node.func.value, ast.Name) and node.func.value.id == "subprocess"):
                if not any(k.arg == "timeout" for k in node.keywords) and rel not in TIMEOUT_ALLOW \
                        and not any(k.arg is None for k in node.keywords):   # **kwargs may carry it
                    bad.append(f"{rel}:{node.lineno}")
    assert not bad, "subprocess call without timeout= (a hung child would hang ISHTI): " + ", ".join(bad[:30])


# ---- 8. invalid escape sequences (Issue 7 class) ------------------------------------------------------------------
def test_shipped_code_compiles_without_warnings():
    """Windows paths typed into non-raw strings (a drive path such as D:/ISHTI written with backslashes) produce DeprecationWarning/SyntaxWarning today and a hard
    SyntaxError in a future Python. Every shipped module (and every test) must compile clean."""
    import warnings
    bad = []
    paths = list(_files((".py",)))
    for base, _dirs, names in os.walk(os.path.join(ROOT, "tests")):
        paths += [os.path.join(base, n) for n in names if n.endswith(".py")]
    for p in paths:
        with open(p, encoding="utf-8") as f:
            src = f.read()
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            try:
                compile(src, p, "exec")
            except (SyntaxError, DeprecationWarning, SyntaxWarning) as exc:
                bad.append(f"{_rel(p)}: {type(exc).__name__} {str(exc)[:60]}")
    assert not bad, "; ".join(bad[:15])
