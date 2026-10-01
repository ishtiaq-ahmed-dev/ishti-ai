r"""Regression tests (WP-C1): the router must honour skill lifecycle statuses and the owner's
Disable button.

Before the fix, ishti/router.py:53 filtered only `status != "broken"`, so a skill whose
manifest says `"disabled"`, `"draft"` or `"retired"` was still chosen in chat - and a skill
switched off in Settings (`skills.disabled`, what `registry.set_enabled` - the dashboard's
Disable button - persists and `registry.retrieve()` honours) kept being routed to, because
`retrieve()` is not what answers chat.

Offline by design: Laya is patched off (no model load), learned corrections are stubbed,
Settings point at a scratch dir, and the real registry is used read-only in one test - the
owner's data is never touched (house rule).
"""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("ISHTI_STATE_DB", os.path.join(tempfile.mkdtemp(prefix="ishti_state_rlf_"), "state.db"))
os.environ["ISHTI_SETTINGS_DIR"] = tempfile.mkdtemp(prefix="ishti_settings_rlf_")

import pytest  # noqa: E402

from ishti.core import settings as settings_mod  # noqa: E402

settings_mod._service = None  # fresh settings service on the scratch dir

import ishti.router as R  # noqa: E402


class FakeSkill:
    """Tiny stand-in for ishti.skills.Skill: exactly the attributes route() touches."""

    def __init__(self, name, status="working", hit=True, priority=50):
        self.name = name
        self.status = status
        self.manifest = {"name": name, "route": f"asks for {name}", "priority": priority}
        self._hit = hit

    def matches(self, text):
        return self._hit


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    """No Laya, no learned corrections, cold flag cache - every test is self-contained."""
    monkeypatch.setattr(R, "laya", lambda: None)                       # never load a model
    monkeypatch.setattr("ishti.corrections.learned", lambda text: None)
    monkeypatch.setattr(R, "_flags_cache", (0.0, frozenset()), raising=False)  # absent before the fix


# ------------------------------------------------------------------ lifecycle statuses
@pytest.mark.parametrize("status", ["disabled", "draft", "retired"])
def test_non_routable_status_never_chosen(status):
    skills = {"ghost": FakeSkill("ghost", status=status, hit=True), "chat": FakeSkill("chat", hit=False)}
    dec = R.route("anything at all", skills)
    assert dec["skill"] != "ghost", dec


@pytest.mark.parametrize("status", ["disabled", "draft", "retired"])
def test_non_routable_status_never_chosen_as_only_skill(status):
    dec = R.route("anything at all", {"ghost": FakeSkill("ghost", status=status, hit=True)})
    assert dec["skill"] is None, dec


def test_broken_status_still_excluded():
    dec = R.route("anything at all", {"broken_one": FakeSkill("broken_one", status="broken", hit=True)})
    assert dec["skill"] is None, dec


def test_working_status_skill_is_still_chosen():
    """The fix must not disable everything: a working skill still wins deterministically."""
    dec = R.route("anything at all", {"chat": FakeSkill("chat", status="working", hit=True)})
    assert dec["skill"] == "chat" and dec["source"] == "rule", dec


def test_exclude_argument_still_wins():
    dec = R.route("anything at all", {"chat": FakeSkill("chat", hit=True), "ghost": FakeSkill("ghost", hit=True)},
                  exclude=("chat",))
    assert dec["skill"] == "ghost", dec


# ------------------------------------------------------------------ Settings disable flag
def test_settings_disabled_name_never_chosen():
    """What the dashboard's Disable button writes (skills.disabled) must stop chat routing."""
    from ishti.skills import registry as REG
    svc = settings_mod.get_settings_service()
    before = list(svc.get().get("skills", {}).get("disabled") or [])
    svc.update({"skills": {"disabled": ["ghost"]}})
    try:
        R._flags_cache = (0.0, frozenset())   # drop the TTL cache: the change must be seen now
        dec = R.route("anything at all", {"ghost": FakeSkill("ghost", hit=True), "chat": FakeSkill("chat", hit=False)})
        assert dec["skill"] != "ghost", dec
    finally:
        svc.update({"skills": {"disabled": before}})
        R._flags_cache = (0.0, frozenset())   # never leak the scratch flag into another test


def test_router_uses_registry_disabled_names_accessor(monkeypatch):
    """route() consults the registry's public accessor instead of duplicating its logic."""
    from ishti.skills import registry as REG
    assert hasattr(REG, "disabled_names"), "registry.disabled_names() must be the public flag accessor"
    monkeypatch.setattr(REG, "disabled_names", lambda: {"ghost"})
    R._flags_cache = (0.0, frozenset())
    dec = R.route("anything at all", {"ghost": FakeSkill("ghost", hit=True), "chat": FakeSkill("chat", hit=False)})
    assert dec["skill"] != "ghost", dec


def test_settings_failure_cannot_break_routing(monkeypatch):
    """A broken Settings store degrades to 'nothing disabled', not to a routing crash."""
    from ishti.skills import registry as REG

    def boom():
        raise RuntimeError("settings store on fire")

    monkeypatch.setattr(REG, "disabled_names", boom)
    R._flags_cache = (0.0, frozenset())
    dec = R.route("anything at all", {"chat": FakeSkill("chat", hit=True)})
    assert dec["skill"] == "chat" and dec["source"] == "rule", dec


def test_disabled_names_cached_not_read_per_call(monkeypatch):
    """route() is on the hot path of every request: the Settings read is cached for a few seconds."""
    from ishti.skills import registry as REG
    calls = {"n": 0}

    def counted():
        calls["n"] += 1
        return set()

    monkeypatch.setattr(REG, "disabled_names", counted)
    R._flags_cache = (0.0, frozenset())
    R.route("first request", {"chat": FakeSkill("chat", hit=True)})
    R.route("second request", {"chat": FakeSkill("chat", hit=True)})
    assert calls["n"] == 1, f"disabled_names() called {calls['n']} times for 2 routes (cache miss?)"


# ------------------------------------------------------------------ the real registry
def test_real_registry_still_routes_known_request():
    """The fix must not change routing for the shipped skills: a weather request still hits weather."""
    from ishti.skills import load_skills
    skills = load_skills()
    dec = R.route("what is the weather tomorrow in Peshawar?", skills)
    assert dec["skill"] == "weather" and dec["source"] == "rule", dec
