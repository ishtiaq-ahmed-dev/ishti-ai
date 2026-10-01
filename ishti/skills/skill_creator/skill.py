"""skill_creator - ISHTI's self-extending pipeline: research -> design -> build -> test -> do -> save.

When the owner asks for something no existing skill can do, ISHTI must neither refuse nor bluff. It
researches the approach, checks honestly whether it is possible and safe, designs it, builds it, tests
it in a sandbox, does the task, and saves a proper new skill - registered DISABLED, so it only ever runs
after the owner says yes.

This is the ORCHESTRATOR: it reuses what already exists instead of growing a second copy.
  gap detection     - ishti.skill_lifecycle.discover_gaps + ishti.router.route
  shape/validation  - ishti.skills.skillsmith.validate / scan / install_folder
  ownership gates   - ishti.skills.ask_first (T2) + ishti.test_mode.guarded (test safety)
  scope             - ishti.scope (one narrow exception for ishti/skills/_drafts)
  model + budget    - ishti.llm.ask(role="coder") under the VRAM governor (ishti.vram.admit)
  logging           - ishti.skills.trace + ishti.audit.record + log_need -> NEEDS.md
Stages live in their own modules so each is testable alone: gap, research, design, vetting, drafts,
sandbox. Architecture: IMPLEMENTATION_PLAN.md section 3 (WP-A).

Routing: `match()` covers ONLY capability-gap phrasings. Plain "create a skill that ..." stays with
skillsmith (the quick path); this skill is the full pipeline - and the self-check asserts that split so
the two can never steal each other's requests.
"""
from __future__ import annotations

import json
import os
import re

from ishti import ROOT, llm
from ishti.skills import ask_first, log_need, trace

from ishti.skills.skill_creator import design, drafts, gap, research, vetting

MINE = re.compile(r"\b(?:make|get)\s+yourself\s+(?:able\s+to|to)|"
                  r"\bteach\s+yourself\s+to\b|\bgive\s+yourself\s+(?:the\s+ability|the\s+skill)\s+to\b|"
                  r"\badd\s+(?:the\s+)?(?:ability|skill|capability)\s+to\b|"
                  r"\bcreate\s+a\s+new\s+capability\b|\byou\s+(?:still\s+)?can(?:'t|not)\s+|"
                  r"\byou\s+don'?t\s+know\s+how\s+to\b|\bskill\s*creator\b", re.I)
MAX_GENERATE_CALLS = 5          # RESEARCH.md R6: <=5 coder calls per skill, split anything larger
MAX_OUTPUT_TOKENS = 3500        # ... and <=3500 out-tokens per call
MAX_MODULE_LINES = 300          # one self-contained module per generation
MAX_TEST_ATTEMPTS = 8           # master prompt: 8 failed attempts on ONE issue -> stop and write NEEDS.md



# ------------------------------------------------------------------ stage: code generation
def _fence(text: str) -> str:
    """Strip the code fences models wrap code in, even when it sits inside JSON."""
    return re.sub(r"^\s*```(?:json|python)?\s*\n|\n?```\s*$", "", str(text or "").strip())


