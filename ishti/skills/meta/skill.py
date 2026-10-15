"""About ISHTI itself: skill health (runs every skill's self-check), what it has logged as missing (the needs log), what it
accessed/did recently (permission audit from the route log), which skills get used most."""
import concurrent.futures as cf
import datetime as dt
import json

try:  # best-effort failure log (never breaks the module if logging is unavailable)
    from ishti.core.logutil import swallowed
except Exception:  # pragma: no cover
    def swallowed(*_a, **_k):
        return None
import os
import re
import subprocess
import sys
import time

from ishti import ROOT
from ishti.skills import needs_path, trace
from ishti.test_mode import scratch_path

I = re.I
# Section 33 (checklist 201/202/205): questions about ISHTI's own previous turn. Answered from the
# recorded route decision and action audit, never by asking a model to describe what it probably did -
# a guessed trajectory is exactly the failure these checks look for.
EXPLAIN = re.compile(r"\bwhich\s+skill\s+(?:handled|answered|took|ran|did|was)\b|\bwhich\s+skill\s+was\s+used\b|"
                     r"\bwhat\s+steps?\s+(?:did|do)\s+you\b|\bwhy\s+did\s+you\s+(?:choose|pick|use|open|run|go\s+with)\b|"
                     r"\bhow\s+did\s+you\s+(?:find|locate|get|do)\s+that\b|\bwhat\s+were\s+you\s+doing\s+just\s+now\b", I)
