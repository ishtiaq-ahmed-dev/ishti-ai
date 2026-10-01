"""Guard for SC-13: `from ishti.skills import <skill>` binds the FOLDER.

No skill folder has an `__init__.py`, so that import silently yields a namespace package whose
`.scan` / `.install_folder` / `.run` do not exist - and it only blows up later, at the attribute
access, far from the import. The module inside a skill must be named explicitly:
`from ishti.skills.<name> import skill`. Same class of guard as test_selfcheck_hygiene.py
(keeps collect_ignore honest) and test_desktop_bootstrap.py (keeps the exe stdlib-only): an AST
walk, so prose that *describes* the trap in a docstring or comment is not flagged.
"""
import ast
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILLS_DIR = os.path.join(ROOT, "ishti", "skills")
SKILL_NAMES = {d for d in os.listdir(SKILLS_DIR)
               if os.path.isdir(os.path.join(SKILLS_DIR, d)) and not d.startswith("_")}
SCAN_DIRS = ("ishti", "orchestrator", "interface", "desktop", "tools", "tests")


def _iter_py():
    for base in SCAN_DIRS:
        for root, dirs, files in os.walk(os.path.join(ROOT, base)):
            dirs[:] = [d for d in dirs if d not in ("__pycache__", "node_modules")]
            for name in files:
                if name.endswith(".py"):
                    yield os.path.join(root, name)


def _violations(path):
    try:
        tree = ast.parse(open(path, encoding="utf-8", errors="replace").read(), filename=path)
    except SyntaxError:
        return
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "ishti.skills":
            for alias in node.names:
                if alias.name in SKILL_NAMES:
                    yield node.lineno, f"from ishti.skills import {alias.name}"
        elif isinstance(node, ast.Import):
            for alias in node.names:
                parts = alias.name.split(".")
                # exactly `ishti.skills.<folder>` is the trap; `ishti.skills.<folder>.<module>`
                # (e.g. ishti.skills.find_leads.skill) imports the real module and is fine
                if len(parts) == 3 and parts[:2] == ["ishti", "skills"] and parts[2] in SKILL_NAMES:
                    yield node.lineno, f"import {alias.name}"


def test_no_module_imports_a_skill_folder_as_a_module():
    assert SKILL_NAMES, "skill folder scan found nothing - the guard would pass vacuously"
    bad = [f"{os.path.relpath(p, ROOT)}:{line}: {what}"
           for p in _iter_py() for line, what in _violations(p)]
    assert not bad, ("a skill folder was imported as a module (it is a namespace package, not the "
                     "skill's code - name the module inside it instead):\n  " + "\n  ".join(bad))


def test_the_explicit_form_really_binds_the_functions():
    """The reason the guard exists: the safe form must expose the real callables."""
    from ishti.skills.skillsmith import skill as skillsmith
    for fn in ("scan", "validate", "install_folder"):
        assert callable(getattr(skillsmith, fn, None)), f"skillsmith.{fn} is missing"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
