"""WP-ECC acceptance: session schema v1 contract (matrix row ECC-09).

T-ECC-09:
  - every lifecycle state conforms; REQUIRED fields are enforced by validate()
  - junk / missing / mis-typed envelopes produce problems (never exceptions)
  - make() is forgiving (agent -> ishti, state -> idle, strings bounded) and total
  - emit() reaches a bus subscriber as type "session" and drops invalid input
  - docs/SESSION_SCHEMA.md names every required field and the version (doc stays in sync)
  - the module's own __main__ self-check stays green

Run: venv\\Scripts\\python -m pytest tests/test_session_schema.py -q
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from ishti import events, session_schema as ss  # noqa: E402


def test_every_state_conforms_and_required_fields_are_enforced():
    for state in ss.STATES:
        e = ss.make("s-1", "claude-cc", state, label="step")
        assert ss.validate(e) == [] and ss.conforms(e), state
    base = ss.make("s-1", "ishti", "working")
    for field in ss.REQUIRED:
        broken = {k: v for k, v in base.items() if k != field}
        problems = ss.validate(broken)
        assert any(field in p for p in problems), (field, problems)


def test_make_is_forgiving_and_bounds_strings():
    e = ss.make("s", "BAD NAME!", "nonsense")
    assert e["agent"] == "ishti" and e["state"] == "idle"
    assert ss.make(None, None, None)["session_id"] == "default"
    assert ss.make("s", "ok-agent", "working", label="L" * 999)["label"] == "L" * ss.MAX_LABEL
    assert ss.make("s", "ok", "working", detail="D" * 9999)["detail"] == "D" * ss.MAX_DETAIL
    assert ss.make("s", "ok", "working", parent="")["parent"] is None
    assert isinstance(ss.make("s", "ok", "working", ts="junk")["ts"], float)
    for junk in (None, 42, [], object()):
        assert ss.conforms(ss.make(junk, junk, junk))


def test_validate_reports_problems_never_raises():
    bad = ss.validate({"v": 2, "session_id": "", "agent": "NOPE!", "state": "exploded", "ts": "now"})
    assert len(bad) >= 5
    assert ss.validate(None) == ["event must be an object"]
    assert ss.validate("not-a-dict") == ["event must be an object"]
    assert ss.validate([]) == ["event must be an object"]
    good = ss.make("s", "ishti", "idle")
    for key, value in (("label", 7), ("detail", 7), ("parent", 7), ("tier", [])):
        problems = ss.validate({**good, key: value})
        assert any(key in p for p in problems), (key, problems)
    assert ss.validate({**good, "label": "x" * (ss.MAX_LABEL + 1)}), "over-long label"
    assert ss.validate({**good, "detail": "x" * (ss.MAX_DETAIL + 1)}), "over-long detail"


def test_emit_reaches_the_bus_and_drops_invalid():
    got = []
    events.subscribe(got.append)
    try:
        msg = ss.emit(ss.make("s-1", "worker-1", "working", detail="pytest -q"))
        assert msg and msg["type"] == "session"
        assert got[-1]["data"]["agent"] == "worker-1" and got[-1]["data"]["state"] == "working"
        assert ss.emit({"nope": 1}) is None and ss.emit(None) is None
    finally:
        events.unsubscribe(got.append)


def test_doc_names_every_field():
    doc = open(os.path.join(ROOT, "docs", "SESSION_SCHEMA.md"), encoding="utf-8").read()
    assert "ishti.session.v1" in doc
    for field in ss.REQUIRED + ss.OPTIONAL:
        assert f"`{field}`" in doc, field
    for state in ss.STATES:
        assert f"`{state}`" in doc, state


def test_pills_lifecycle_matches_schema_states():
    from desktop.overlay import pills
    mapped = set(pills.EVENT_STATE.values()) if isinstance(pills.EVENT_STATE, dict) else set()
    assert mapped <= set(ss.STATES), mapped - set(ss.STATES)


def test_selfcheck_still_passes():
    mod = os.path.join(ROOT, "ishti", "session_schema.py")
    env = {**os.environ,
           "PYTHONPATH": ROOT + (os.pathsep + os.environ["PYTHONPATH"] if os.environ.get("PYTHONPATH") else "")}
    r = subprocess.run([sys.executable, mod], capture_output=True, text=True,
                       timeout=120, cwd=ROOT, env=env)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "[self-check] session_schema OK" in r.stdout
