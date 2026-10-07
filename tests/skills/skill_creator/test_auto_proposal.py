"""Offline end-to-end coverage for repeated-workflow skill proposals."""
import json
import os

import pytest

from ishti.skills.skill_creator import design, drafts
from ishti.skills.skill_creator import skill as creator


GENERATED_SKILL = """import re

_MATCH = re.compile(r"archive report", re.I)


def match(text):
    return bool(_MATCH.search(str(text or "")))


def run(text, session):
    return {"reply": "workflow complete", "sources": []}


if __name__ == "__main__":
    assert match("archive report")
    assert run("archive report", {})["reply"] == "workflow complete"
    print("workflow proposal self-check OK")
"""


GENERATED_JSON = json.dumps({
    "name": "model_tried_to_rename_it",
    "description": "Archive a located report as a document.",
    "route": "archive report",
    "tier": "T0",
    "scope": {"write": ["data/workflow_outputs"], "net": ["*"], "apps": True},
    "python_packages": [],
    "skill_py": GENERATED_SKILL,
})


@pytest.fixture()
def isolated_creator(tmp_path, monkeypatch):
    monkeypatch.setattr(drafts, "FOLDER", str(tmp_path / "drafts"))
    monkeypatch.setattr(drafts, "STATE", str(tmp_path / "state.json"))
    monkeypatch.setattr(drafts, "BACKUPS", str(tmp_path / "backups"))
    monkeypatch.setattr(design, "vram_check", lambda *_a, **_k: {
        "ok": True, "note": "synthetic coder", "coder": "synthetic", "free_mb": 8000,
    })
    return tmp_path


def _research(_request, _session=None):
    return {"ok": True, "sources": [], "queries": [], "path": "", "injections": [], "note": "offline"}


def test_two_successes_draft_vet_test_and_package_disabled(isolated_creator, monkeypatch):
    session = {}
    proposal = {
        "name": "archive_report_workflow",
        "steps": [{"tool": "files", "input": "find the report"},
                  {"tool": "docs", "input": "archive the report"}],
        "count": 2,
    }

    out = creator.propose_workflow(
        proposal, session, llm_fn=lambda *_a, **_k: GENERATED_JSON, research_fn=_research)

    assert out["stage"] == "gate", out
    assert out["facts"]["tests"]["ok"] is True
    assert out["facts"]["tests"]["green"] == drafts.MAX_GREEN
    assert out["facts"]["vetting"]["ok"] is True
    assert session["pending"]["skill"] == "skill_creator", "installation still needs an explicit yes"

    manifest_path = os.path.join(drafts.draft_dir("archive_report_workflow"), "manifest.json")
    manifest = json.loads(open(manifest_path, encoding="utf-8").read())
    assert manifest["name"] == "archive_report_workflow", "model output cannot rename the proposal"
    assert manifest["status"] == "disabled"
    assert manifest["tier"] == "T1", "the composed skill inherits the highest tool tier"
    assert manifest["workflow_proposal"]["count"] == 2
    assert [s["tool"] for s in manifest["workflow_proposal"]["steps"]] == ["files", "docs"]
    assert manifest["route"] == "archive report", "generated routing metadata must not be discarded"
    assert manifest["scope"]["net"] == [] and manifest["scope"]["apps"] is False

    enabled = []
    monkeypatch.setattr(drafts, "enable", lambda name: enabled.append(name) or {"ok": True})
    enable_session = {}
    gate = creator.run("enable the archive_report_workflow skill", enable_session)
    assert "pending" in enable_session and enabled == [], gate
    assert manifest["status"] == "disabled", "asking to enable is not owner approval"


def test_one_success_is_not_enough_to_create_a_draft(isolated_creator):
    def should_not_generate(*_args, **_kwargs):
        raise AssertionError("a one-off workflow must not reach code generation")

    out = creator.propose_workflow(
        {"name": "one_off", "steps": ["files", "docs"], "count": 1}, {},
        llm_fn=should_not_generate, research_fn=_research)
    assert out["stage"] == "proposal" and out["draft"] == ""
    assert not drafts.exists("one_off")

