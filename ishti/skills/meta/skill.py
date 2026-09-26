"""About ISHTI itself: skill health (runs every skill's self-check), what it has logged as missing (NEEDS.md), what it
accessed/did recently (permission audit from the route log), which skills get used most."""
import concurrent.futures as cf
import datetime as dt
import json
import os
import re
import subprocess
import sys
import time

from ishti import ROOT
from ishti.skills import trace

I = re.I
NEEDS = os.path.join(ROOT, "md files", "NEEDS.md")
OPS = [
    ("health", re.compile(r"\b(?:skill\s+health|check\s+(?:your|all|the)\s+skills|are\s+(?:all\s+)?(?:your|the)\s+skills\s+(?:ok|working)|self[-\s]?test|diagnose\s+(?:your|the)\s+skills)\b", I)),
    ("needs", re.compile(r"\b(?:what\s+(?:have\s+you|did\s+you)\s+(?:flag|log)(?:ged)?|what(?:'s| is)\s+missing|needs\.md|capability\s+gaps?|what\s+can'?t\s+you\s+do)\b", I)),
    ("audit", re.compile(r"\b(?:what\s+have\s+you\s+(?:accessed|done|touched)|permission\s+audit|activity\s+log|what\s+did\s+you\s+do\s+(?:today|recently))\b", I)),
    ("usage", re.compile(r"\b(?:which|what)\s+skills?\s+(?:do\s+i|did\s+i)\s+use\s+(?:the\s+)?most\b|\bskill\s+usage\b", I)),
]


def match(text):
    return any(rx.search(text) for _, rx in OPS)


def run(text, session):
    for name, rx in OPS:
        if rx.search(text):
            trace(session, "meta", name)
            return {"reply": globals()["op_" + name](text, session), "sources": []}
    return None


def op_health(text, session):
    from ishti.dispatcher import SKILLS
    here = os.path.join(ROOT, "ishti", "skills")
    names = [n for n in SKILLS if os.path.isfile(os.path.join(here, n, "skill.py"))
             and "__main__" in open(os.path.join(here, n, "skill.py"), encoding="utf-8").read()]
    trace(session, "selfcheck", f"running {len(names)} skill self-checks", status="start")

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
    return f"Skill health: {ok}/{len(results)} self-checks pass, {len(SKILLS) - len(broken)}/{len(SKILLS)} skills loaded.\n" + "\n".join(lines)


def op_needs(text, session):
    try:
        with open(NEEDS, encoding="utf-8") as f:
            body = f.read()
    except OSError:
        return "NEEDS.md isn't there."
    logged = re.findall(r"^- (\d{4}-\d{2}-\d{2} \d{2}:\d{2}) \[(\w+)\] (.+)$", body, re.M)
    planned = re.findall(r"^##\s+(\d+\..+)$", body, re.M)  # "## 1. Fine-tune Laya ..."
    out = []
    if logged:
        out.append("Logged while working (newest first):\n" + "\n".join(f"- {t} [{k}] {d[:140]}" for t, k, d in logged[::-1][:12]))
    if planned:
        out.append("Open needs in NEEDS.md:\n" + "\n".join(f"- {p[:100]}" for p in planned[:15]))
    return "\n\n".join(out) or "Nothing logged as missing."


def _routes(days):
    from ishti.facts import get_facts
    f = get_facts()
    with f.lock:
        return f.db.execute("SELECT ts, text, skill, source FROM route_log WHERE ts>=? ORDER BY ts DESC",
                            (time.time() - days * 86400,)).fetchall()


def op_audit(text, session):
    from ishti.dispatcher import SKILLS
    rows = _routes(1 if re.search(r"\btoday\b", text, I) else 7)
    sensitive = [r for r in rows if r[2] in SKILLS and SKILLS[r[2]].manifest.get("tier") in ("T2", "T3")]
    shown = (sensitive or rows)[:25]
    head = f"{len(rows)} requests handled; {len(sensitive)} used sensitive (T2) skills" + (" - listed:" if sensitive else " - latest:")
    return head + "\n" + "\n".join(f"- {dt.datetime.fromtimestamp(t):%d %b %H:%M} {skill or 'none'}: {q[:80]}" for t, q, skill, _ in shown)


def op_usage(text, session):
    rows = _routes(30)
    counts = {}
    for _, _, skill, src in rows:
        if skill and src != "escalation":
            counts[skill] = counts.get(skill, 0) + 1
    top = sorted(counts.items(), key=lambda kv: -kv[1])[:12]
    return (f"Skills used in the last 30 days ({len(rows)} requests):\n" + "\n".join(f"- {n}: {c}" for n, c in top)) if top else "No usage yet."


if __name__ == "__main__":
    for t, want in {"check your skills": "health", "what have you flagged as missing": "needs", "what have you accessed recently": "audit",
                    "which skills do I use the most": "usage"}.items():
        assert next((n for n, rx in OPS if rx.search(t)), None) == want, t
    assert not match("check the weather")
    print("[self-check] meta OK - routing")
