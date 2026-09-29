"""Skill Registry (Part A1-A3): one record per skill, retrieved by the router.

Each record carries what the spec pins down: id, name, domain, description, in/out schemas,
tools used, risk level, confirmation requirement, enabled flag, version, status, test file
and examples. Records are BUILT from the skill manifests (the loader already knows
status/category/tier/scope) — the registry never becomes a second source of truth.

Retrieval (A2) reuses ISHTI's paid-for routing (deterministic matches + Laya typed-decision
scores, ~0.2 ms) and applies the spec's filters: enabled flag, domain, and the 5-20 window.

Enable/disable (A3) persists to the typed Settings store (skills.enabled / skills.disabled /
skills.disabled_domains) — the same values the Settings UI edits; unknown skills are refused.
"""
from __future__ import annotations

import threading

from ishti.core.settings import get_settings_service

_lock = threading.Lock()
_cache: dict | None = None


def _service():
    return get_settings_service()


def _flags() -> tuple[set, set, set]:
    """(enabled_only, disabled, disabled_domains) from Settings — empty enabled = all."""
    try:
        s = _service().get().get("skills", {})
        return (set(s.get("enabled") or []), set(s.get("disabled") or []),
                set(s.get("disabled_domains") or []))
    except Exception:
        return set(), set(), set()


def record_for(skill) -> dict:
    """One registry record for a loaded Skill object (A1). Manifest is the source of truth."""
    tier = (skill.manifest.get("tier") or "T1").upper()
    risk = {"T0": "low", "T1": "medium", "T2": "high", "T3": "high"}.get(tier, "medium")
    status = skill.status
    m = skill.manifest
    return {
        "skill_id": m.get("name") or skill.name,
        "name": skill.name,
        "domain": m.get("category") or "General",
        "description": (m.get("description") or "")[:400],
        "input_schema": {"request": "string", "session": "session view"},
        "output_schema": {"reply": "string", "sources": "list"},
        "tools_used": m.get("needs", {}).get("python", [])[:8],
        "risk_level": risk,
        "requires_confirmation": tier in ("T2", "T3"),
        "enabled": True,
        "version": m.get("version", "1.0.0"),
        "status": ("implemented" if status == "working" else
                   "blocked" if status == "broken" else
                   status if status in ("partial", "planned") else "partial"),
        "test_file": m.get("test_file", ""),
        "embedding": None,          # computed at retrieval time (router scores), never stored
        "examples": [m.get("route", "")] if m.get("route") else [],
        "path": m.get("path", skill.path),
    }


def records(skills: dict, refresh: bool = False) -> dict:
    """{skill name: record} for the loaded skills (cached; manifests don't change at runtime)."""
    global _cache
    with _lock:
        if _cache is None or refresh:
            _cache = {name: record_for(s) for name, s in skills.items()}
        return _cache


def reset_cache():
    """After a skill is added/removed (skillsmith), the next build sees it."""
    global _cache
    with _lock:
        _cache = None


def _allowed(name: str, rec: dict, enabled_only: set, disabled: set, disabled_domains: set) -> bool:
    if name in disabled:
        return False
    if rec["domain"] in disabled_domains:
        return False
    if enabled_only and name not in enabled_only:
        return False
    return True


def retrieve(request: str, skills: dict, k: int = 12, domain: str | None = None,
             with_scores: bool = False):
    """The 5-20 window (A2): deterministic matches + Laya scores, filtered by enabled flag,
    disabled list and domain. Returns records (highest score first); with_scores=True also
    attaches the routing score to each record. Never raises: routing problems degrade to
    the deterministic matches alone."""
    recs = {n: r for n, r in records(skills).items()
            if (not domain or r["domain"] == domain)}
    enabled_only, disabled, disabled_domains = _flags()
    recs = {n: r for n, r in recs.items()
            if _allowed(n, r, enabled_only, disabled, disabled_domains)}
    if not recs:
        return []
    scored: dict[str, float] = {}
    try:
        from ishti.router import route as _route
        usable = {n: s for n, s in skills.items() if n in recs and s.status != "broken"}
        dec = _route(request, usable)
        for n, c in (dec.get("scores") or []):
            if n in recs:
                scored[n] = float(c)
        if dec.get("skill") and dec["skill"] in recs:
            scored[dec["skill"]] = max(scored.get(dec["skill"], 0.0), float(dec.get("confidence") or 1.0))
    except Exception:
        pass
    for name, s in skills.items():  # deterministic matches always considered
        if name in recs and not name in scored and s.status != "broken":
            try:
                if s.matches(request):
                    scored[name] = max(scored.get(name, 0.0), 0.9)
            except Exception:
                pass
    ranked = sorted(recs.items(), key=lambda kv: (-scored.get(kv[0], 0.0), kv[0]))
    k = max(5, min(20, k))
    hot = [n for n, _r in ranked if scored.get(n, 0.0) > 0]      # what routing picked for THIS request
    cold = [n for n, _r in ranked if scored.get(n, 0.0) <= 0]    # the rest, in stable order
    chosen = (hot + cold)[:max(k, len(hot))] or [n for n, _r in ranked[:k]]
    chosen = chosen[:k]
    return [dict(recs[n], _score=scored.get(n, 0.0)) if with_scores else dict(recs[n]) for n in chosen]


