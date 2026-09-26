"""Fast routing before any LLM call: deterministic skill triggers, then Laya typed decisions,
then the default skill. Every decision is logged (facts.route_log) so the Laya typed-decisions
checkpoint can later be fine-tuned on ISHTI's own traffic (NEEDS.md)."""
import time

from ishti import CONFIG

_cfg = CONFIG["router"]
_laya = None
laya_error = ""


def laya():
    """Lazy Laya Router; None (with laya_error set) when laya/torch isn't usable."""
    global _laya, laya_error
    if _laya is None and not laya_error and _cfg["use_laya"]:
        try:
            from laya import Router
            import torch
            cuda = torch.cuda.is_available()
            _laya = Router(max_loaded=1, device="cuda" if cuda else "cpu")
            if cuda:  # fp16: same decisions, 0.9 GB instead of 1.7 GB of VRAM next to the LLM, ~35 ms
                _laya.load(_cfg["laya_model"]).model.half()
        except Exception as exc:
            laya_error = f"{type(exc).__name__}: {exc}"
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
    live = {n: s for n, s in skills.items() if s.status != "broken" and n not in exclude}
    done = lambda skill, conf, source, **kw: {"skill": skill, "confidence": round(conf, 3), "source": source,
                                              "ms": round((time.perf_counter() - t0) * 1000, 1), **kw}

    for s in sorted(live.values(), key=lambda s: s.manifest.get("priority", 50)):
        if s.matches(text):
            return done(s.name, 1.0, "rule")

    extra = {}
    r = laya()
    if r and live:
        try:
            a = r.predict({"request": text}, questions(live), model=_cfg["laya_model"])["answers"]
            probs = a["skill"]["probabilities"]
            score = {n: probs[n] + a[f"is_{n}"]["noul"] for n in live}
            skill = max(score, key=score.get)
            conf = score[skill] / 2
            extra = {"domain": a["domain"]["choice"], "needs_tools": a["needs_tools"]["noul"],
                     "sensitive": a["is_sensitive"]["noul"], "laya_choice": skill}
            if conf >= _cfg["min_confidence"]:
                return done(skill, conf, "laya", **extra)
        except Exception as exc:
            extra = {"laya_error": f"{type(exc).__name__}: {exc}"}

    default = _cfg["default_skill"]
    return done(default if default in live else None, 0.0, "default", **extra)
