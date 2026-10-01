"""ISHTI overlay session pills (WP-COUCOU, matrix COU-03 + COU-14; coucou AGENTS.md).

Live list of agent sessions with a step ticker and pill lifecycle states, re-implemented
from coucou's documented behaviour (docs/repos/coucou.md):
  - any agent gets a pill via the ``coucou_agent``-style field, regex-validated
    ``^[a-z0-9-]{1,24}$``; anything else falls back to the default ``ishti`` pill (C14)
  - lifecycle events -> states: SessionStart=idle, UserPromptSubmit=thinking,
    PreToolUse/PostToolUse=working (+ticker), PostToolUseFailure=error,
    Notification=question, Stop=finished, StopFailure=error, RateLimit=ratelimit,
    SessionEnd removes the pill; SubagentStart/Stop add/remove a sub-pill
  - finished/error/ratelimit fade back to idle after ttl_s; question NEVER expires
    (the owner must answer it)

Pure + clock-injectable: apply(event, ...) / snapshot() - testable headlessly.
Self-check: python desktop/overlay/pills.py
"""
import os
import re
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

AGENT_RE = re.compile(r"^[a-z0-9-]{1,24}$")     # coucou_agent field, verbatim
DEFAULT_AGENT = "ishti"
STATES = ("idle", "thinking", "working", "ratelimit", "question", "finished", "error")
EVENT_STATE = {
    "SessionStart": "idle",
    "UserPromptSubmit": "thinking",
    "PreToolUse": "working",
    "PostToolUse": "working",
    "PostToolUseFailure": "error",
    "Notification": "question",
    "Stop": "finished",
    "StopFailure": "error",
    "RateLimit": "ratelimit",
}
TICKER_EVENTS = ("PreToolUse", "PostToolUse")
FADE = ("finished", "error", "ratelimit")        # -> idle after ttl; question excluded
TICKER_CAP = 6


def agent_name(agent) -> str:
    """Valid ``coucou_agent`` name, else the default pill (COU-14 fallback)."""
    name = str(agent) if agent is not None else ""
    return name if AGENT_RE.match(name) else DEFAULT_AGENT


class Pills:
    def __init__(self, ttl_s: float = 30.0, now=None):
        self.ttl_s = float(ttl_s)
        self._now = now or time.monotonic
        self._pills = {}                          # agent -> dict(state, steps, since, ts)

    def apply(self, event: str, agent=None, label=None, now=None) -> bool:
        """Fold one lifecycle event into the pill list. True when something changed.
        Unknown events are ignored (never raise - this sits on the live event bus)."""
        now = self._now() if now is None else float(now)
        key = agent_name(agent)
        ev = str(event)
        if ev == "SessionEnd" or ev == "SubagentStop":
            return self._pills.pop(key, None) is not None
        if ev == "SubagentStart":
            pill = self._pills.setdefault(key, {"state": "working", "steps": [], "since": now})
            pill.update(state="working", since=now, ts=now)
            if label:
                self._ticker(pill, str(label))
            return True
        state = EVENT_STATE.get(ev)
        if state is None:
            return False
        pill = self._pills.setdefault(key, {"state": "idle", "steps": [], "since": now})
        pill.update(state=state, since=now, ts=now)
        if ev in TICKER_EVENTS and label:
            self._ticker(pill, str(label))
        return True

    @staticmethod
    def _ticker(pill, step: str):
        steps = pill["steps"]
        steps.append(step[:80])
        del steps[:-TICKER_CAP]

    def snapshot(self, now=None) -> list:
        """Render list, newest activity first; fades finished/error/ratelimit -> idle."""
        now = self._now() if now is None else float(now)
        out = []
        for key, pill in self._pills.items():
            self._fade(pill, now)
            out.append({
                "agent": key,
                "state": pill["state"],
                "steps": list(pill["steps"]),
                "ticker": " -> ".join(pill["steps"]),
                "since": pill["since"],
            })
        out.sort(key=lambda p: (-p["since"], p["agent"]))
        return out

    def _fade(self, pill, now):
        if pill["state"] in FADE and now - pill["since"] >= self.ttl_s:
            pill["state"] = "idle"            # fade in place; pill stays visible

    def state_of(self, agent) -> str:
        pill = self._pills.get(agent_name(agent))
        if not pill:
            return ""
        self._fade(pill, self._now())
        return pill["state"]


if __name__ == "__main__":
    # self-check: lifecycle happy path, error path, question persistence, TTL fade,
    # COU-14 name fallback, subagent add/remove, unknown event ignored
    clock = [100.0]
    p = Pills(ttl_s=30, now=lambda: clock[0])
    assert p.apply("SessionStart", agent="claude-cc")
    assert p.state_of("claude-cc") == "idle"
    p.apply("UserPromptSubmit", agent="claude-cc")
    assert p.state_of("claude-cc") == "thinking"
    p.apply("PreToolUse", agent="claude-cc", label="Bash")
    p.apply("PostToolUse", agent="claude-cc", label="Edit")
    snap = p.snapshot()
    assert snap[0]["state"] == "working" and snap[0]["ticker"] == "Bash -> Edit", snap
    p.apply("Stop", agent="claude-cc")
    assert p.state_of("claude-cc") == "finished"
    clock[0] += 31
    assert p.state_of("claude-cc") == "idle", "finished fades back to idle after ttl"
    # question never expires
    p.apply("Notification", agent="claude-cc")
    clock[0] += 99999
    assert p.state_of("claude-cc") == "question", "an unanswered question must persist"
    p.apply("SessionEnd", agent="claude-cc")
    assert p.snapshot() == [], "SessionEnd removes the pill"
    # COU-14: invalid names fall back to the default pill; valid names get their own
    p.apply("SessionStart", agent="Bad Name!")
    p.apply("SessionStart", agent="my-agent")
    p.apply("SessionStart", agent="X" * 25)
    names = sorted(x["agent"] for x in p.snapshot())
    assert names == ["ishti", "my-agent"], names
    # error path + rate limit + subagent
    p.apply("PostToolUseFailure", agent="my-agent")
    assert p.state_of("my-agent") == "error"
    p.apply("RateLimit", agent="my-agent")
    assert p.state_of("my-agent") == "ratelimit"
    p.apply("SubagentStart", agent="worker-1", label="research")
    assert p.state_of("worker-1") == "working"
    p.apply("SubagentStop", agent="worker-1")
    assert p.state_of("worker-1") == ""
    assert p.apply("TotallyUnknownEvent", agent="my-agent") is False, "unknown event ignored"
    print("[self-check] overlay pills OK - lifecycle states, ticker, COU-14 fallback, question persistence")
