"""ISHTI session schema v1 (WP-ECC, matrix ECC-09; ECC's harness-neutral `ecc.session.v1`).

One event shape for monitoring whatever runs a session - ISHTI's own skills, a Claude Code /
ECC harness, or a sub-agent - so the overlay pills, the dashboard and any future adapter read
the same fields. Based on ECC's session-adapter contract, re-expressed for ISHTI:

    {"v": 1, "session_id": str, "agent": str, "state": STATE, "ts": float,
     "parent": str|None, "label": str, "detail": str, "tool": str, "tier": str, "source": str}

  * agent matches the same rule the overlay enforces: ^[a-z0-9-]{1,24}$ ("ishti" fallback)
  * state is one of STATES - the same lifecycle the pills render
  * serialize/validate are total: validate() reports problems, make() never raises
  * emit() publishes it on the backend event bus (ishti.events), so /ws/events clients see it

The full contract is documented in docs/SESSION_SCHEMA.md (kept in sync by test).
Self-check: python ishti/session_schema.py
"""
from __future__ import annotations

import os
import re
import sys
import time

# `python ishti/session_schema.py` (the documented self-check) puts ishti/ first on sys.path; put
# the repo root back so the lazy `from ishti import events` in emit() resolves.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

V = 1
AGENT_RE = re.compile(r"^[a-z0-9-]{1,24}$")
STATES = ("idle", "thinking", "working", "finished", "error", "ratelimit", "question")
REQUIRED = ("v", "session_id", "agent", "state", "ts")
OPTIONAL = ("parent", "label", "detail", "tool", "tier", "source")
MAX_LABEL = 120
MAX_DETAIL = 500

_DOC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "docs", "SESSION_SCHEMA.md")


def make(session_id, agent, state, *, parent=None, label="", detail="", tool="", tier="",
         source="ishti", ts=None) -> dict:
    """A valid envelope; invalid agent -> \"ishti\", invalid state -> \"idle\", strings bounded.
    Never raises."""
    try:
        sid = str(session_id if session_id is not None else "default")[:120] or "default"
        ag = str(agent or "").lower().strip()
        if not AGENT_RE.match(ag):
            ag = "ishti"
        st = str(state or "").lower().strip()
        if st not in STATES:
            st = "idle"
        return {"v": V, "session_id": sid, "agent": ag, "state": st,
                "ts": float(ts if ts is not None else time.time()),
                "parent": str(parent)[:120] if parent else None,
                "label": str(label or "")[:MAX_LABEL],
                "detail": str(detail or "")[:MAX_DETAIL],
                "tool": str(tool or "")[:60],
                "tier": str(tier or "")[:40],
                "source": str(source or "ishti")[:40]}
    except Exception:
        return {"v": V, "session_id": "default", "agent": "ishti", "state": "idle", "ts": time.time(),
                "parent": None, "label": "", "detail": "", "tool": "", "tier": "", "source": "ishti"}


def validate(event) -> list:
    """Problems with an envelope ([] = it conforms). Total: junk returns problems, never raises."""
    problems = []
    try:
        if not isinstance(event, dict):
            return ["event must be an object"]
        for field in REQUIRED:
            if field not in event:
                problems.append(f"missing field: {field}")
        if event.get("v") != V:
            problems.append(f"v must be {V}")
        if not isinstance(event.get("session_id"), str) or not event.get("session_id"):
            problems.append("session_id must be a non-empty string")
        if not isinstance(event.get("agent"), str) or not AGENT_RE.match(str(event.get("agent"))):
            problems.append("agent must match ^[a-z0-9-]{1,24}$")
        if event.get("state") not in STATES:
            problems.append(f"state must be one of {STATES}")
        if not isinstance(event.get("ts"), (int, float)) or isinstance(event.get("ts"), bool):
            problems.append("ts must be a number")
        for field in OPTIONAL:
            if field in event and not isinstance(event[field], (str, type(None))):
                problems.append(f"{field} must be a string or null")
        if isinstance(event.get("label"), str) and len(event["label"]) > MAX_LABEL:
            problems.append("label too long")
        if isinstance(event.get("detail"), str) and len(event["detail"]) > MAX_DETAIL:
            problems.append("detail too long")
        return problems
    except Exception as exc:
        return [f"unusable event ({type(exc).__name__})"]


def conforms(event) -> bool:
    return not validate(event)


def emit(event) -> dict | None:
    """Publish a conforming envelope on the event bus as type \"session\"; None when invalid.
    Never raises."""
    try:
        if validate(event):
            return None
        from ishti import events
        return events.publish("session", event)
    except Exception:
        return None


if __name__ == "__main__":
    e = make("s1", "claude-cc", "working", label="Bash", tool="Bash", tier="T1")
    assert validate(e) == [] and conforms(e) and e["v"] == 1 and e["agent"] == "claude-cc"
    assert make("s1", "BAD NAME!", "weird")["agent"] == "ishti", "invalid agent falls back"
    assert make("s1", "ok", "nonsense")["state"] == "idle", "invalid state falls back to idle"
    assert make(None, None, None)["session_id"] == "default"
    assert all(conforms(make("s", "a", st)) for st in STATES)
    # strict validation reports every problem
    bad = validate({"v": 2, "session_id": "", "agent": "NOPE", "state": "exploded", "ts": "now"})
    assert len(bad) >= 5 and any("agent" in p for p in bad) and any("state" in p for p in bad), bad
    assert validate(None) == ["event must be an object"] and validate("x") == ["event must be an object"]
    assert not conforms({})
    assert emit({"nope": 1}) is None
    got = []
    from ishti import events
    events.subscribe(got.append)
    msg = emit(e)
    assert msg and got and got[-1]["type"] == "session" and got[-1]["data"]["state"] == "working"
    assert len(e["label"]) <= MAX_LABEL and len(make("s", "a", "idle", label="L" * 999)["label"]) == MAX_LABEL
    doc = open(_DOC, encoding="utf-8").read()
    for needle in ("session_id", "agent", "state", "ishti.session.v1", str(V)):
        assert needle in doc, needle
    print("[self-check] session_schema OK - one envelope for every harness, strict validate, "
          "forgiving make, bus emit, doc in sync")
