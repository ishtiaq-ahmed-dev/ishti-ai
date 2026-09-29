"""Pytest collection config (Prompt 4 QA).

tests/test_lifecycle.py is a STANDALONE self-check script (see its own docstring: it is run
directly as `python tests/test_lifecycle.py`, prints a self-check line and calls sys.exit() at
import time). Importing it during pytest collection aborts the whole run with INTERNALERROR,
so it is excluded here. Its contract-style sibling tests/test_lifecycle_contract.py is a real
pytest module (e2e-marked) and still runs.
"""
collect_ignore = ["test_lifecycle.py"]
