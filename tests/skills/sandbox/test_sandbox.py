"""The code sandbox: parsing, the hardening flags on the container, timeouts clean up, Docker missing is explained.
Docker itself is faked here (no image pull in tests)."""
import subprocess

import pytest

from ishti.skills.sandbox import skill as sbx


@pytest.mark.parametrize("text,code", [("run this in the sandbox: ```print(1+1)```", "print(1+1)"),
                                       ("run this python:\n```python\nprint('hi')\n```", "print('hi')"),
                                       ("run in the sandbox print(2)", "print(2)")])
def test_code_is_extracted(text, code):
    assert sbx.code_of(text) == code


@pytest.mark.parametrize("text", ["run git status", "run the dishwasher", "test my microphone"])
def test_other_runs_are_not_claimed(text):
    assert not sbx.match(text)


def test_container_is_locked_down():
    cmd = " ".join(sbx.command("C:/tmp/w", "n"))
    for flag in ("--network none", "--read-only", "--cap-drop ALL", "no-new-privileges", "--pids-limit 64",
                 "--memory 256m", "--user 65534:65534", "--rm"):
        assert flag in cmd


def test_runs_and_reports(monkeypatch):
    calls = []

    def fake(*args, timeout=0):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, stdout="45\n", stderr="")
    monkeypatch.setattr(sbx, "_docker", fake)
    out = sbx.run("run this in the sandbox: ```print(sum(range(10)))```", {})
    assert "45" in out["reply"] and out["verified"] and calls[-1][0] == "run"


def test_timeout_removes_the_container(monkeypatch):
    calls = []

    def fake(*args, timeout=0):
        calls.append(args)
        if args[0] == "run":
            raise subprocess.TimeoutExpired("docker", timeout)
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
    monkeypatch.setattr(sbx, "_docker", fake)
    rc, out = sbx.run_python("while True: pass")
    assert rc == 124 and calls[-1][:2] == ("rm", "-f")


def test_docker_missing_is_explained(monkeypatch):
    monkeypatch.setattr(sbx.shutil, "which", lambda name: None)
    out = sbx.run("run this in the sandbox: ```print(1)```", {})
    assert "Docker Desktop" in out["reply"] and out["verified"] is False


def test_routing():
    from ishti import dispatcher, tool_routing
    assert tool_routing.route("run this in the sandbox: ```print(1)```", dispatcher.SKILLS).get("skill") == "sandbox"