def generate(request, *, llm_fn=None, name=None, calls_limit=MAX_GENERATE_CALLS) -> dict:
    """Ask the local coder model for one module + a manifest. Injectable `llm_fn` keeps tests offline.

    -> {ok, calls, skill_py, manifest, reason}. Hard limits: <=calls_limit calls, <=MAX_OUTPUT_TOKENS
    each, <=MAX_MODULE_LINES lines (RESEARCH.md R6: a Q4 14B at 8k ctx can hold one module, not four).
    """
    name = name or drafts.name_for(request)
    example = os.path.join(ROOT, "ishti", "skills", "utilities", "skill.py")
    style = ""
    try:
        with open(example, encoding="utf-8") as f:
            style = f.read()[:2500]
    except OSError as exc:
        print(f"[skill_creator] no style example ({exc}) - continuing without one")

    def default_ask(prompt, **kw):
        return llm.ask(prompt, role="coder", system=None, temperature=kw.get("temperature", 0.1),
                       max_tokens=MAX_OUTPUT_TOKENS)

    ask = llm_fn or default_ask
    prompt = (
        "Write a new skill for ISHTI, a local Python assistant on Windows, that does: " + request + "\n\n"
        "A skill is ONE module exposing match(text) -> bool (a precise regex so it never steals other "
        "requests) and run(text, session) -> {\"reply\": str, \"sources\": []} or None, ending with an "
        "`if __name__ == \"__main__\":` self-check of asserts needing no network and no model. "
        "Standard library or requests only. No subprocess, no eval/exec, no writes outside its own "
        f"folder. Keep it under {MAX_MODULE_LINES} lines.\n\nStyle example:\n```python\n{style}\n```\n\n"
        "Return JSON {\"name\": \"<snake_case>\", \"description\": \"...\", \"route\": \"<when to use it>\", "
        "\"tier\": \"T0\"|\"T1\"|\"T2\", \"scope\": {\"write\": [], \"net\": [], \"apps\": false}, "
        "\"python_packages\": [], \"skill_py\": \"<the complete file>\"}.")

    code, spec, raw_last, problem = "", {}, "", "nothing usable came back"
    for attempt in range(max(1, calls_limit)):
        raw_last = _fence(str(ask(prompt, temperature=0.1 * attempt) or "").strip())
        try:
            spec = json.loads(raw_last)
            code = _fence(spec.get("skill_py", ""))
        except ValueError:
            # the model returned the file directly instead of JSON - take it, verify below
            spec = {"name": name, "description": request[:300], "route": request[:300]}
            code = raw_last
        if code and code.count("\n") <= MAX_MODULE_LINES and "def run(" in code and "def match(" in code:
            return {"ok": True, "calls": attempt + 1, "skill_py": code,
                    "manifest": {k: v for k, v in spec.items() if k != "skill_py"}}
        # remember WHY it was rejected, so the final message names the real problem, not just "failed"
        if not code:
            problem = "no code came back"
        elif code.count("\n") > MAX_MODULE_LINES:
            problem = (f"the module is {code.count(chr(10))} lines (limit {MAX_MODULE_LINES}) - split it "
                       f"into smaller skills instead of one huge file (RESEARCH.md R6)")
        else:
            missing = [w for w in ("def match(", "def run(") if w not in code]
            problem = f"the module is missing {' and '.join(missing)}"
        prompt = (f"That answer was not usable: {problem}. It must be JSON with a 'skill_py' key "
                  f"containing one module of at most {MAX_MODULE_LINES} lines that defines `def match` "
                  f"and `def run`. Previous answer:\n{raw_last[:1500]}\n\nReturn ONLY the JSON object.")
    return {"ok": False, "calls": calls_limit,
            "reason": f"the coder model never returned usable code after {calls_limit} attempt(s); "
                      f"the last problem was {problem}"}