OPS = [
    ("health", re.compile(r"\b(?:skill\s+health|check\s+(?:your|all|the)\s+skills|are\s+(?:all\s+)?(?:your|the)\s+skills\s+(?:ok|working)|self[-\s]?test|diagnose\s+(?:your|the)\s+skills)\b", I)),
    ("needs", re.compile(r"\b(?:what\s+(?:have\s+you|did\s+you)\s+(?:flag|log)(?:ged)?|what(?:'s| is)\s+missing|needs\.md|capability\s+gaps?|what\s+can'?t\s+you\s+do)\b", I)),
    ("audit", re.compile(r"\b(?:what\s+have\s+you\s+(?:accessed|done|touched)|permission\s+audit|activity\s+log|action\s+log|audit\s+log|"
                         r"what\s+did\s+you\s+do\s+(?:today|recently|yesterday|while\s+i\s+was\s+(?:away|gone|out|asleep))|"
                         r"(?:show|list)\s+(?:me\s+)?(?:today'?s|yesterday'?s|your|all)\s+actions|what\s+(?:actions|things)\s+did\s+you\s+(?:do|take))\b", I)),
    ("tasks", re.compile(r"\b(?:status\s+of\s+(?:my|the|all|your)\s+(?:tasks?|requests?|jobs?)|(?:tasks?|jobs?)\s+status|"
                         r"what\s+are\s+you\s+(?:still\s+)?(?:working\s+on|doing)|how\s+are\s+my\s+(?:tasks?|requests?)|"
                         r"(?:are|is)\s+(?:my|the)\s+(?:tasks?|requests?)\s+(?:done|finished|ready))\b", I)),
    ("pkg_update", re.compile(r"\b(?:update|upgrade)\s+(?:ishti(?:'s)?\s+)?(?:python\s+)?(?:package|pip\s+package|dependency)\s+(?P<pkg>[\w.-]+)|"
                              r"\b(?:update|upgrade)\s+(?:all\s+)?(?:of\s+)?ishti(?:'s)?\s+(?:python\s+)?(?:packages|dependencies)\b", re.I)),
    ("pkg_outdated", re.compile(r"\b(?:outdated|old)\s+(?:python\s+|pip\s+)?packages\b|\b(?:check|any)\s+(?:ishti(?:'s)?\s+)?(?:python\s+|pip\s+)"
                                r"(?:packages?\s+)?(?:for\s+)?updates\b|\b(?:check\s+)?ishti(?:'s)?\s+(?:python\s+)?packages\s+for\s+updates\b", re.I)),
    ("confirm_profile", re.compile(r"\bconfirm\s+(?:my\s+|the\s+)?(?:owner\s+)?(?:profile|details)\b", I)),
    ("explain", EXPLAIN),
    ("usage", re.compile(r"\b(?:which|what)\s+skills?\s+(?:do\s+i|did\s+i)\s+use\s+(?:the\s+)?most\b|\bskill\s+usage\b", I)),
    # Self-knowledge (WP-B): identity, live machine facts, model inventory, skill inventory, and the
    # refresh command. Deliberately does NOT take "what can't you do?" - that already answers from
    # the needs log (op_needs) and two skills must not fight over one phrasing.
    ("self_check", re.compile(
        r"\b(?:what\s+(?:are\s+your|is\s+your)\s+(?:specs?|specifications|setup|machine|system)|"
        r"(?:your|this)\s+(?:pc|machine|system)\s+(?:specs?|specifications)|"
        r"what\s+(?:gpu|graphics\s+card|cpu|processor|ram|memory|vram)\b|"
        r"how\s+much\s+(?:ram|memory|disk|vram|space)\s+(?:do\s+(?:i|you)\s+have|have\s+(?:i|you))|"
        r"which\s+models\s+(?:are\s+you|do\s+you)\s+(?:using|have|installed)|"
        r"what\s+skills?\s+(?:do\s+you\s+have|have\s+you\s+got)|"
        r"who\s+are\s+you\b|whose\s+assistant\s+are\s+you\b|"
        r"what\s+(?:pc|computer|laptop|system|machine)\s+are\s+you|"
        # Urdu (Arabic script), Pashto and Roman-Urdu: the dispatcher translates before routing, but a
        # deterministic trigger is cheaper and works even when the translator model is busy or unavailable.
        # No trailing \b on this group: Pashto/Urdu plurals attach suffixes (ما[ڈډ]?لونه) and \b is ASCII-only.
        r"تم\s*کون\s*ہو|کمپیوٹر.{0,20}(?:سپیفیکیشن|سپیکس|تفصیل)|گرافکس\s*کارڈ|"
        r"جی\s*پی\s*یو|کون\s*کون\s*سی?\s*ما[ڈډ]?ل|کون\s*کون\s*سی?\s*(?:اسکل|مہارتیا)|کیا\s*نہیں\s*کر\s*سکتے|"
        r"ته\s*ول\s*څوک\s*یې|کمپیوټر.{0,20}(?:ډول|ځانګړی)|ګرافیکس\s*کارډ|کوم\s*ډول\s*ما[ڈډ]?ل|"
        r"څه\s*مهارتونه\s*لرې|څه\s*نه\s*شې\s*کولی|"
        r"tum\s*(?:kaun|kon)\s*ho|mer[ei]\s*(?:computer|pc|laptop)\s*(?:ki|ka)\s*(?:specs?|kaisi)|"
        r"mer[ei]\s*(?:graphics|gpu|vram)\s*(?:card|kitab)|kaun\s*kaun\s*si\s*(?:models?|skills?)|"
        r"tum(?:hare)?\s*(?:kya|kyun)\s*(?:nahi|na)\s*kar\s*sakte|"
        r"(?:refresh|update|reload)\s+(?:your\s+)?self[-\s]?knowledge)", I)),
]


def match(text):
    return any(rx.search(text) for _, rx in OPS)


def run(text, session):
    for name, rx in OPS:
        if rx.search(text):
            trace(session, "meta", name)
            out = globals()["op_" + name](text, session)
            return out if isinstance(out, dict) else {"reply": out, "sources": []}  # a yes/no question is already a reply
    return None


_health_cache = {}   # mtime-sum -> (ts, reply); FIX-10: 72 s per call, no cache, re-run 4x per suite
_HEALTH_DISK = scratch_path(os.path.join(ROOT, "data", "cache", "meta_health.json"))  # Cycle 4: survives a restart


def _health_key():
    """The cache key for op_health: a hash over every skill.py's mtime (+ the file list). Identical
    sources cannot produce a different result, so the same key means the same report."""
    import hashlib
    from ishti.dispatcher import SKILLS  # lazy: the dispatcher imports the skills that import this module
    here = os.path.join(ROOT, "ishti", "skills")
    names = [n for n in SKILLS if os.path.isfile(os.path.join(here, n, "skill.py"))
             and "__main__" in open(os.path.join(here, n, "skill.py"), encoding="utf-8").read()]
    stamp = hashlib.sha256()
    for n in sorted(names):
        stamp.update(str(os.path.getmtime(os.path.join(here, n, "skill.py"))).encode())
    return here, names, stamp.hexdigest()


