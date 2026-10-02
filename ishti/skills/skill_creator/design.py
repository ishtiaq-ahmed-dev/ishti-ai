"""Stage 3 - risk & feasibility: say plainly what can be done, and name exactly what is missing.

Everything here is DETERMINISTIC keyword/rule logic, not an LLM judgement, so the refusal is auditable
and cannot be talked into passing. Four questions, always answered in the design doc:

  1. does it need a T2 side effect, or a T3 action that is blocked outright?
  2. does it need credentials/money/sensitive data ISHTI does not have?
  3. does it fit the VRAM and latency budget (RESEARCH.md R6: no partial offload, no model > budget)?
  4. what tier does it get - T2 for anything with side effects, T0 for read-only, and NEVER lowered here?

If it cannot be done locally, safely or within the limits, `refuse()` says so and names the exact gap;
the caller logs it to the needs log. A plausible-sounding "I'll try anyway" would be worse than a refusal.
"""
from __future__ import annotations

import re

T3 = re.compile(r"\b(?:live trade|trade (?:shares|stocks|crypto)|place (?:an? )?order on a broker|"
                r"withdraw (?:funds|money)|liquidate|access the (?:root|admin) (?:shell|account)|"
                r"self-modif(?:y|ication)|disable (?:the )?(?:kill switch|safety)|"
                r"delete (?:my )?(?:entire|all) (?:drive|disk|archive))\b", re.I)
CREDENTIAL = re.compile(r"\b(?:password|api[ _-]?key|access token|credentials|log ?in|sign[ _-]?in|"
                        r"oauth|secret key|2fa|otp|private key)\b", re.I)
MONEY = re.compile(r"\b(?:buy|purchase|order now|subscribe|checkout|pay for|transfer money|"
                   r"charge my (?:card|account))\b", re.I)
SIDE_EFFECT = re.compile(r"\b(?:send|reply|forward|upload|delete|remove|post|publish|email|message|"
                         r"share|install|rename|whatsapp|telegram|submit|comment|book|reserve|order)\b", re.I)
SENSITIVE = re.compile(r"\b(?:bank (?:account|statement)|id card|passport|medical (?:record|history)|"
                       r"home address|phone number|salary)\b", re.I)
LOCAL_WRITE = re.compile(r"\b(?:save|write|export|convert|merge|combine|create (?:a|the)|generate|"
                         r"build|extract)\b", re.I)


def _hits(rx, text) -> list[str]:
    return sorted({m.group(0).strip().lower() for m in rx.finditer(text)})


def _join(items) -> str:
    return ", ".join(sorted(items)) or "-"


