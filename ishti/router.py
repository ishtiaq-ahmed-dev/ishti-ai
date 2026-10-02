"""Fast routing before any LLM call: deterministic skill triggers, then Laya typed decisions,
then the default skill. Every decision is logged (facts.route_log) so the Laya typed-decisions
checkpoint can later be fine-tuned on ISHTI's own traffic (NEEDS.md)."""
import os
import time
import warnings

from ishti import CONFIG

# The shipped checkpoint's own calibration warning (uncalibrated temperatures) fired on every load
# and is not actionable here - its confidences are only compared relative to each other. Filtered once
# at import, in the main thread: catch_warnings() contexts are not thread-safe and routing runs in
# parallel plan steps.
warnings.filterwarnings("ignore", message=".*checkpoint ships invalid temperatures.*", category=RuntimeWarning)

_cfg = CONFIG["router"]
_laya = None
laya_error = ""

# Statuses the router must never pick (manifest `status`; "broken" also covers load failures).
# The owner turning a skill off from the dashboard is separate: registry.disabled_names(), below.
NON_ROUTABLE = {"broken", "draft", "disabled", "retired"}
_FLAGS_TTL = 5.0                                # s: route() runs per request; don't read Settings every call
_flags_cache: tuple[float, frozenset] = (0.0, frozenset())   # (monotonic timestamp, disabled skill names)


def _disabled_flags() -> frozenset:
    """Skill names switched off in Settings, cached for _FLAGS_TTL seconds. Lazy import (the registry's
    retrieve() imports router back) and never raises: a Settings problem degrades to 'nothing disabled'
    instead of breaking every route()."""
    global _flags_cache
    ts, names = _flags_cache
    if time.monotonic() - ts < _FLAGS_TTL:
        return names
    try:
        from ishti.skills import registry
        names = frozenset(registry.disabled_names())
    except Exception:
        names = frozenset()
    _flags_cache = (time.monotonic(), names)
    return names


def laya():
    """Lazy Laya Router; None (with laya_error set) when laya/torch isn't usable."""
    global _laya, laya_error
    if _laya is None and not laya_error and _cfg["use_laya"]:
        # Local-first: the weights are already in the Hugging Face cache, so the loader must not ask the hub anything (it sent
        # ~6 requests to huggingface.co at every boot - a network call from a runtime that promises none, and a
        # failure point offline). Offline first; only a machine that has never downloaded the model goes online, once.
        for offline in ("1", "0"):
            os.environ["HF_HUB_OFFLINE"] = offline
            try:
                from laya import Router
                import torch
                cuda = torch.cuda.is_available()
                _laya = Router(max_loaded=1, device="cuda" if cuda else "cpu")
                if cuda:  # fp16: same decisions, 0.9 GB instead of 1.7 GB of VRAM next to the LLM, ~35 ms
                    _laya.load(_cfg["laya_model"]).model.half()
                laya_error = ""
                break
            except Exception as exc:
                _laya = None
                laya_error = f"{type(exc).__name__}: {exc}"
        os.environ.pop("HF_HUB_OFFLINE", None)   # only for the duration of this load: other loaders keep their own behaviour
    return _laya


def questions(skills):
    """One forward pass: a skill choice, a yes/no per skill, and Laya's router preset.
    Choice alone under-picks generic skills and yes/no alone is too timid; their sum scored
    22/24 on a labelled set vs 16/24 for choice alone (see ishti/check.py)."""
    from laya import router_questions
    routes = {n: s.manifest["route"] for n, s in skills.items()}
    return {
        "skill": {"type": "choice", "instructions": "Which assistant skill should handle `request`?",
                  "criteria": routes},
        **{f"is_{n}": {"type": "noul", "instructions": f"Does `request` ask for {r}?"} for n, r in routes.items()},
        **router_questions(),
    }


def route(text, skills, exclude=()):
    """-> {"skill", "confidence", "source", "ms", ...laya extras}; skill None if nothing is left."""
    t0 = time.perf_counter()
    blocked = _disabled_flags()
    live = {n: s for n, s in skills.items()
            if s.status not in NON_ROUTABLE and n not in exclude and n not in blocked}
    done = lambda skill, conf, source, **kw: {"skill": skill, "confidence": round(conf, 3), "source": source,
                                              "ms": round((time.perf_counter() - t0) * 1000, 1), **kw}

    from ishti import corrections  # D4: what the owner corrected before wins (exact request, or 2+ agreeing)
    fixed = corrections.learned(text)
    if fixed in live:
        return done(fixed, 1.0, "learned")
    for s in sorted(live.values(), key=lambda s: s.manifest.get("priority", 50)):
        if s.matches(text):
            return done(s.name, 1.0, "rule")
    # A skill that ships switched off ("disabled") but whose own trigger asks for it exactly: offered, never run silently -
    # the dispatcher asks "turn it on?" first (owner: "when they are needed it should be usable").
    for s in sorted(skills.values(), key=lambda s: s.manifest.get("priority", 50)):
        if s.status == "disabled" and s.name not in exclude and s.name not in blocked and s.matches(text):
            return done(s.name, 1.0, "rule", needs_enable=True)

    extra = {}
    r = laya()
    # "laya": false = trigger-only skill (deterministic cue exists; as a Laya option it only adds confusion)
    candidates = {n: s for n, s in live.items() if s.manifest.get("laya", True)}
    if r and candidates:
        try:
            a = r.predict({"request": text}, questions(candidates), model=_cfg["laya_model"])["answers"]
            probs = a["skill"]["probabilities"]
            score = {n: probs[n] + a[f"is_{n}"]["noul"] for n in candidates}
            skill = max(score, key=score.get)
            conf = score[skill] / 2
            extra = {"scores": [(n, round(v / 2, 3)) for n, v in sorted(score.items(), key=lambda kv: -kv[1])[:3]],
                     "domain": a["domain"]["choice"], "needs_tools": a["needs_tools"]["noul"],
                     "sensitive": a["is_sensitive"]["noul"], "laya_choice": skill}
            if conf >= _cfg["min_confidence"]:
                return done(skill, conf, "laya", **extra)
        except Exception as exc:
            extra = {"laya_error": f"{type(exc).__name__}: {exc}"}

    default = _cfg["default_skill"]
    return done(default if default in live else None, 0.0, "default", **extra)
