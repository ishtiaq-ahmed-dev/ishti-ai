"""tools/codegen_quality.py (N24): the measuring core, offline, with fake coder models. No GPU, no Ollama."""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import codegen_quality as cq  # noqa: E402

GOOD = '''import re

def match(text):
    return bool(re.search(r"roman numeral", text, re.I))

def run(text, session):
    return {"reply": "XIV", "sources": []}

if __name__ == "__main__":
    assert match("convert a roman numeral")
    assert run("x", {})["reply"] == "XIV"
    print("ok")
'''
BAD_SELFCHECK = GOOD.replace('assert run("x", {})["reply"] == "XIV"', 'assert run("x", {})["reply"] == "nope"')
SYNTAX_ERROR = "def match(text) return True\ndef run(text, session): pass\n"


def fake_models(by_rung):
    """ask_for_rung(rung) -> an llm_fn that answers with that rung's canned JSON reply every time."""
    def ask_for_rung(rung):
        answer = json.dumps({"name": "x", "description": "d", "route": "r", "tier": "T0",
                             "scope": {"write": [], "net": [], "apps": False}, "python_packages": [],
                             "skill_py": by_rung[rung]})
        return lambda prompt, **kw: answer
    return ask_for_rung


def test_check_module_tells_good_from_broken():
    assert cq.check_module(GOOD) == {"compiles": True, "selfcheck": True, "detail": ""}
    bad = cq.check_module(BAD_SELFCHECK)
    assert bad["compiles"] is True and bad["selfcheck"] is False and "AssertionError" in bad["detail"]
    syn = cq.check_module(SYNTAX_ERROR)
    assert syn["compiles"] is False and syn["selfcheck"] is False


def test_matrix_counts_every_outcome_per_rung_and_task():
    rows = cq.run_matrix(fake_models({"good": GOOD, "weak": BAD_SELFCHECK, "broken": SYNTAX_ERROR}),
                         ["good", "weak", "broken"], tasks={"roman": cq.TASKS["roman"]}, runs=2)
    by = {r["rung"]: r for r in rows}
    assert (by["good"]["usable"], by["good"]["compiles"], by["good"]["selfcheck"]) == (2, 2, 2)
    assert (by["weak"]["usable"], by["weak"]["compiles"], by["weak"]["selfcheck"]) == (2, 2, 0)
    assert (by["broken"]["usable"], by["broken"]["compiles"], by["broken"]["selfcheck"]) == (2, 0, 0)
    assert all(r["calls"] == [1, 1] for r in rows) and by["weak"]["failures"]


def test_unusable_model_output_is_recorded_not_raised():
    rows = cq.run_matrix(lambda rung: (lambda prompt, **kw: "I cannot help with that."), ["mute"],
                         tasks={"roman": cq.TASKS["roman"]}, runs=1)
    r = rows[0]
    assert r["usable"] == 0 and r["calls"] == [5] and "never returned usable code" in r["failures"][0]


def test_table_renders_one_line_per_rung_and_task():
    rows = cq.run_matrix(fake_models({"good": GOOD}), ["good"], tasks={"roman": "r", "temperature": "t"}, runs=1)
    text = cq.table(rows)
    assert text.count("\n") == 3 and "| good | roman | 1/1 | 1/1 | 1/1 |" in text
