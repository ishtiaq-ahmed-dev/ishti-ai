"""WP-COUCOU acceptance: overlay session pills (matrix rows COU-03, COU-14).

T-C03/T-C14, against a fresh Pills registry with an injected clock:
  - lifecycle events drive states: SessionStart=idle -> UserPromptSubmit=thinking
    -> PreToolUse/PostToolUse=working (+ step ticker) -> Stop=finished -> idle after ttl;
    failures=error, Notification=question (never auto-fades), RateLimit=ratelimit
  - SessionEnd removes the pill; SubagentStart/Stop add/remove a sub-pill
  - agent names are regex-validated ^[a-z0-9-]{1,24}$; invalid names merge into the
    default "ishti" pill (COU-14 fallback); unknown events never raise
  - the module's own __main__ self-check stays green

Run: venv\\Scripts\\python -m pytest tests/test_overlay_pills.py -q
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from desktop.overlay import pills  # noqa: E402


class Clock:
    def __init__(self, t=100.0):
        self.t = t

    def __call__(self):
        return self.t


def _pills(ttl_s=30.0):
    c = Clock()
    return pills.Pills(ttl_s=ttl_s, now=c), c


def test_happy_path_lifecycle_with_ticker():
    p, c = _pills()
    assert p.apply("SessionStart", agent="claude-cc")
    assert p.state_of("claude-cc") == "idle"
    p.apply("UserPromptSubmit", agent="claude-cc")
    assert p.state_of("claude-cc") == "thinking"
    p.apply("PreToolUse", agent="claude-cc", label="Bash")
    p.apply("PostToolUse", agent="claude-cc", label="Edit")
    p.apply("PostToolUse", agent="claude-cc", label="Read")
    snap = p.snapshot()
    assert len(snap) == 1 and snap[0]["state"] == "working"
    assert snap[0]["ticker"] == "Bash -> Edit -> Read", snap[0]["ticker"]
    p.apply("Stop", agent="claude-cc")
    assert p.state_of("claude-cc") == "finished"
    c.t += 31
    assert p.state_of("claude-cc") == "idle", "finished fades to idle after ttl"


def test_error_paths_and_rate_limit():
    p, _ = _pills()
    p.apply("SessionStart", agent="cc")
    p.apply("PostToolUseFailure", agent="cc", label="Bash")
    assert p.state_of("cc") == "error"
    p.apply("RateLimit", agent="cc")
    assert p.state_of("cc") == "ratelimit"
    p.apply("UserPromptSubmit", agent="cc")
    assert p.state_of("cc") == "thinking", "next prompt clears rate limit"


def test_question_never_auto_fades():
    p, c = _pills(ttl_s=5)
    p.apply("SessionStart", agent="cc")
    p.apply("Notification", agent="cc", label="Proceed with deploy?")
    c.t += 10_000
    assert p.state_of("cc") == "question", "an unanswered question must persist"
    snap = p.snapshot()
    assert snap[0]["state"] == "question"


def test_session_end_removes_and_subagents_add_remove():
    p, _ = _pills()
    p.apply("SessionStart", agent="claude-cc")
    p.apply("SubagentStart", agent="worker-1", label="research")
    names = sorted(x["agent"] for x in p.snapshot())
    assert names == ["claude-cc", "worker-1"], names
    assert p.state_of("worker-1") == "working"
    p.apply("SubagentStop", agent="worker-1")
    p.apply("SessionEnd", agent="claude-cc")
    assert p.snapshot() == []


def test_c14_agent_name_validation_fallback():
    assert pills.agent_name("my-agent") == "my-agent"
    assert pills.agent_name("a" * 24) == "a" * 24
    for bad in ("Bad Name!", "UPPER", "x" * 25, "under_score", "", None, "sp ace"):
        assert pills.agent_name(bad) == "ishti", bad
    # invalid names all land on ONE default pill, valid names keep their own
    p, _ = _pills()
    p.apply("SessionStart", agent="BAD NAME")
    p.apply("SessionStart", agent="ok-agent")
    assert sorted(x["agent"] for x in p.snapshot()) == ["ishti", "ok-agent"]


def test_unknown_events_and_never_raise():
    p, _ = _pills()
    assert p.apply("NotAnEvent", agent="cc") is False
    assert p.snapshot() == []
    p.apply("SessionStart", agent="cc")
    assert p.apply(None, agent="cc") is False
    assert p.state_of("cc") == "idle"


def test_selfcheck_still_passes():
    mod = os.path.join(ROOT, "desktop", "overlay", "pills.py")
    r = subprocess.run([sys.executable, mod], capture_output=True, text=True,
                       timeout=60, cwd=ROOT)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "[self-check] overlay pills OK" in r.stdout