def vram_check(request="") -> dict:
    """Is the local coder rung usable right now? Read-only probes; never loads a model (RESEARCH.md R6)."""
    out = {"ok": None, "installed": [], "free_mb": None, "note": ""}
    try:
        import requests
        from ishti import CONFIG
        base = CONFIG["llm"]["base_url"].rsplit("/v1", 1)[0]
        out["installed"] = [m.get("name", "") for m in
                            requests.get(f"{base}/api/tags", timeout=4).json().get("models", [])]
    except Exception as exc:
        out["note"] = f"could not list models ({type(exc).__name__}: {exc})"
        return out
    try:
        import subprocess
        r = subprocess.run(["nvidia-smi", "--query-gpu=memory.free,memory.total",
                            "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=8,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        free, total = (int(x) for x in r.stdout.strip().splitlines()[0].split(","))
        out["free_mb"], out["total_mb"] = free, total
    except Exception as exc:
        out["note"] = (out["note"] + "; " if out["note"] else "") + \
            f"could not read VRAM ({type(exc).__name__}: {exc})"
    # The coder rung comes from the LADDER (models_ladder.yaml via ishti.vram), never from "the first
    # model whose name contains coder" - that heuristic returned qwen2.5-coder-c8k:7b while the ladder's
    # rung 0 is ishti-qwen2.5-coder:14b (RESEARCH.md R6 S1).
    out["coder"] = ""
    out["coder_rungs"] = []
    try:
        from ishti import vram
        rungs = [r.get("name") for r in (vram.rungs("coder") or [])]
        out["coder_rungs"] = [n for n in rungs if n and vram.installed(n)]
        out["coder"] = out["coder_rungs"][0] if out["coder_rungs"] else ""
    except Exception as exc:
        out["note"] = (out["note"] + "; " if out["note"] else "") + \
            f"ladder unavailable ({type(exc).__name__}: {exc})"
        out["coder"] = next((m for m in out["installed"] if "coder" in m.lower()), "")
    # Can the governor place the coder right now? ishti.vram.plan() is read-only and answers with the real numbers
    # (measured footprints, the 80% budget, Ollama's headroom rule) and the steps it would take: unload an idle model,
    # step the planner down a rung, a lighter coder rung. A fixed "4096 MB free" threshold refused jobs the governor
    # could place (SC-23) and could not see that an idle model was about to be evicted.
    out["fits"], out["plan"] = None, []
    if out["coder"]:
        try:
            from ishti import vram
            p = vram.plan(out["coder"], role="coder")
            out["fits"] = p is not None
            out["plan"] = [f"{a.get('do')}: {a.get('model') or a.get('role')}" for a in (p or {}).get("actions", [])]
            if p is not None:
                out["need_mb"] = p.get("need_mb")
                out["coder_target"] = p["target"]["model"]
            else:
                out["note"] = (out["note"] + "; " if out["note"] else "") + vram.why_not(out["coder"])
        except Exception as exc:  # governor unreadable: degrade to a coarse free-memory check, and say so
            out["note"] = (out["note"] + "; " if out["note"] else "") +                 f"VRAM governor unavailable ({type(exc).__name__}); used a static free-memory check"
            out["fits"] = out["free_mb"] is None or out["free_mb"] >= 4096
    out["ok"] = bool(out["coder"]) and bool(out["fits"])
    if not out["note"]:
        out["note"] = (f"coder rung '{out['coder']}' installed, {out['free_mb']} MB free of "
                       f"{out.get('total_mb')} MB" + (f"; governor plan: {', '.join(out['plan'])}" if out["plan"] else "")
                       if out["coder"]
                       else "no coder rung installed from the ladder - code generation cannot run")
    return out


def feasibility(request, *, gpu=None) -> dict:
    """-> {possible, tier, missing, reasons, side_effects, credentials, money, sensitive, gpu}"""
    t = str(request or "").strip()
    gpu = gpu or vram_check(t)
    t3, cred, money = _hits(T3, t), _hits(CREDENTIAL, t), _hits(MONEY, t)
    sens, side_eff = _hits(SENSITIVE, t), _hits(SIDE_EFFECT, t)
    missing, reasons = [], []

    if t3:
        tier = "T3"
        missing.append(f"a blocked (T3) action: {_join(t3)} - ISHTI will not do this, and no "
                       f"confirmation unblocks it")
        reasons.append("T3 action detected - refused outright, not gated")
    elif cred:
        tier = "T2"
        missing.append(f"the credential for {', '.join(cred)} - you have not given me one and it is not "
                       f"stored locally, so there is nothing for me to authenticate with")
        reasons.append("credential required")
    elif money:
        tier = "T2"
        missing.append(f"a paid step ({', '.join(money)}) - I do not spend money without your explicit "
                       f"instruction, and no purchase is authorised here")
        reasons.append("money/purchase required")
    else:
        tier = "T2" if side_eff else "T1" if _hits(LOCAL_WRITE, t) else "T0"
        if sens:
            tier = "T2"
            reasons.append(f"reads sensitive data ({', '.join(sens)}) - owner confirmation required")

    if gpu.get("ok") is False:
        missing.append("a local coder model that is installed and fits in VRAM (" +
                       gpu.get("note", "") + ") - I will not offload part of a model onto the CPU")
        reasons.append("VRAM/model budget")

    return {"possible": tier != "T3" and not missing, "tier": tier, "missing": missing,
            "reasons": reasons, "side_effects": side_eff, "credentials": cred, "money": money,
            "sensitive": sens, "local_writes": _hits(LOCAL_WRITE, t), "gpu": gpu,
            "never_lower": "tier is never lowered automatically; only the owner can widen a permission"}


def refuse(feas) -> str:
    """The plain, honest refusal: what is missing, exactly. No hedging, no fake partial success."""
    if feas.get("possible"):
        return ""
    head = "I can't do that yet."
    if feas.get("tier") == "T3":
        head = "I won't do that - it's a blocked (T3) action."
    lines = [head, "", "Exactly what is missing:"]
    lines += [f"- {m}" for m in (feas.get("missing") or
                                 ["nothing specific is missing; the checks failed"])]
    if feas.get("reasons"):
        lines += ["", "Why, from the check: " + "; ".join(feas["reasons"]) + "."]
    lines += ["", "If you provide what is listed above (or tell me a different, allowed way to do it), "
                  "I can try again. I'd rather say this than pretend it worked."]
    return "\n".join(lines)



if __name__ == "__main__":
    # ---- offline self-check: no network, no model; only the keyword rules run -------------------
    gpu_ok = {"ok": True, "note": "coder rung installed", "free_mb": 8000, "total_mb": 16303}
    gpu_bad = {"ok": False, "note": "coder model absent"}

    # read-only / local work: not blocked, tier never over-claimed
    f = feasibility("convert a folder of images into a single PDF", gpu=gpu_ok)
    assert f["possible"] is True and f["tier"] in ("T0", "T1"), f
    assert f["missing"] == [], f

    # a T2 side effect is PERMITTED but gated - possible, tier T2, nothing "missing"
    f2 = feasibility("send this report on WhatsApp", gpu=gpu_ok)
    assert f2["tier"] == "T2" and f2["possible"] is True, f2
    assert f2["side_effects"], f2

    # a credential ISHTI does not have -> possible False, and refusal names it exactly
    f3 = feasibility("log in to my bank with the password", gpu=gpu_ok)
    assert f3["possible"] is False and f3["credentials"], f3
    assert "credential" in refuse(f3).lower(), refuse(f3)
    assert "Exactly what is missing:" in refuse(f3), refuse(f3)

    # a purchase -> blocked, naming the paid step
    f4 = feasibility("buy me a subscription for this service", gpu=gpu_ok)
    assert f4["possible"] is False and f4["money"], f4
    assert "paid step" in refuse(f4), refuse(f4)

    # T3 -> refused outright, and no confirmation language lets it through
    f5 = feasibility("place a live trade on my broker account", gpu=gpu_ok)
    assert f5["possible"] is False and f5["tier"] == "T3", f5
    assert "blocked (T3)" in refuse(f5), refuse(f5)

    # VRAM/model budget failure is its own named gap, never a silent retry
    f6 = feasibility("convert a folder of images into a single PDF", gpu=gpu_bad)
    assert f6["possible"] is False and any("VRAM" in m for m in f6["missing"]), f6

    # possible tasks refuse with an empty string (refusal is only for what cannot be done)
    assert refuse(f) == "", refuse(f)

    # the tier is never lowered automatically - the field says so, always
    assert "never lowered" in f2["never_lower"], f2["never_lower"]

    # the live probe works (read-only; it does NOT load a model)
    live = vram_check()
    assert isinstance(live, dict) and "installed" in live and "note" in live, live
    assert live["installed"], f"no models listed: {live}"
    print(f"[self-check] skill_creator.design OK - T3/credential/purchase refused with the exact gap "
          f"named, T2 gated not blocked, VRAM gap named, live probe found {len(live['installed'])} models "
          f"(coder rung: {live.get('coder')})")

