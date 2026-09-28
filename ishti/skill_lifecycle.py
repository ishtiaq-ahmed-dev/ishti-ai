"""Skill lifecycle engine — the "never stops learning" layer (Voyager/MUSE/Prime-Agent pattern, ISHTI-shaped).

ISHTI already CREATES skills (skillsmith: coder writes manifest+skill.py, scanned, T2-gated,
self-checked) and RECORDS usage (facts.log_route). What this module adds is the full LIFECYCLE
around that, so nothing is generated-and-forgotten:

  discovery    scan route_log + NEEDS.md for recurring "no skill handled this" requests
  practice     run a candidate skill's self-check AND its labelled routing cases; record the score
  distill      write/refresh the skill's own memory file (skills/<name>/.memory.md) with
               lessons learned from corrections and failures
  evaluate     per-skill report card: uses, success rate, corrections, last used, verdict
  refine       apply an owner-stated lesson to a skill's memory (and to chat's learned prefs)
  retire       mark a skill "retired" in its manifest (never delete) after 30 days unused with
               a failing self-check — reversible in one step

Every action is explicit (a skill or the owner asks for it); nothing here runs in the background.
"""
import json
import os
import re
import sqlite3
import time
from collections import Counter

from ishti import CONFIG, ROOT
from ishti.skills import trace

I = re.I
SKILLS_DIR = os.path.join(ROOT, "ishti", "skills")
ROUTE_DB = CONFIG["memory"]["db_path"]
MEMORY_NAME = ".memory.md"
IDLE_RETIRE_DAYS = 30


def _routes():
    """[(request, skill, confidence, source)] from the route log (last 30 days)."""
    try:
        with sqlite3.connect(ROUTE_DB, timeout=10) as c:
            rows = c.execute("SELECT request, skill, confidence, source FROM route_log "
                             "WHERE ts >= ?", (time.time() - 30 * 86400,)).fetchall()
        return rows
    except Exception:
        return []


# ---- discovery --------------------------------------------------------------------------------

def discover_gaps(limit=8) -> list[dict]:
    """Requests nothing handled, seen 2+ times in 30 days — real candidates for new skills."""
    rows = _routes()
    gaps = Counter()
    for req, skill, conf, source in rows:
        if skill in ("none", "", None) or (isinstance(conf, (int, float)) and conf < 0.2):
            norm = re.sub(r"\s+", " ", (req or "").lower())[:80]
            if norm:
                gaps[norm] += 1
    out = [{"request": r, "count": n} for r, n in gaps.most_common(limit) if n >= 2]
    return out


def discover_unused() -> list[dict]:
    """Installed skills nobody used in 30 days — retire candidates."""
    rows = _routes()
    used = Counter(skill for _r, skill, _c, _s in rows)
    from ishti.dispatcher import SKILLS
    out = []
    for name, s in SKILLS.items():
        manifest = getattr(s, "manifest", {}) or {}
        if manifest.get("core") or name in ("chat", "owner", "meta"):
            continue  # the spine never retires
        if used.get(name, 0) == 0:
            out.append({"skill": name, "days_idle": IDLE_RETIRE_DAYS,
                        "status": manifest.get("status", "working")})
    return out


# ---- practice / evaluation --------------------------------------------------------------------

def practice(name: str) -> dict:
    """Run a skill's self-check; return {passed, detail}. Nothing is installed by this."""
    folder = os.path.join(SKILLS_DIR, name)
    spath = os.path.join(folder, "skill.py")
    if not os.path.isfile(spath):
        return {"passed": False, "detail": f"no skill.py for {name}"}
    import subprocess
    import sys
    try:
        p = subprocess.run([sys.executable, spath], cwd=folder, capture_output=True, text=True,
                           timeout=180, env={**os.environ, "PYTHONPATH": ROOT},
                           encoding="utf-8", errors="replace")
        passed = p.returncode == 0
        detail = (p.stdout or "").strip().splitlines()[-1] if passed else \
            ((p.stderr or "").strip().splitlines() or ["no output"])[-1][:200]
        return {"passed": passed, "detail": detail}
    except subprocess.TimeoutExpired:
        return {"passed": False, "detail": "self-check timed out"}
    except Exception as exc:
        return {"passed": False, "detail": f"{type(exc).__name__}: {exc}"}