def _health_disk_get(key):
    """A ~72-80 s full pass must not run again in a NEW process when nothing changed (Cycle 4: the
    FIX-10 cache was in-memory only, so the first `check your skills` after every restart was the
    slowest request in the whole system). Same key = identical skill sources; same 900 s TTL."""
    try:
        with open(_HEALTH_DISK, encoding="utf-8") as f:
            d = json.load(f)
        if d.get("key") == key and time.time() - float(d.get("ts", 0)) < 900 and d.get("reply"):
            return str(d["reply"])
    except (OSError, ValueError, TypeError) as exc:  # no usable cached health reply: the check runs again
        swallowed("ishti/skills/meta/skill.py:health_disk_get", exc)
    return None


def _health_disk_put(key, reply):
    try:
        os.makedirs(os.path.dirname(_HEALTH_DISK), exist_ok=True)
        tmp = _HEALTH_DISK + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:  # atomic replace: a crash never leaves a half file
            json.dump({"key": key, "ts": time.time(), "reply": reply}, f, ensure_ascii=False)
        os.replace(tmp, _HEALTH_DISK)
    except OSError as exc:  # the cache is an optimisation: a failed write only costs a re-check
        swallowed("ishti/skills/meta/skill.py:health_disk_put", exc)


def op_health(text, session):
    from ishti.dispatcher import SKILLS  # lazy: the dispatcher imports the skills that import this module
    here, names, key = _health_key()
    trace(session, "selfcheck", f"running {len(names)} skill self-checks", status="start")

    # FIX-10: a full pass costs ~72 s. Cache on the CONTENT of the skill files: identical
    # sources cannot produce a different result, so repeat calls (the generated suite fires
    # this row 4x; users repeat too) return instantly. Any skill.py edit changes the key.
    if key in _health_cache and time.time() - _health_cache[key][0] < 900:
        ts, cached = _health_cache[key]
        trace(session, "selfcheck", "cached (skills unchanged)", status="done")
        return cached
    disk = _health_disk_get(key)   # Cycle 4: also across restarts - the key proves nothing changed
    if disk:
        _health_cache[key] = (time.time(), disk)
        trace(session, "selfcheck", "cached (skills unchanged since the last run)", status="done")
        return disk

    def check(n):
        t0 = time.time()
        try:
            p = subprocess.run([sys.executable, "-m", f"ishti.skills.{n}.skill"], cwd=ROOT, capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=240, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            err = [l for l in p.stderr.splitlines() if re.search(r"Error|assert", l)]
            return n, p.returncode == 0, (err[-1][:120] if err else ""), time.time() - t0
        except subprocess.TimeoutExpired:
            return n, False, "timed out", time.time() - t0
    with cf.ThreadPoolExecutor(4) as pool:
        results = sorted(pool.map(check, names), key=lambda r: (r[1], r[0]))
    broken = {n: s.problem for n, s in SKILLS.items() if s.status == "broken"}
    ok = sum(r[1] for r in results)
    trace(session, "selfcheck", f"{ok}/{len(results)} passed", status="done" if ok == len(results) else "error")
    lines = [f"- {'✓' if good else '✗'} {n} ({secs:.0f} s)" + (f": {why}" if why else "") for n, good, why, secs in results]
    lines += [f"- ✗ {n} didn't load: {p}" for n, p in broken.items()]
    reply = f"Skill health: {ok}/{len(results)} self-checks pass, {len(SKILLS) - len(broken)}/{len(SKILLS)} skills loaded.\n" + "\n".join(lines)
    _health_cache[key] = (time.time(), reply)
    _health_disk_put(key, reply)  # Cycle 4: a restart must not pay the ~72 s pass again
    return reply


def op_tasks(text, session):
    """This chat's task tracker (kept by the dispatcher): what is running, what finished, with short results."""
    now = time.time()
    tasks = [t for t in session.get("tasks") or [] if not dict(OPS)["tasks"].search(t["topic"])]  # not these questions
    if not tasks:
        return "Nothing running - no other tasks in this chat."
    icon = {"running": "…", "done": "✓", "failed": "✗", "cancelled": "–"}
    lines = [f"- {icon[t['status']]} {t['topic']} - " + (f"running for {now - t['started']:.0f} s" if t["status"] == "running"
             else f"{t['status']}: {' '.join(t['result'].split())[:120]}") for t in tasks]
    busy = sum(t["status"] == "running" for t in tasks)
    return f"{busy} running, {len(tasks) - busy} finished:\n" + "\n".join(lines)


# ---------------------------------------------------------------- self-update (ISHTI's own venv)
def _pip(*args, timeout=900):
    return subprocess.run([sys.executable, "-m", "pip", *args, "--disable-pip-version-check"], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def outdated():
    """[(name, installed, latest)] in ISHTI's own environment."""
    p = _pip("list", "--outdated", "--format", "json")
    return [(d["name"], d["version"], d["latest_version"]) for d in json.loads(p.stdout or "[]")]


def healthy():
    """ISHTI's own full check (routing + core loop); (ok, last line)."""
    p = subprocess.run([sys.executable, "-m", "ishti.check"], cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=1800, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return p.returncode == 0 and "core loop OK" in p.stdout, (p.stdout.strip().splitlines() or [""])[-1][:160]


def update(pkgs, check=healthy):
    """Upgrade, verify with ISHTI's own checks, roll everything back if they fail. Returns the reply."""
    before = {n: v for n, v, _ in outdated() if n.lower() in {p.lower() for p in pkgs}}
    if not before:
        return "Nothing to update - " + (", ".join(pkgs) if len(pkgs) < 6 else f"{len(pkgs)} packages") + " already current."
    p = _pip("install", "--upgrade", *before)
    if p.returncode:
        return f"pip couldn't upgrade them: {(p.stderr or p.stdout).strip()[-200:]}"
    ok, detail = check()
    if ok:
        return "Updated and verified (ISHTI's checks pass): " + ", ".join(f"{n} {v} -> new" for n, v in before.items())
    _pip("install", *[f"{n}=={v}" for n, v in before.items()])
    return f"The update broke ISHTI's checks ({detail}) - rolled back to " + ", ".join(f"{n} {v}" for n, v in before.items()) + "."


def op_pkg_outdated(text, session):
    trace(session, "pip", "checking ISHTI's packages", status="start")
    rows = outdated()
    trace(session, "pip", f"{len(rows)} outdated", status="done")
    if not rows:
        return "All of ISHTI's Python packages are up to date."
    return (f"{len(rows)} of ISHTI's packages have updates:\n" + "\n".join(f"- {n}: {v} -> {l}" for n, v, l in rows[:30])
            + "\nSay \"update ISHTI package <name>\" (I test ISHTI after and roll back if it breaks).")


def op_pkg_update(text, session):
    from ishti.skills import ask_first
    m = dict(OPS)["pkg_update"].search(text)
    pkgs = [m["pkg"]] if m["pkg"] else [n for n, _, _ in outdated()]
    if not pkgs:
        return "All of ISHTI's Python packages are up to date."
    what = pkgs[0] if len(pkgs) == 1 else f"{len(pkgs)} packages"
    return ask_first(session, "meta", f"Upgrade {what} in ISHTI's environment, then run ISHTI's checks (a few minutes) and "
                                      "roll back if anything breaks", lambda: update(pkgs))


def op_needs(text, session):
    path = needs_path()  # data/needs_runtime.md - the one runtime gap log (ishti.skills.log_need writes it)
    try:
        with open(path, encoding="utf-8") as f:
            body = f.read()
    except OSError:
        return "Nothing logged as missing."
    logged = re.findall(r"^- (\d{4}-\d{2}-\d{2} \d{2}:\d{2}) \[(\w+)\] (.+)$", body, re.M)
    if not logged:
        return "Nothing logged as missing."
    rows = "\n".join(f"- {t} [{k}] {d[:140]}" for t, k, d in logged[::-1][:12])
    return "Logged while working (newest first):\n" + rows


def _routes(days):
    from ishti.facts import get_facts
    f = get_facts()
    with f.lock:
        return f.db.execute("SELECT ts, text, skill, source FROM route_log WHERE ts>=? ORDER BY ts DESC",
                            (time.time() - days * 86400,)).fetchall()


def op_audit(text, session):
    """The action audit log (ishti/audit.py): every action with who initiated it and when T2 ones were confirmed."""
    from ishti.audit import report
    return report(text)


def op_usage(text, session):
    rows = _routes(30)
    counts = {}
    for _, _, skill, src in rows:
        if skill and src != "escalation":
            counts[skill] = counts.get(skill, 0) + 1
    top = sorted(counts.items(), key=lambda kv: -kv[1])[:12]
    return (f"Skills used in the last 30 days ({len(rows)} requests):\n" + "\n".join(f"- {n}: {c}" for n, c in top)) if top else "No usage yet."


def _explained_row():
    """The owner's previous routed request (route_log) with this turn's own question skipped."""
    from ishti.facts import get_facts
    f = get_facts()
    with f.lock:
        rows = f.db.execute("SELECT ts, text, skill, confidence, source, detail FROM route_log "
                            "ORDER BY ts DESC LIMIT 25").fetchall()
    return next((r for r in rows if r[1] and not EXPLAIN.search(str(r[1]))), None)


def _steps_for(request: str, limit: int = 6) -> list[str]:
    """What actually ran for that request, from the action audit (real calls, not a description)."""
    from ishti import audit
    try:
        rows = audit.query(limit=400)
    except Exception:  # noqa: BLE001 - a missing audit db must not break the answer
        return []
    out = []
    for _ts, _who, skill, action, _detail, tier, _conf, req, _chan in rows:
        if str(req or "").strip() != request.strip() or str(action or "").startswith("request:"):
            continue
        label = f"{skill or 'ishti'}: {action}"[:150]
        if label not in out:
            out.append(label)
    return out[::-1][:limit]


def op_explain(text, session):
    """"which skill handled that", "what steps did you take", "why did you choose X" - from the record."""
    t = (text or "").lower()
    row = _explained_row()
    if not row:
        return {"reply": "Nothing is recorded yet - ask me something first and I'll explain exactly how I handled it.",
                "sources": []}
    _ts, req, skill, _conf, source, detail = row[0], str(row[1]), row[2], row[3], str(row[4] or ""), row[5]
    why = ""
    try:
        why = str((json.loads(detail or "{}") or {}).get("why") or "")
    except (TypeError, ValueError) as exc:  # an unreadable detail just has no reason text
        swallowed("ishti/skills/meta/skill.py:explain_detail", exc)
    head = (f'Your last request was "{req[:140]}" - it went to the **{skill or "dispatcher"}** skill '
            f'(decided by: {source or "rule"}{(": " + why) if why else ""}).')

    named = re.search(r"\b(?:choose|pick|use|open|run|go\s+with)\s+(?P<what>[\w .+-]{2,40}?)"
                      r"(?:\s+(?:just\s+now|instead|over|rather|and\s+not|for\s+that)|[?.!,]|$)", t)
    if named:
        what = named.group("what").strip()
        ran = str(req).lower()
        used = what.lower() in ran or what.lower() in str(skill or "").lower()
        # the honest form: name the tool that really ran, and correct the premise when it did not
        return {"reply": head + (f" It did use {what}. " if used else
                f" I didn't use {what} for it - no such tool ran, so there is nothing to compare. ") +
                "That decision comes only from what each skill declares it can do, so I can't claim a "
                "reason that isn't in the record.", "sources": []}

    steps = _steps_for(req)
    body = ("\nSteps actually recorded for it:\n" + "\n".join(f"- {s}" for s in steps)) if steps else \
        "\nNo separate tool steps were logged for it."
    return {"reply": head + body, "sources": []}


def op_confirm_profile(text, session):
    """"confirm my profile": show the details ISHTI holds about the owner and, on a yes, record that they are right.
    Until then ISHTI says they are unconfirmed and never states them as fact (owner_profile.yaml `confirmed: false`)."""
    from ishti import selfmodel as sm
    from ishti.skills import ask_first
    prof = sm.profile()
    if prof.get("confirmed"):
        return "Your owner details are already confirmed."
    owner = prof.get("owner") or {}
    shown = "; ".join(f"{k}: {v}" for k, v in owner.items() if v) or "nothing recorded yet"
    return ask_first(session, "meta", f"These are the details I hold about you - {shown}. Mark them as correct (edit "
                                      "owner_profile.yaml first if something is wrong)",
                     lambda: "Done - I will treat your owner details as confirmed." if sm.confirm_profile() else "Done.")


REFRESH = re.compile(r"\b(?:refresh|update|reload)\s+(?:your\s+)?self[-\s]?knowledge\b", I)


def op_self_check(text, session=None):
    """Self-knowledge: the measured detail view, or a forced refresh ("refresh your self-knowledge").

    Answers from `ishti.selfmodel`, which measures this PC live - so "what GPU do I have" cannot be
    answered from memory. Local only: this text is never sent anywhere.
    """
    from ishti import selfmodel as sm
    if REFRESH.search(str(text or "")):
        out = sm.refresh()
        changed = "Something changed and I've re-measured everything." if out["changed"] \
            else "I re-measured everything; nothing relevant had changed."
        lines = [changed, f"Measured {out['measured_at']} (fingerprint {out['fingerprint']}).", ""]
        if out["mismatch"]:
            lines.append("Note: this machine disagrees with the recorded baseline - " +
                         "; ".join(out["mismatch"]))
            lines.append("")
        lines.append(out["render"])
        return {"reply": "\n".join(lines), "sources": []}
    return {"reply": sm.detail(), "sources": []}


if __name__ == "__main__":
    for t, want in {"check your skills": "health", "what have you flagged as missing": "needs", "what have you accessed recently": "audit", "what did you do while I was away": "audit",
                    "show me today's actions": "audit",
                    "confirm my profile": "confirm_profile",
                    "which skills do I use the most": "usage", "what's the status of my tasks": "tasks",
                    "what are you working on": "tasks", "check ishti's python packages for updates": "pkg_outdated",
                    "any outdated python packages": "pkg_outdated", "update ishti package requests": "pkg_update",
                    "update all of ishti's packages": "pkg_update",
                    "what are your specs": "self_check", "what GPU do I have": "self_check",
                    "which models are you using": "self_check", "what skills do you have": "self_check",
                    "who are you": "self_check", "whose assistant are you": "self_check",
                    "refresh your self-knowledge": "self_check",
                    "how much RAM do I have": "self_check"}.items():
        assert next((n for n, rx in OPS if rx.search(t)), None) == want, t
    assert not match("check the weather")
    # "what can't you do?" must stay with op_needs (it answers from the needs log) - two skills must not
    # fight over one phrasing
    assert next((n for n, rx in OPS if rx.search("what can't you do")), None) == "needs"
    now = time.time()
    tracked = {"tasks": [{"topic": "compare laptops", "status": "running", "started": now - 12, "result": ""},
                         {"topic": "weather tomorrow", "status": "done", "started": now - 30, "result": "33°C,\n clear"},
                         {"topic": "status of my tasks", "status": "running", "started": now, "result": ""}]}
    out = op_tasks("status of my tasks", tracked)
    assert out.startswith("1 running, 1 finished") and "compare laptops - running for 12 s" in out and "33°C, clear" in out, out
    calls = []
    fake_rows = [("demo-pkg", "1.0", "2.0")]
    real_outdated, real_pip = outdated, _pip
    outdated = lambda: fake_rows
    _pip = lambda *a, **k: calls.append(a) or type("P", (), {"returncode": 0, "stdout": "", "stderr": ""})()
    assert "rolled back to demo-pkg 1.0" in update(["demo-pkg"], check=lambda: (False, "routing: 90/125"))
    assert calls[-1] == ("install", "demo-pkg==1.0")  # the old version went back
    assert update(["demo-pkg"], check=lambda: (True, "ok")).startswith("Updated and verified")
    assert update(["other"]).startswith("Nothing to update")
    outdated, _pip = real_outdated, real_pip
    assert isinstance(outdated(), list)  # the real pip query works

    # self-knowledge: measured live, and the owner's identity never rides along outbound
    detail = op_self_check("what are your specs")["reply"]
    assert "Measured" in detail, detail[:200]
    assert "RTX 5070 Ti" in detail, detail[:400]
    refreshed = op_self_check("refresh your self-knowledge")["reply"]
    assert "re-measured" in refreshed, refreshed[:200]
    from ishti import selfmodel as _sm
    assert _sm.assert_local_only("what is the weather in Peshawar") == []
    print("[self-check] meta OK - routing, task tracker, self-update with rollback, self-knowledge from "
          "live measurements + refresh")
