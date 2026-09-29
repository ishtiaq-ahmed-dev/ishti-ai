"""Guard: pytest's collection phase must never import a standalone self-check script.

Regression test for the 2026-09-29 finding (ISSUES.md #30): the one-command offline suite documented in
pytest.ini (`venv\\Scripts\\python -m pytest`) did not finish. pytest imports every `test_*.py`, and nine
of them are standalone scripts whose module-level bodies do real work - `tests/test_stt_threads.py` runs
up to ~390 s of whisper sub-process self-checks, `tests/test_whatsapp_context.py` loads the 9 GB planner
into VRAM, and one collection run died with the 0xC0000409 fail-fast it is meant to catch.

Two invariants keep that from coming back:

  1. Every `tests/test_*.py` with no top-level `def test_*` function must be listed in
     `tests/conftest.py::collect_ignore` (otherwise pytest imports it and its body runs at collection).
  2. An ignored module that DOES define pytest tests must call every one of them from its
     `if __name__ == "__main__":` block - that is what keeps them in the suite, because the script runs
     through `tools/verify_all.py` instead of through pytest.

Offline, no services, milliseconds.
"""
import ast
import importlib.util
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS = os.path.join(ROOT, "tests")


def _conftest():
    spec = importlib.util.spec_from_file_location("_ishti_conftest", os.path.join(TESTS, "conftest.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _parse(path):
    with open(path, encoding="utf-8") as fh:
        return ast.parse(fh.read(), filename=path)


def _top_level_tests(tree):
    return [n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            and n.name.startswith("test_")]


def _main_block_calls(tree):
    """Names called inside `if __name__ == "__main__":`."""
    calls = set()
    for node in tree.body:
        if not isinstance(node, ast.If):
            continue
        src = ast.dump(node.test)
        if "__name__" not in src:
            continue
        for sub in ast.walk(node):
            if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name):
                calls.add(sub.func.id)
    return calls


def _pytest_files():
    return sorted(f for f in os.listdir(TESTS) if f.startswith("test_") and f.endswith(".py"))


def test_no_script_only_module_reaches_pytest_collection():
    ignored = set(_conftest().collect_ignore)
    offenders, scripts_seen = [], []
    for name in _pytest_files():
        path = os.path.join(TESTS, name)
        tree = _parse(path)
        if _top_level_tests(tree):
            continue                      # a real pytest module: pytest is supposed to import it
        scripts_seen.append(name)
        if name not in ignored:
            offenders.append(name)
    assert not offenders, (
        "these standalone self-check scripts would be executed by pytest's collection phase and must be "
        "added to tests/conftest.py::collect_ignore (or given `def test_*` functions): " + ", ".join(offenders))
    assert scripts_seen, "the detector found no script-only module - it has stopped detecting anything"


def test_ignored_modules_exist_and_their_tests_still_run():
    for name in _conftest().collect_ignore:
        path = os.path.join(TESTS, name)
        assert os.path.isfile(path), f"collect_ignore lists {name}, which does not exist"
        tree = _parse(path)
        defined = set(_top_level_tests(tree))
        called = _main_block_calls(tree)
        dropped = defined - called
        assert not dropped, (f"{name} defines pytest tests that nothing runs: {sorted(dropped)} - "
                             f"its `if __name__ == \"__main__\":` block must call them (tools/verify_all.py "
                             f"runs the file as a script)")