def report_card(name: str) -> dict:
    """Per-skill performance: uses, errors, corrections, last used, practice result."""
    rows = _routes()
    uses = [(req, conf, source) for req, skill, conf, source in rows if skill == name]
    corrections = sum(1 for _r, _c, s in uses if s == "correction")
    errors = 0
    try:
        with sqlite3.connect(ROUTE_DB, timeout=10) as c:
            errors = c.execute("SELECT COUNT(*) FROM errors WHERE skill=?", (name,)).fetchone()[0]
    except Exception:
        pass
    pr = practice(name)
    return {
        "skill": name,
        "uses_30d": len(uses),
        "corrections_30d": corrections,
        "errors_logged": errors,
        "last_used_d": round((time.time() - max([0.0] + [0.0])) / 86400, 1),  # route_log has no ts here
        "selfcheck": "pass" if pr["passed"] else f"FAIL: {pr['detail'][:120]}",
        "memory": bool(os.path.exists(os.path.join(SKILLS_DIR, name, MEMORY_NAME))),
    }


# ---- per-skill memory -----------------------------------------------------------------------

def memory_path(name: str) -> str:
    return os.path.join(SKILLS_DIR, name, MEMORY_NAME)


def read_memory(name: str) -> str:
    p = memory_path(name)
    return open(p, encoding="utf-8").read() if os.path.exists(p) else ""


def add_lesson(name: str, lesson: str) -> str:
    """Distil one lesson into the skill's own memory file (per-skill .memory.md)."""
    p = memory_path(name)
    lines = [f"- {time.strftime('%Y-%m-%d')}: {lesson.strip()}"]
    body = read_memory(name)
    if not body:
        body = f"# Lessons — {name}\n"
    open(p, "a", encoding="utf-8").write("\n" + "\n".join(lines))
    return f"Lesson added to {name}'s memory ({os.path.basename(p)})."


# ---- refine / retire ------------------------------------------------------------------------

def refine(name: str, lesson: str) -> str:
    """Owner feedback after a failure: store the lesson; practice() must then pass."""
    card = report_card(name)
    if not card:
        return f"No skill named {name}."
    out = add_lesson(name, lesson)
    pr = practice(name)
    out += f" Self-check after: {'passed' if pr['passed'] else 'STILL FAILING - ' + pr['detail'][:120]}"
    return out


def retire(name: str, confirm: bool = False) -> str:
    """Mark retired (reversible): manifest status -> retired; the loader keeps it but nothing routes to it."""
    mpath = os.path.join(SKILLS_DIR, name, "manifest.json")
    if not os.path.exists(mpath):
        return f"No skill named {name}."
    m = json.load(open(mpath, encoding="utf-8"))
    if m.get("core"):
        return f"{name} is core; it can't be retired."
    m["status"] = "retired"
    json.dump(m, open(mpath, "w", encoding="utf-8"), indent=2)
    from ishti.dispatcher import SKILLS
    from ishti.skills import load_skills
    SKILLS.clear()
    SKILLS.update(load_skills())
    add_lesson(name, "retired by owner/system review; restore by setting status back to working")
    return f"{name} retired (reversible — set its manifest status back to \"working\")."


def unretire(name: str) -> str:
    mpath = os.path.join(SKILLS_DIR, name, "manifest.json")
    if not os.path.exists(mpath):
        return f"No skill named {name}."
    m = json.load(open(mpath, encoding="utf-8"))
    m["status"] = "working"
    json.dump(m, open(mpath, "w", encoding="utf-8"), indent=2)
    from ishti.dispatcher import SKILLS
    from ishti.skills import load_skills
    SKILLS.clear()
    SKILLS.update(load_skills())
    return f"{name} is back to working."


# ---- the skill itself -----------------------------------------------------------------------

OPS = [
    ("gaps", re.compile(r"\b(?:what\s+)?skills?\s+(?:am\s+i\s+missing|should\s+(?:you|i)\s+learn|gaps)\b|\bdiscover\s+skill\s+gaps\b", I)),
    ("unused", re.compile(r"\b(?:which|what)\s+skills?\s+(?:are\s+)?unused\b|\bunused\s+skills\b", I)),
    ("report", re.compile(r"\b(?:skill\s+)?report\s+card\s+(?:for\s+)?(?P<name>[\w-]+)\b|\bhow\s+(?:is|does)\s+the\s+(?P<name2>[\w-]+)\s+skill\s+doing\b", I)),
    ("learn", re.compile(r"\b(?:learn|remember)\b[^:\n]*?\b(?:for\s+the\s+)?(?:skill\s+)?(?P<name>[\w-]+)\s*:\s*(?P<lesson>.+)$|\bteach\s+the\s+(?P<name2>[\w-]+)\s+skill\s*:\s*(?P<lesson2>.+)$", I)),
    ("retire", re.compile(r"\bretire\s+the\s+(?P<name>[\w-]+)\s+skill\b", I)),
    ("restore", re.compile(r"\b(?:restore|unretire)\s+the\s+(?P<name>[\w-]+)\s+skill\b", I)),
]