# ------------------------------------------------------------------ stage: design doc
def write_design(request, feas, res, name, generated=None) -> str:
    """Write SKILL_DESIGN.md into the draft folder. Returns the path (stage 4 of the pipeline)."""
    folder = drafts.draft_dir(name)
    os.makedirs(folder, exist_ok=True)
    m = (generated or {}).get("manifest") or {}
    scope = m.get("scope") or {"write": ["data"], "net": [], "apps": False}
    lines = [f"# SKILL_DESIGN.md - {name}", "",
             f"Generated by ISHTI skill_creator for: *{request}*", "",
             "## Goal", "", str(m.get("description") or request)[:600], "",
             "## Inputs / outputs", "", "- Input: the owner's request text (`run(text, session)`).",
             "- Output: `{\"reply\": str, \"sources\": [...]}`.", "",
             "## Data flow", "", "1. route request -> 2. parse/act -> 3. reply.",
             "Research: see `research.md` in this folder "
             f"({len((res or {}).get('sources') or [])} source(s)).", "",
             "## Libraries chosen (pinned before install)", "",
             "| package | version | licence | advisories |", "|---|---|---|---|"]
    for pin in (m.get("python_packages") or []):
        lines.append(f"| {pin} | see vetting.json | see vetting.json | see vetting.json |")
    if not m.get("python_packages"):
        lines.append("| (none - standard library only) | - | PSF | - |")
    lines += ["", "Full checks in `vetting.json` (PyPI + OSV + release age + sha256), written by vetting.py.",
              "", "## Permission scope and tier", "",
              f"- Proposed tier: **{feas['tier']}** ({feas['never_lower']}).",
              f"- Scope: `{scope}`",
              f"- Side effects detected: {', '.join(feas['side_effects']) or 'none'}.",
              f"- Credentials required: {', '.join(feas['credentials']) or 'none'}.",
              f"- Money required: {', '.join(feas['money']) or 'none'}.", "",
              "## Failure modes, timeouts, tests", "",
              "- Sandbox: 120 s wall / 90 s CPU / 512 MB process / 8 processes (Job Object).",
              "- Failure modes: request not understood; dependency missing; local model unavailable.",
              "- Tests: `selftest.py`, 3 CONSECUTIVE green runs required before packaging.", "",
              "## Feasibility verdict", "", f"- Possible: **{feas['possible']}**",
              f"- Reasons: {', '.join(feas['reasons']) or '-'}",
              f"- GPU: {feas['gpu'].get('note', '-')}", ""]
    if feas["missing"]:
        lines += ["## What is missing", ""] + [f"- {x}" for x in feas["missing"]] + [""]
    with open(os.path.join(folder, "SKILL_DESIGN.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return os.path.join(folder, "SKILL_DESIGN.md")



# ------------------------------------------------------------------ stage: the pipeline
def build(request, session=None, *, llm_fn=None, research_fn=None, gap_fn=None) -> dict:
    """Run stages 1-7 of the pipeline. -> {"reply", "sources", "stage", "draft", "facts"}.

    Every stage is trace()d for the Thinking panel and audit()-logged. Refusals are explicit and name
    exactly what is missing; nothing is ever faked as a success. `llm_fn`, `research_fn` and `gap_fn`
    are injectable so the whole pipeline runs offline in tests - no model, no web, no Laya.
    """
    request = str(request or "").strip()
    name = drafts.name_for(request)
    facts: dict = {"request": request, "draft": name}
    from ishti.audit import context as audit_context
    with audit_context(skill="skill_creator", initiator="instructed", request=request[:300]):

        # 1. gap detection - at most ONE clarifying question
        trace(session, "skill_creator", "checking whether a skill already fits", status="start")
        g = gap_fn(request) if gap_fn else gap.detect_gap(request)
        facts["gap"] = {"gap": g["gap"], "reason": g["reason"], "clarify": g["clarify"]}
        trace(session, "skill_creator", g["reason"])
        if g["clarify"]:
            return {"reply": g["clarify"], "sources": [], "stage": "gap", "draft": "", "facts": facts}
        if not g["gap"]:
            who = g["route"].get("skill")
            return {"reply": f"'{who}' already handles that ({g['reason']}), so there is nothing for me "
                             f"to build.", "sources": [], "stage": "gap", "draft": "", "facts": facts}

        # 2. feasibility + risk - refuse plainly when it cannot be done (never fake success)
        trace(session, "skill_creator", "checking risk, credentials, money and the VRAM budget",
              status="start")
        feas = design.feasibility(request)
        facts["feasibility"] = {k: v for k, v in feas.items() if k != "gpu"}
        if not feas["possible"]:
            detail = "; ".join(feas["missing"])[:400]
            log_need("gap", "skill_creator", request, detail)
            from ishti import audit
            audit.record("refused to build a skill", detail, skill="skill_creator", tier="T0",
                         request=request[:200])
            trace(session, "skill_creator", "cannot be done - said so plainly", status="error")
            return {"reply": design.refuse(feas), "sources": [], "stage": "feasibility",
                    "draft": "", "facts": facts}

        # 3. research (T0, read-only, budgeted) - artifacts land in the draft folder
        trace(session, "skill_creator", "researching the approach (read-only)", status="start")
        res = research_fn(request, session) if research_fn else research.research(request, session)
        facts["research"] = {"sources": len(res.get("sources") or []),
                             "path": res.get("path", ""), "injections": len(res.get("injections") or []),
                             "note": res.get("note", "")}
        folder = drafts.new_draft(name, request)

        # 4. architecture doc
        write_design(request, feas, res, name)
        trace(session, "skill_creator", "wrote SKILL_DESIGN.md")

        # 5. implement (local coder model; budgets enforced inside generate())
        trace(session, "skill_creator", "writing the new skill with the coder model", status="start")
        gen = generate(request, llm_fn=llm_fn, name=name)
        facts["generate"] = {"ok": gen["ok"], "calls": gen.get("calls", 0),
                             "reason": gen.get("reason", "")}
        if not gen["ok"]:
            log_need("gap", "skill_creator", request, "code generation failed: " + gen["reason"][:200])
            trace(session, "skill_creator", "the coder model could not produce it", status="error")
            return {"reply": f"I couldn't build this one: {gen['reason']} I logged the gap in NEEDS.md "
                             f"with exactly what's missing rather than pretending it works.",
                    "sources": [], "stage": "implement", "draft": name, "facts": facts}
        with open(os.path.join(folder, "skill.py"), "w", encoding="utf-8") as f:
            f.write(gen["skill_py"])


        # 6. static scan + dependency vetting BEFORE anything is installed or run
        trace(session, "skill_creator", "scanning the code and vetting its dependencies", status="start")
        findings = _scan(folder, gen["skill_py"])
        facts["scan"] = findings
        vet = vetting.vet(gen["manifest"].get("python_packages") or [], src=gen["skill_py"],
                          folder=folder, draft_dir=folder)
        facts["vetting"] = {"ok": vet["ok"], "pinned": vet["pinned"], "blocked": vet["blocked"]}
        if not vet["ok"]:
            why = "; ".join(b.get("reason", "") for b in vet["blocked"][:3])[:300]
            log_need("gap", "skill_creator", request, "dependency blocked by vetting: " + why)
            trace(session, "skill_creator", "a dependency did not pass vetting", status="error")
            return {"reply": f"I didn't install anything - a dependency failed the pre-install checks: "
                             f"{why}. That's recorded in the draft's vetting.json and in NEEDS.md.",
                    "sources": [], "stage": "vetting", "draft": name, "facts": facts}
        limit = drafts.caps(name)
        if not limit["ok"]:
            return {"reply": f"I stopped before running it: {limit['reason']}", "sources": [],
                    "stage": "caps", "draft": name, "facts": facts}

        # 7. sandbox test loop: fix -> retest until 3 CONSECUTIVE greens, max 8 attempts on one issue
        trace(session, "skill_creator", "running it in the sandbox (3 consecutive green runs required)",
              status="start")
        attempt, last = 0, {"ok": False, "green": 0, "reason": "not run"}
        while attempt < MAX_TEST_ATTEMPTS:
            attempt += 1
            last = drafts.run_tests(name, runs=drafts.MAX_GREEN)
            if last["ok"] or not llm_fn or not last.get("runs"):
                if not last["ok"] and not llm_fn:
                    last["reason"] = (last.get("reason", "") +
                                      "; the coder model is unavailable so it cannot be fixed here")
                break
            err = " ".join((last["runs"][-1].get("tail") or ["(no output)"]))[:800]
            code = _read_code(folder)
            fixed = _fence(str(llm_fn(f"This generated ISHTI skill fails its own self-check:\n{err}\n\n"
                                      f"File:\n{code}\n\nReturn ONLY the corrected complete file."
                                      f"", temperature=0.0) or ""))
            if fixed and "def run(" in fixed and "def match(" in fixed:
                with open(os.path.join(folder, "skill.py"), "w", encoding="utf-8") as f:
                    f.write(fixed)
                trace(session, "skill_creator", f"fix attempt {attempt}/{MAX_TEST_ATTEMPTS}")
            else:
                last["reason"] = last.get("reason", "") + "; the fix itself was unusable"
                break
        facts["tests"] = {"attempts": attempt, "ok": last.get("ok", False),
                          "green": last.get("green", 0), "reason": last.get("reason", "")}
        if not last.get("ok"):
            log_need("gap", "skill_creator", request,
                     f"self-test never went green: {attempt} attempt(s), last error "
                     f"{str(last.get('reason', ''))[:160]}")
            trace(session, "skill_creator", "its test never passed - not installing it", status="error")
            return {"reply": f"I couldn't get it working: after {attempt} attempt(s) its own test still "
                             f"fails ({str(last.get('reason', ''))[:200]}). I did NOT install it; the "
                             f"diagnosis and exactly what's missing are in NEEDS.md.",
                    "sources": [], "stage": "test", "draft": name, "facts": facts}

        # 8. package as DISABLED, then the owner's explicit yes to install it (T2)
        pkg = drafts.package(name)
        if not pkg.get("ok"):
            return {"reply": f"I couldn't package it: {pkg.get('reason')}", "sources": [],
                    "stage": "package", "draft": name, "facts": facts}
        m = pkg["manifest"]
        facts["package"] = {"version": m.get("version"), "status": m.get("status"), "tier": m.get("tier")}
        scan_txt = "; ".join(sorted({f"{x.get('file')}: {x.get('detail')}" for x in findings}))[:300]
        hard = last.get("runs", [{}])[-1].get("hard_limits", "?")
        review = (f"**New skill: {name}** v{m.get('version')} - {m.get('description')}\n"
                  f"- Tier: {m.get('tier')} (scope: {m.get('scope')})\n"
                  f"- Dependencies pinned: {', '.join(vet['pinned']) or 'none (standard library only)'}\n"
                  f"- Scan: {len(findings)} finding(s)"
                  + (f": {scan_txt}" if findings else " (no risky calls)") + "\n"
                  f"- Tests: {last.get('green')} consecutive green runs in the sandbox "
                  f"({attempt} attempt(s)); hard limits enforced: {hard}\n"
                  f"- Research: {facts['research']['sources']} source(s) in research.md\n"
                  f"- Risks: {', '.join(feas['reasons']) or 'none detected'}\n\n"
                  f"Installing it registers it **DISABLED** - it will not run until you separately say "
                  f"\"enable the {name} skill\".")
        def _install():
            out = drafts.register(name)
            return out.get("message") or out.get("reason") or str(out)

        # ask_first returns only {"reply","sources"}; carry the audit facts through so the owner (and
        # the tests) can still see what was built, scanned, vetted and run before saying yes.
        gated = ask_first(session, "skill_creator", review + "\n\nInstall this new skill", _install)
        gated["stage"] = "gate"
        gated["draft"] = name
        gated["facts"] = facts
        return gated



# ------------------------------------------------------------------ helpers
def _read_code(folder) -> str:
    try:
        with open(os.path.join(folder, "skill.py"), encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


def _scan(folder, src="") -> list[dict]:
    """Two independent scanners merged: skillsmith's regex RISKY list and R3's stdlib `ast` table."""
    out = []
    try:
        # `from ishti.skills import skillsmith` gives the FOLDER (there is no __init__.py there), so the
        # module inside it must be named explicitly - otherwise .scan/.install_folder do not exist.
        from ishti.skills.skillsmith import skill as skillsmith
        out += [{"file": f, "line": 0, "kind": "regex", "detail": w}
                for f, w in skillsmith.scan(folder)]
    except Exception as exc:
        print(f"[skill_creator] skillsmith.scan unavailable ({type(exc).__name__}: {exc})")
    out += vetting.scan_python(src or _read_code(folder))
    seen, dedup = set(), []
    for x in out:
        key = (x.get("file"), x.get("line"), x.get("detail"))
        if key not in seen:
            seen.add(key)
            dedup.append(x)
    return dedup


# ------------------------------------------------------------------ owner-facing operations
ENABLE = re.compile(r"\b(?:enable|turn on|activate)\s+(?:the\s+)?(?P<name>[a-z][a-z0-9_]*)\s+skill\b", re.I)
DISABLE = re.compile(r"\b(?:disable|switch off|turn off)\s+(?:the\s+)?(?P<name>[a-z][a-z0-9_]*)\s+skill\b", re.I)
ROLLBACK = re.compile(r"\b(?:rollback|roll back|revert)\s+(?:the\s+)?(?P<name>[a-z][a-z0-9_]*)\s+skill\b", re.I)
STATUS = re.compile(r"\b(?:list|show)\s+(?:the\s+)?(?:drafts|pending skills|skills i created)\b|"
                    r"\bskill creator (?:status|report)\b", re.I)


def _op(op, m, session):
    name = (m.groupdict().get("name") or "").lower()
    trace(session, "skill_creator", op + (f" {name}" if name else ""))
    if op == "status":
        st = drafts._state()
        if not st:
            return {"reply": "No drafts yet - nothing has been built.", "sources": []}
        rows = [f"- {n}: {r.get('status', '?')}, {r.get('consecutive_green', 0)} green, "
                f"{r.get('failures', 0)} failure(s)" for n, r in sorted(st.items())]
        return {"reply": "Skill creator drafts:\n" + "\n".join(rows), "sources": []}
    if op == "disable":
        from ishti.skills import skillsmith as _  # noqa: F401  (registry import is done inside drafts)
        out = drafts.disable(name, "switched off by the owner")
        return {"reply": out.get("message") or out.get("reason") or str(out), "sources": []}
    if op == "enable":
        question = f"Enable the {name} skill now (it will start handling its requests)?"
        return ask_first(session, "skill_creator", question, lambda: _enable(name))
    question = f"Roll the {name} skill back to its previous version?"
    return ask_first(session, "skill_creator", question, lambda: _rollback(name))


def _enable(name) -> str:
    from ishti.test_mode import guarded
    out = guarded("skill_creator", f"enable the {name} skill", lambda: drafts.enable(name), tier="T2")
    if isinstance(out, dict):
        return out.get("reply", "")
    return str(out.get("message") or out.get("reason") or out)


def _rollback(name) -> str:
    from ishti.test_mode import guarded
    out = guarded("skill_creator", f"roll the {name} skill back", lambda: drafts.rollback(name), tier="T2")
    if isinstance(out, dict):
        return out.get("reply", "")
    return str(out.get("message") or out.get("reason") or out)


def match(text):
    """Capability gaps ONLY, plus the three admin verbs this skill alone owns.

    `skillsmith` keeps the plain "create a skill that ..." phrasing - asserted in the self-check, so the
    two can never take each other's request.
    """
    t = str(text or "")
    if any(rx.search(t) for rx in (ENABLE, DISABLE, ROLLBACK, STATUS)):
        return True
    return bool(MINE.search(t))


def run(text, session):
    for rx, op in ((ENABLE, "enable"), (DISABLE, "disable"), (ROLLBACK, "rollback"), (STATUS, "status")):
        m = rx.search(str(text or ""))
        if m:
            return _op(op, m, session)
    out = build(text, session)
    return out if isinstance(out, dict) else {"reply": str(out), "sources": []}



if __name__ == "__main__":
    import shutil
    import tempfile

    # ---- 1. the routing split: capability gaps are OURS, skillsmith's plain phrasing is NOT ----------
    assert match("make yourself able to turn a folder of images into one PDF")
    assert match("teach yourself to extract tables from a PDF")
    assert match("add the ability to watch a price and tell me when it changes")
    assert match("you can't do that yet")
    assert match("skill creator")
    assert not match("create a skill that converts files"), "skillsmith owns 'create a skill that'"
    assert not match("learn to convert roman numerals"), "skillsmith owns 'learn to'"
    assert not match("what is the weather tomorrow in Peshawar?"), "must never steal ordinary requests"
    assert match("enable the weather skill") and match("disable the weather skill")
    assert match("rollback the notes skill") and match("list drafts")

    # ---- 2. generate(): injectable coder, hard limits enforced ------------------------------------
    good_code = ("def match(text):\n    return False\n\n\n"
                 "def run(text, session):\n    return {'reply': 'ok', 'sources': []}\n\n\n"
                 "if __name__ == '__main__':\n    assert run('x', {})['reply'] == 'ok'\n"
                 "    print('[self-check] demo OK')\n")
    good = json.dumps({"name": "demo", "description": "d", "route": "r", "tier": "T0",
                       "scope": {"write": [], "net": [], "apps": False}, "python_packages": [],
                       "skill_py": good_code})
    g = generate("demo task", llm_fn=lambda prompt, **kw: good, name="demo_gen")
    assert g["ok"] and g["calls"] == 1 and "def run(" in g["skill_py"], g

    g2 = generate("demo task", llm_fn=lambda prompt, **kw: "I refuse to write code.", name="demo_gen",
                  calls_limit=3)
    assert g2["ok"] is False and g2["calls"] == 3, g2          # gives up instead of looping forever

    over = json.dumps({"name": "big", "description": "d", "route": "r", "tier": "T0",
                       "scope": {"write": [], "net": [], "apps": False}, "python_packages": [],
                       "skill_py": "def match(text):\n    return False\n" + "\n".join(
                           f"# filler {i}" for i in range(MAX_MODULE_LINES)) +
                           "\ndef run(text, session):\n    return None\n"})
    g3 = generate("big", llm_fn=lambda prompt, **kw: over, name="demo_gen")
    assert g3["ok"] is False and "lines" in g3["reason"], g3    # too big -> split, not accepted


    # ---- 3. the whole pipeline, offline: no model, no web, no Laya, no install ------------------
    base = tempfile.mkdtemp(prefix="ishti_sc_")
    drafts.FOLDER = os.path.join(base, "_drafts")               # drafts stay in a temp tree
    real_vram = design.vram_check
    design.vram_check = lambda *a, **k: {"ok": True, "note": "stubbed for the self-check",
                                         "installed": ["stub"], "coder": "stub", "free_mb": 8000}
    session: dict = {}
    gap_stub = lambda req: {"gap": True, "reason": "forced for the self-check", "clarify": None,
                            "route": {"skill": None, "confidence": 0.0, "source": "rule"},
                            "recurring": []}
    res_stub = lambda req, sess=None: {"ok": True, "sources": [{"url": "https://example.com/lib"}],
                                       "queries": [], "path": "", "injections": [], "note": ""}
    try:
        out = build("combine a folder of images into a single PDF", session,
                    llm_fn=lambda prompt, **kw: good, research_fn=res_stub, gap_fn=gap_stub)
        assert out.get("facts", {}).get("tests", {}).get("ok") is True, out
        assert out["facts"]["tests"]["green"] == drafts.MAX_GREEN, out["facts"]["tests"]
        assert out["facts"]["vetting"]["ok"] is True, out["facts"]["vetting"]

        folder = drafts.draft_dir(out["draft"])
        for f in ("manifest.json", "SKILL_DESIGN.md", "skill.py", "vetting.json"):
            assert os.path.isfile(os.path.join(folder, f)), f

        # packaged but NOT enabled: v0.1.0 and DISABLED
        pkg = json.load(open(os.path.join(folder, "manifest.json"), encoding="utf-8"))
        assert pkg["version"] == "0.1.0" and pkg["status"] == "disabled", pkg
        assert pkg["author"] == "ISHTI skill_creator", pkg

        # the owner's explicit yes is required before installation. The gate is pending and this
        # self-check deliberately does NOT run it: it would copy into the real ishti/skills tree.
        assert "pending" in session, session
        assert "DISABLED" in out["reply"] and "**New skill:" in out["reply"], out["reply"][:200]
        assert "enable the" in out["reply"], out["reply"][:200]

        # a T3 request is refused by name, and nothing is created
        session2: dict = {}
        bad_out = build("place a live trade on my broker account", session2,
                        llm_fn=lambda prompt, **kw: good, research_fn=res_stub, gap_fn=gap_stub)
        assert bad_out["stage"] == "feasibility" and "blocked (T3)" in bad_out["reply"], bad_out
        assert "pending" not in session2, session2

        # an existing skill is left alone: no build, no draft
        notgap = dict(gap_stub(""))
        notgap.update({"gap": False, "reason": "'files' already owns this (rule, confidence 1.0)",
                       "route": {"skill": "files", "confidence": 1.0, "source": "rule"}})
        ok_out = build("make yourself able to do something files already does", {},
                       llm_fn=lambda prompt, **kw: good, research_fn=res_stub,
                       gap_fn=lambda req: notgap)
        assert ok_out["stage"] == "gap" and ok_out["draft"] == "", ok_out
        assert "already handles" in ok_out["reply"], ok_out["reply"]
    finally:
        design.vram_check = real_vram
        shutil.rmtree(base, ignore_errors=True)

    print("[self-check] skill_creator OK - routing split with skillsmith, coder budget limits enforced "
          "(call cap + line cap), full pipeline offline (research -> design -> generate -> scan -> vet "
          "-> 3 green sandbox runs -> packaged DISABLED with the owner's yes still pending), T3 refused "
          "by name, existing skill left alone, nothing installed into the real skill tree")

