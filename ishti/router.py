"""Fast routing before any LLM call: deterministic skill triggers, then Laya typed decisions,
then the default skill. Every decision is logged (facts.route_log) so the Laya typed-decisions
checkpoint can later be fine-tuned on ISHTI's own traffic (NEEDS.md)."""
import os
import re
import threading
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
_laya_lock = threading.Lock()
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
    with _laya_lock:
        if _laya is None and not laya_error and _cfg["use_laya"]:
            try:
                from laya import Router
                from laya.router import normalise_name
                import torch
                cuda = torch.cuda.is_available()
                router = Router(max_loaded=1, device="cuda" if cuda else "cpu")
                key = normalise_name(_cfg["laya_model"])
                router.models[key] = _cached_checkpoint(router.models[key])
                agent = router.load(key)  # CPU also preloads; predict never needs a hub lookup
                if cuda:
                    agent.model.half()
                _laya = router  # publish only after the complete load/precision change
            except Exception as exc:
                laya_error = f"{type(exc).__name__}: {exc}"
    return _laya


def _cached_checkpoint(spec):
    """Resolve the installed checkpoint without changing process-wide offline flags.

    Laya 0.3.20 accepts local paths in Router.models. Require its bundled tokenizer
    and encoder config too: absent directories make its Agent fetch a base model.
    """
    repo, subfolder = spec if isinstance(spec, (tuple, list)) else (spec, None)
    if not os.path.isdir(repo):
        from huggingface_hub import snapshot_download
        repo = snapshot_download(repo_id=repo, local_files_only=True)
    path = os.path.abspath(os.path.join(repo, subfolder) if subfolder else repo)
    for filename in ("rl_agent_config.json", "model.safetensors", "tokenizer/tokenizer_config.json", "encoder/config.json"):
        if not os.path.isfile(os.path.join(path, filename)):
            raise FileNotFoundError("Laya checkpoint is incomplete in the local cache; run the installer to repair it")
    return path


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

    # Explicit skill directive: "[skill:name]" or "/use <name>" or "/skill <name>"
    explicit = re.match(r"^(?:\[skill:(?P<name>[a-z0-9_-]+)\]|/(?:use|skill)\s+(?P<name2>[a-z0-9_-]+))\s*", text, re.I)
    if explicit:
        target = (explicit.group("name") or explicit.group("name2") or "").lower().replace("-", "_")
        if target in live:
            return done(target, 1.0, "explicit_directive")
        if target in skills and skills[target].status == "disabled" and target not in exclude and target not in blocked:
            return done(target, 1.0, "explicit_directive", needs_enable=True)

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

    if not r and live:  # Laya checkpoint not installed: let the pinned planner model pick, never fall to plain chat blindly
        picked = _llm_pick(text, live)
        if picked:
            return done(picked, 0.6, "llm", **extra)

    default = _cfg["default_skill"]
    return done(default if default in live else None, 0.0, "default", **extra)


_LLM_PICKS: dict[str, str | None] = {}
_SMALLTALK = re.compile(r"^\W*(hi|hello|hey|thanks?|thank you|ok(ay)?|good (morning|evening|night)|salam|bye)\b", re.I)


def _llm_pick(text, candidates):
    """One short planner-model call: which skill (or 'chat') fits this request. Cached per request text;
    fails open (None -> default chat) on any error or a name that is not a live skill."""
    key = " ".join(text.lower().split())[:300]
    if key in _LLM_PICKS:
        return _LLM_PICKS[key]
    if len(key.split()) < 2 or _SMALLTALK.match(key):
        return None
    pick = None
    try:
        from ishti import llm
        menu = chr(10).join(f"{n}: {str(s.manifest.get('route', ''))[:70]}" for n, s in sorted(candidates.items())
                         if n != _cfg["default_skill"] and s.manifest.get("route"))[:12000]
        out = llm.ask(
            f"Request: {text[:400]}" + chr(10) * 2 + "Skills (name: what it does):" + chr(10) + menu + chr(10) * 2 +
            ""
            "Answer with exactly ONE skill name from the list if a skill is needed to do or look up what the request "
            "asks (current facts, files, apps, web, reminders, money, messages, media...). Answer chat if it is plain "
            "conversation or timeless knowledge. Output only the name.",
            system="You route requests to tools. Output a single token: a skill name or chat.",
            role="planner", temperature=0, max_tokens=12)
        word = re.sub(r"[^a-z0-9_]", "", (out or "").strip().split()[0].lower()) if (out or "").strip() else ""
        pick = word if word in candidates and word != _cfg["default_skill"] else None
    except Exception as exc:
        from ishti.core.logutil import swallowed
        swallowed("ishti/router.py:_llm_pick", exc)
        return None  # not cached: a transient failure must not stick
    if len(_LLM_PICKS) > 500:
        _LLM_PICKS.clear()
    _LLM_PICKS[key] = pick
    return pick