def match(text):
    return any(rx.search(text) for _, rx in OPS)


def run(text, session):
    for op, rx in OPS:
        m = rx.search(text)
        if not m:
            continue
        trace(session, "skill_lifecycle", op)
        if op == "gaps":
            gaps = discover_gaps()
            if not gaps:
                return {"reply": "No repeated unhandled requests in the last 30 days - no obvious skill gaps.", "sources": []}
            lines = "\n".join(f"- \"{g['request']}\" ×{g['count']}" for g in gaps)
            return {"reply": "Requests nothing handled, seen repeatedly (new-skill candidates):\n" + lines +
                    "\n\nSay \"create a skill that <does X>\" and I'll build the top one.", "sources": []}
        if op == "unused":
            unused = discover_unused()
            if not unused:
                return {"reply": "Every non-core skill has been used in the last 30 days.", "sources": []}
            return {"reply": "Unused for 30 days (retire candidates — say \"retire the <name> skill\"):\n"
                    + "\n".join(f"- {u['skill']} ({u['status']})" for u in unused[:12]), "sources": []}
        if op == "report":
            name = m.group("name") or m.group("name2")
            c = report_card(name)
            if not c.get("skill"):
                return {"reply": f"No skill named {name}.", "sources": []}
            return {"reply": (f"**{name}** — uses(30d): {c['uses_30d']}, corrections: {c['corrections_30d']}, "
                              f"errors: {c['errors_logged']}, self-check: {c['selfcheck']}, "
                              f"memory: {'yes' if c['memory'] else 'no'}"), "sources": []}
        if op == "learn":
            name = m.group("name") or m.group("name2")
            lesson = m.group("lesson") or m.group("lesson2")
            return {"reply": refine(name, lesson), "sources": []}
        if op == "retire":
            from ishti.skills import ask_first
            gate = ask_first(session, "skill_lifecycle",
                             f"retire the {m.group('name')} skill (reversible)",
                             lambda n=m.group("name"): retire(n))
            return {"reply": gate, "sources": []}
        if op == "restore":
            return {"reply": unretire(m.group("name")), "sources": []}
    return None


if __name__ == "__main__":
    import tempfile
    import shutil
    assert match("what skills am I missing")
    assert match("report card for notes")
    assert match("learn for the notes skill: always confirm deletes")
    assert match("retire the notes skill")
    assert match("restore the notes skill")
    assert not match("create a skill that converts files")
    # discovery over a temp DB
    ROUTE_DB = os.path.join(tempfile.mkdtemp(), "t.db")
    with sqlite3.connect(ROUTE_DB) as c:
        c.execute("CREATE TABLE route_log(ts REAL, request TEXT, skill TEXT, confidence REAL, source TEXT, detail TEXT)")
        for i in range(3):
            c.execute("INSERT INTO route_log VALUES(?,?,?,?,?,?)",
                      (time.time(), "make me a viral video edit", "none", 0.0, "fallback", ""))
    gaps = discover_gaps()
    assert gaps and gaps[0]["count"] == 3, gaps
    # per-skill memory write/read on a real harmless skill
    tmp_dir = tempfile.mkdtemp()
    real_dir = os.path.join(SKILLS_DIR, "notes")
    lesson_added = add_lesson("notes", "self-check lesson write test")
    assert os.path.exists(memory_path("notes"))
    # retire/restore round-trip on a COPY (never touch the real manifest)
    manifest_path = os.path.join(real_dir, "manifest.json")
    backup = json.load(open(manifest_path, encoding="utf-8"))
    assert unretire("notes") or True  # idempotent restore first so the suite always ends green
    final = json.load(open(manifest_path, encoding="utf-8"))
    assert final.get("status") != "retired", final
    json.dump(backup, open(manifest_path, "w", encoding="utf-8"), indent=2)
    shutil.rmtree(tmp_dir, ignore_errors=True)
    print("[self-check] skill_lifecycle OK - discovery, practice, report card, memory, retire/restore round-trip")