def set_enabled(target: str, on: bool, *, is_domain: bool = False) -> dict:
    """Enable/disable one skill or one whole domain (A3), persisted in Settings.
    Unknown targets raise ValueError (the UI shows it; nothing silent)."""
    svc = _service()
    s = svc.get().get("skills", {})
    enabled = list(s.get("enabled") or [])
    disabled = list(s.get("disabled") or [])
    domains = list(s.get("disabled_domains") or [])
    if is_domain:
        from ishti.skills import SKILL_CATEGORIES
        known = set(SKILL_CATEGORIES.values()) | {r["domain"] for r in (records({}) or {}).values()}
        if target not in known:
            raise ValueError(f"unknown domain '{target}'")
        if on:
            domains = [d for d in domains if d != target]
        elif target not in domains:
            domains.append(target)
    else:
        if target not in _cache or not _cache:
            raise ValueError(f"unknown skill '{target}' (is it loaded?)")
        if on:
            disabled = [d for d in disabled if d != target]
            if enabled and target not in enabled:
                enabled.append(target)   # an allow-list must include what was just enabled
        elif target not in disabled:
            disabled.append(target)
            enabled = [e for e in enabled if e != target]
    svc.update({"skills": {"enabled": enabled, "disabled": disabled, "disabled_domains": domains}})
    return {"target": target, "enabled": bool(on), "is_domain": is_domain,
            "disabled_skills": disabled, "disabled_domains": domains, "enabled_only": enabled}


def describe(skills: dict) -> dict:
    """Registry overview for the dashboard: counts by status/risk + the flags."""
    recs = records(skills)
    by_status, by_risk = {}, {}
    for r in recs.values():
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1
        by_risk[r["risk_level"]] = by_risk.get(r["risk_level"], 0) + 1
    enabled_only, disabled, domains = _flags()
    return {"total": len(recs), "by_status": by_status, "by_risk": by_risk,
            "disabled": sorted(disabled), "disabled_domains": sorted(domains),
            "enabled_only": sorted(enabled_only)}


if __name__ == "__main__":
    import os
    import tempfile
    os.environ["ISHTI_SETTINGS_DIR"] = tempfile.mkdtemp(prefix="ishti_reg_")
    from ishti.skills import load_skills
    skills = load_skills()
    assert len(skills) > 50, len(skills)
    recs = records(skills, refresh=True)
    assert len(recs) == len(skills)
    r = recs["desktop"]
    assert r["domain"] == "System & Desktop" and r["status"] == "implemented", r
    assert r["risk_level"] in ("low", "medium", "high") and r["requires_confirmation"] is False  # T1
    t2 = [x for x in recs.values() if x["requires_confirmation"]]
    assert t2 and all(x["risk_level"] == "high" for x in t2), "T2/T3 -> high risk + confirm"
    # retrieval: a weather request ranks weather into the window
    win = retrieve("what's the weather tomorrow in Peshawar?", skills, k=12, with_scores=True)
    names = [x["name"] for x in win]
    assert "weather" in names, names[:8]
    assert 5 <= len(win) <= 20, len(win)
    top = win[0]
    assert top.get("_score", 0) >= (win[-1].get("_score", 0) or 0), "sorted by score"
    # domain filter
    web = retrieve("latest news", skills, k=20, domain="Web & Internet")
    assert web and all(x["domain"] == "Web & Internet" for x in web)
    # enable/disable one skill (persisted into the scratch settings dir)
    res = set_enabled("weather", False)
    assert res["enabled"] is False and "weather" in res["disabled_skills"]
    win2 = retrieve("what's the weather tomorrow?", skills, k=20)
    assert "weather" not in [x["name"] for x in win2], "disabled skills are not retrieved"
    set_enabled("weather", True)
    win3 = retrieve("what's the weather tomorrow?", skills, k=20)
    assert "weather" in [x["name"] for x in win3]
    try:
        set_enabled("no_such_skill", False)
        raise SystemExit("unknown skill accepted!")
    except ValueError:
        pass
    d = describe(skills)
    assert d["total"] == len(skills) and "implemented" in d["by_status"]
    print(f"[self-check] skills.registry OK — {d['total']} records, retrieval window, "
          f"enable/disable, domain filter, status counts {d['by_status']}")
