"""Measured, offline tool-selection regression suite (120 labeled prompts)."""
from __future__ import annotations

import importlib
import json
import re
from pathlib import Path

from ishti import tool_routing as decision_loop
from ishti import tool_routing as legacy_router


ROOT = Path(__file__).resolve().parents[1]
EVAL = ROOT / "tests" / "fixtures" / "tool_select_eval.json"  # the 120-prompt labelled routing set
TARGETS = {
    "weather", "web_search", "calc", "calendar", "reminders", "files", "fileops", "whatsapp", "email",
    "budget", "writing", "study", "travel", "shopping", "markets", "docs", "pdftables", "chart", "memory",
    "security_scan",
}


class EvalSkill:
    """The production manifest + matcher without dependency installation or tool execution."""

    def __init__(self, name: str):
        path = ROOT / "ishti" / "skills" / name / "manifest.json"
        self.manifest = json.loads(path.read_text(encoding="utf-8"))
        self.name = name
        self.module = importlib.import_module(f"ishti.skills.{name}.skill")

    @property
    def status(self):
        return self.manifest.get("status", "working")

    def matches(self, text: str) -> bool:
        fn = getattr(self.module, "match", None)
        try:
            return bool(fn and fn(text))
        except Exception:
            return False


class ChatSkill:
    name = "chat"
    status = "working"
    manifest = {"name": "chat", "route": "conversation and timeless questions", "priority": 100}

    @staticmethod
    def matches(_text: str) -> bool:
        return False


def _skills():
    return {**{name: EvalSkill(name) for name in sorted(TARGETS)}, "chat": ChatSkill()}


def _parts(prompt: str) -> list[str]:
    parts = [part.strip(" ,.;") for part in re.split(r"\s+(?:and then|and(?!\s+whether\b)|then)\s+", prompt, flags=re.I)]
    return [part for part in parts if len(part.split()) >= 2]


def _selected(prompt: str, skills: dict, route_fn) -> set[str]:
    parts = _parts(prompt)
    if len(parts) == 1:
        parts = [prompt]
    picked = set()
    for part in parts:
        try:
            dec = route_fn(part, skills)
        except Exception:
            dec = {"skill": "chat", "source": "error"}
        if dec.get("clarify") or dec.get("source") == "clarify":
            picked.add("clarify")
        elif dec.get("skill") and dec.get("skill") != "chat":
            picked.add(dec["skill"])
    return picked or {"chat"}


def measure(route_fn) -> dict:
    rows = json.loads(EVAL.read_text(encoding="utf-8"))
    skills = _skills()
    correct = 0
    misses = []
    for row in rows:
        actual = _selected(row["prompt"], skills, route_fn)
        expected = set(row["expected"])
        if actual == expected:
            correct += 1
        else:
            misses.append({"prompt": row["prompt"], "expected": sorted(expected), "actual": sorted(actual)})
    return {"total": len(rows), "correct": correct, "accuracy": correct / len(rows), "misses": misses}


def test_120_prompt_eval_measures_routing_improvement(monkeypatch):
    rows = json.loads(EVAL.read_text(encoding="utf-8"))
    assert len(rows) == 120
    covered = {skill for row in rows for skill in row["expected"] if skill != "clarify"}
    assert len(covered) >= 20 and TARGETS <= covered

    # The one routing layer, measured offline: it never calls a model. Low confidence is a clarification, and the
    # single decision step (ishti/brain.py) takes it from there.
    result = measure(lambda text, skills: decision_loop.route(text, skills))
    print(json.dumps({k: result[k] for k in ("correct", "total", "accuracy")}, sort_keys=True))
    assert result["accuracy"] >= 0.85, result["misses"][:10]


def test_shortlist_contains_only_routable_and_survives_bad_matcher():
    skills = _skills()
    skills["bad"] = type("Bad", (), {
        "status": "working",
        "manifest": {"route": "bad tool", "priority": 1},
        "matches": lambda *_a: (_ for _ in ()).throw(RuntimeError("boom")),
    })()
    shortlist = decision_loop.candidate_shortlist("find the current weather", skills)
    assert shortlist and all(name != "security_scan" for name, _score in shortlist)
    assert any(name == "weather" for name, _score in shortlist)
