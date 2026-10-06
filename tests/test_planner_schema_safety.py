"""Malformed model plans must never become partially executable workflows."""
import json
from types import SimpleNamespace

import pytest

from ishti import planner


@pytest.fixture
def plan(monkeypatch):
    skills = {name: SimpleNamespace(status="ready", manifest={"route": name})
              for name in ("chat", "browser", "memory")}
    monkeypatch.setattr(planner, "shortlist", lambda *args: None)

    def make(payload):
        monkeypatch.setattr(planner.llm, "ask", lambda *args, **kwargs: json.dumps(payload))
        return planner.plan("read the page and remember its title", skills)

    return make


@pytest.mark.parametrize("payload", [None, 1, True, [], "steps", {}, {"steps": None},
                                      {"steps": "wrong"}, {"steps": {"skill": "browser"}}])
def test_invalid_envelope_falls_back(plan, payload):
    assert plan(payload) is None


@pytest.mark.parametrize("bad", [None, 4, [], {}, {"skill": [], "request": "read"},
                                {"skill": {}, "request": "read"}, {"skill": "missing", "request": "read"},
                                {"skill": "browser", "request": None}, {"skill": "browser", "request": 42},
                                {"skill": "browser", "request": ["read"]},
                                {"skill": "browser", "request": {"text": "read"}},
                                {"skill": "browser", "request": "  "}])
def test_malformed_step_rejects_the_entire_plan(plan, bad):
    assert plan({"steps": [{"skill": "browser", "request": "read page"}, bad,
                           {"skill": "memory", "request": "remember {prev}"}]}) is None


def test_valid_plan_retains_dependencies_and_ignores_extra_keys(plan):
    assert plan({"steps": [{"skill": "browser", "request": " read page ", "tier": "T0"},
                           {"skill": "memory", "request": "remember {prev}"}]}) == [
        {"skill": "browser", "request": "read page"},
        {"skill": "memory", "request": "remember {prev}"},
    ]


def test_non_json_model_response_falls_back(monkeypatch, plan):
    monkeypatch.setattr(planner.llm, "ask", lambda *args, **kwargs: "not JSON")
    skills = {"browser": SimpleNamespace(status="ready", manifest={"route": "browse"})}
    assert planner.plan("read two pages", skills) is None


@pytest.mark.parametrize("count", [0, planner.MAX_STEPS + 1, 1000])
def test_empty_or_oversized_plan_is_not_truncated(plan, count):
    assert plan({"steps": [{"skill": "memory", "request": "remember {prev}"}] * count}) is None
