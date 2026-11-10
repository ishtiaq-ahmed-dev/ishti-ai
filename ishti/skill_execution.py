"""Shared execution boundary for model-selected and specialist skill calls.

The caller may select a skill; only an owner confirmation may authorize T2.
Skill-specific confirmations are preserved and checked again when consumed.
"""
from __future__ import annotations

import threading

from ishti import audit, killswitch, scope, test_mode
from ishti.core.settings import get_settings_service

try:  # best-effort failure log (never breaks the module if logging is unavailable)
    from ishti.core.logutil import swallowed
except Exception:  # pragma: no cover
    def swallowed(*_a, **_k):
        return None

NON_RUNNABLE = frozenset(("broken", "draft", "disabled", "retired", "planned"))


def refusal(skills, name):
    skill = skills.get(name)
    if skill is None:
        return "not available"
    tier = skill.manifest.get("tier", "T1")
    if tier not in ("T0", "T1", "T2"):
        return f"blocked tier {tier}"
    if skill.status in NON_RUNNABLE:
        return f"{skill.status} skill"
    try:
        flags = get_settings_service().get().get("skills", {})
        enabled = flags.get("enabled") or []
        if (name in (flags.get("disabled") or [])
                or skill.manifest.get("category", "General") in (flags.get("disabled_domains") or [])
                or (enabled and name not in enabled)):
            return "disabled in Settings"
    except Exception:
        return "permission settings unavailable"
    return None


def available(skills):
    """Skills eligible to run right now — one Settings read for the whole set.

    refusal() is right for a single skill, but calling it per skill re-dumped the whole
    Settings model 124 times per request (the routing hot path); the flags are read once here.
    Any read failure still yields nothing runnable, exactly as refusal() failed closed.
    """
    try:
        flags = get_settings_service().get().get("skills", {})
    except Exception:
        return {}
    disabled = set(flags.get("disabled") or [])
    domains = set(flags.get("disabled_domains") or [])
    enabled = set(flags.get("enabled") or [])
    return {name: skill for name, skill in skills.items()
            if skill.manifest.get("tier", "T1") in ("T0", "T1", "T2")
            and skill.status not in NON_RUNNABLE
            and name not in disabled
            and skill.manifest.get("category", "General") not in domains
            and (not enabled or name in enabled)}


def _blocked(name, reason):
    audit.record("skill execution blocked", reason, skill=name)
    return {"reply": f"The {name} skill cannot run: {reason}.", "sources": [],
            "execution_blocked": True, "no_escalate": True}


def _once(callback, name):
    lock, used, generation = threading.Lock(), False, killswitch.generation()

    def confirmed():
        nonlocal used
        with lock:
            if used or generation != killswitch.generation():
                return _blocked(name, "confirmation already used or cancelled")
            used = True
        return callback()

    return confirmed


def _invoke(skills, name, text, session, action=None):
    killswitch.check()
    if reason := refusal(skills, name):
        return _blocked(name, reason)
    tier = skills[name].manifest.get("tier", "T1")
    with audit.context(skill=name, tier=tier, view=session, request=text,
                       session=session.get("id", audit.current().get("session", "")),
                       initiator="instructed"):
        if test_mode.enabled() and (tier == "T2" or action is not None):
            return test_mode.blocked(name, text, action, tier)
        audit.record(f"run {name}", "governed executor")
        out = action() if action is not None else skills[name].run(text, session)
        killswitch.check()
        pending = session.get("pending")
        if pending and callable(pending.get("action")):
            # Preserve the exact skill recap, recipient, and callback. Recheck
            # permissions/test mode when that particular owner yes is consumed.
            callback = pending["action"]
            target = pending.get("skill", name)
            recap = pending.get("gate", text)
            pending.setdefault("request", text)
            pending["action"] = _once(lambda: _invoke(skills, target, recap, session, callback), target)
            out = dict(out) if isinstance(out, dict) else {"reply": str(out or recap)}
            out["confirm_required"] = True
        # declined: the skill does not claim this request. A one-step plan then falls back to the normal route (brain.py)
        return out or {"reply": f"The {name} skill had nothing to do for this.", "sources": [], "declined": True}


def execute(skills, name, text, session):
    """Run an allowed skill, or return its pending owner confirmation unchanged.

T2 preparation itself may have side effects in legacy skills, so the model's
tool call first needs approval. That approval never skips the skill's more
specific recipient/action recap. A route value such as 'pending' grants nothing.
"""
    killswitch.check()
    if reason := refusal(skills, name):
        return _blocked(name, reason)
    if session.get("pending") and callable(session["pending"].get("action")):
        return {"reply": str(session["pending"].get("gate", "Please answer the pending confirmation.")),
                "sources": [], "confirm_required": True}
    scope.install()
    killswitch.propagate()
    is_t2 = skills[name].manifest.get("tier", "T1") == "T2"
    if is_t2 and name == "security":
        try:
            from ishti.skills.security.skill import OPS
            # "I found this text in a download: 'password: ...'. What should I do with it?" is advice about
            # an exposed credential: nothing is read, sent, encrypted or stored, so gating it behind a
            # yes/no hid the warning behind a confirmation the owner had no reason to expect (checklist 239).
            if any(name_ == "secret_found" and rx.search(text) for name_, rx in OPS):
                is_t2 = False
        except Exception as exc:  # fail closed: the tier stays T2 when the classifier cannot run
            swallowed("ishti/skill_execution.py:execute.security", exc)
    if is_t2 and skills[name].manifest.get("gates_itself"):
        # the skill asks its own exact recap (recipient + text) before any side effect: a generic "Run whatsapp for:
        # ...? (yes/no)" in front of it made the owner confirm one send twice, and gated read-only asks too
        is_t2 = False
    if is_t2:
        from ishti.skills import ask_first
        out = ask_first(session, name, f"Run {name} for: {text[:240]}",
                        _once(lambda: _invoke(skills, name, text, session), name))
        session["pending"]["request"] = text
        return {**out, "confirm_required": True}
    return _invoke(skills, name, text, session)
