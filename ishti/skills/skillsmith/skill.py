"""Grow ISHTI: install a skill from a GitHub repo, or have the coder model build a new one - never silently.

  "install the skill from https://github.com/user/repo"  -> clone into data/skill_staging, check it has ISHTI's
      skill shape (manifest.json + skill.py with run), scan the code for risky calls, show all that, install only
      after a yes, then run its self-check and load it.
  "create a skill that <does X>" / "learn to <do X>"  -> the coder model writes manifest + skill.py in ISHTI's
      format, same scan + show + yes + self-check.
  "list installed skills" / "remove the <name> skill" (asks first; built-in skills can't be removed).
Installed skills are marked "source" in their manifest so it's clear which ones came from outside."""
import json
import os
import re
import shutil
import subprocess
import sys

from ishti import ROOT, llm
from ishti.skills import ask_first, trace

I = re.I
SKILLS_DIR = os.path.join(ROOT, "ishti", "skills")
STAGING = os.path.join(ROOT, "data", "skill_staging")
REPO = re.compile(r"https?://github\.com/[\w.-]+/[\w.-]+(?:/tree/[\w./-]+)?", I)
RISKY = [(r"\bsubprocess\b|\bos\.system\b|\bPopen\b", "runs other programs"),
         (r"\bos\.remove\b|\bshutil\.rmtree\b|\bunlink\(|\bos\.rmdir\b", "deletes files"),
         (r"\beval\(|\bexec\(|\b__import__\(|\bcompile\(", "runs dynamic code"),
         (r"\brequests\.|\burllib\b|\bhttpx\b|\bsocket\b|\baiohttp\b", "uses the network"),
         (r"\bwinreg\b|\bctypes\b", "touches the registry / low-level Windows APIs"),
         (r"\b(?:password|api_key|token|secret)\b", "mentions credentials")]
OPS = [
    ("install", re.compile(r"\binstall\s+(?:the\s+|a\s+)?skill\b.*github\.com|\badd\s+(?:the\s+)?skill\s+from\b", I)),
    ("remove", re.compile(r"\b(?:remove|uninstall|delete)\s+(?:the\s+)?(?P<name>[\w-]+)\s+skill\b", I)),
    ("list", re.compile(r"\b(?:list|show)\s+(?:the\s+)?(?:installed|added|custom|external)\s+skills\b", I)),
    ("create", re.compile(r"\b(?:create|build|make|write)\s+(?:a\s+|me\s+a\s+)?(?:new\s+)?skill\s+(?:that|to|for|which)\b|\blearn\s+(?:how\s+)?to\s+\w+", I)),
]


def match(text):
    return any(rx.search(text) for _, rx in OPS)


def run(text, session):
    for name, rx in OPS:
        if m := rx.search(text):
            trace(session, "skillsmith", name)
            out = globals()["op_" + name](m, text, session)
            return out if isinstance(out, dict) else {"reply": out, "sources": []}
    return None


def scan(folder):
    """[(file, finding)] for risky calls in every .py file of a skill folder."""
    found = []
    for root, _, files in os.walk(folder):
        for f in files:
            if f.endswith(".py"):
                code = open(os.path.join(root, f), encoding="utf-8", errors="ignore").read()
                found += [(f, why) for rx, why in RISKY if re.search(rx, code)]
    return sorted(set(found))


def selfcheck(folder):
    """(passed, last error line) - runs the staged skill's own __main__ self-check with ISHTI importable."""
    try:
        p = subprocess.run([sys.executable, os.path.join(folder, "skill.py")], cwd=folder, capture_output=True, text=True,
                           timeout=120, env={**os.environ, "PYTHONPATH": ROOT}, encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired:
        return False, "timed out"
    tail = [l for l in p.stderr.strip().splitlines() if l.strip()]
    return p.returncode == 0, "\n".join(tail[-4:])


def validate(folder):
    """(manifest dict, problem or '')."""
    mpath, spath = os.path.join(folder, "manifest.json"), os.path.join(folder, "skill.py")
    if not (os.path.isfile(mpath) and os.path.isfile(spath)):
        return None, "it isn't an ISHTI skill (needs manifest.json and skill.py)"
    try:
        m = json.load(open(mpath, encoding="utf-8"))
    except ValueError:
        return None, "its manifest.json isn't valid JSON"
    if not re.fullmatch(r"[a-z][a-z0-9_]{1,30}", str(m.get("name", ""))):
        return m, "its manifest has no valid name"
    if "def run(" not in open(spath, encoding="utf-8", errors="ignore").read():
        return m, "its skill.py has no run(text, session) function"
    return m, ""


def install_folder(src, manifest, source):
    """Copy into ishti/skills, mark the source, pip-install declared packages, self-check, reload the registry."""
    name = manifest["name"]
    dst = os.path.join(SKILLS_DIR, name)
    if os.path.exists(dst):
        return f"A skill named {name} already exists - not overwriting it."
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc"))
    manifest.update(source=source, tier=manifest.get("tier", "T2"), status=manifest.get("status", "working"))
    json.dump(manifest, open(os.path.join(dst, "manifest.json"), "w", encoding="utf-8"), indent=2)
    pkgs = [p for p in manifest.get("needs", {}).get("python", []) if re.fullmatch(r"[A-Za-z0-9_.-]+", p)]
    if pkgs:
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", *pkgs], capture_output=True, timeout=900)
    check = subprocess.run([sys.executable, "-m", f"ishti.skills.{name}.skill"], cwd=ROOT, capture_output=True, text=True,
                           timeout=300, encoding="utf-8", errors="replace")
    from ishti.dispatcher import SKILLS
    from ishti.skills import load_skills
    SKILLS.clear()
    SKILLS.update(load_skills())
    loaded = SKILLS.get(name)
    state = "loaded" if loaded and loaded.status != "broken" else f"NOT loaded ({loaded.problem if loaded else 'missing'})"
    checked = "its self-check passed" if check.returncode == 0 else ("it has no self-check" if "__main__" not in open(os.path.join(dst, "skill.py"), encoding="utf-8").read()
                                                                     else f"its self-check FAILED: {check.stderr.strip().splitlines()[-1][:150] if check.stderr.strip() else 'no output'}")
    return f"Installed **{name}** ({state}; {checked}). Try: {manifest.get('route', '')}"


def _review(folder, manifest, source):
    findings = scan(folder)
    size = sum(os.path.getsize(os.path.join(r, f)) for r, _, fs in os.walk(folder) for f in fs if f.endswith(".py"))
    lines = [f"**{manifest.get('name')}** v{manifest.get('version', '?')} from {source}",
             f"What it does: {manifest.get('description', '(no description)')}",
             f"Python packages it needs: {', '.join(manifest.get('needs', {}).get('python', [])) or 'none'}",
             f"Code: {size / 1024:.1f} KB of Python"]
    lines.append("Things to know before installing:\n" + "\n".join(f"- {f}: {why}" for f, why in findings) if findings
                 else "Scan: no risky calls found (no program launching, deleting, dynamic code or network).")
    return "\n".join(lines)


def op_install(m, text, session):
    url = REPO.search(text).group(0)
    base = re.match(r"(https?://github\.com/[\w.-]+/[\w.-]+)", url).group(1)
    sub = (re.search(r"/tree/[\w.-]+/(.+)$", url) or [None, ""])[1]
    work = os.path.join(STAGING, re.sub(r"\W+", "_", base.split("github.com/")[1]))
    shutil.rmtree(work, ignore_errors=True)
    os.makedirs(STAGING, exist_ok=True)
    trace(session, "git", f"cloning {base}", status="start")
    p = subprocess.run(["git", "clone", "--depth", "1", base + ".git", work], capture_output=True, text=True, timeout=300)
    if p.returncode != 0:
        return f"Couldn't clone {base}: {p.stderr.strip()[-200:]}"
    folder = os.path.join(work, sub) if sub else work
    if not os.path.isfile(os.path.join(folder, "manifest.json")):  # a repo holding several skills: take the first
        cands = [os.path.dirname(os.path.join(r, "manifest.json")) for r, _, fs in os.walk(work) if "manifest.json" in fs and "skill.py" in fs]
        folder = cands[0] if cands else folder
    manifest, problem = validate(folder)
    if problem:
        from ishti.skills import log_need
        log_need("gap", "skillsmith", text, f"{base}: {problem}")
        return f"Not installing: {problem}. (ISHTI skills are a folder with manifest.json and skill.py exposing run(text, session).)"
    review = _review(folder, manifest, base)
    return ask_first(session, "skillsmith", review + "\n\nInstall it", lambda: install_folder(folder, manifest, base))


def op_create(m, text, session):
    """The coder model writes a new skill in ISHTI's shape; it is shown and scanned before anything is installed."""
    example = open(os.path.join(SKILLS_DIR, "utilities", "skill.py"), encoding="utf-8").read()[:2500]
    trace(session, "coder", "writing the new skill", status="start")
    raw = llm.ask("Write a new skill for ISHTI, a local Python assistant, that does: " + text + "\n\n"
                  "A skill is a folder with manifest.json and skill.py. skill.py must define match(text) -> bool (a "
                  "precise regex so it doesn't steal other requests) and run(text, session) -> {\"reply\": str, "
                  "\"sources\": []} or None, use only the standard library or requests, and end with an "
                  "`if __name__ == \"__main__\":` self-check using asserts that needs no network. Style example:\n"
                  f"```python\n{example}\n```\n\nReturn JSON {{\"name\": \"<snake_case>\", \"description\": \"...\", "
                  "\"route\": \"<when to use it>\", \"python_packages\": [], \"skill_py\": \"<full file>\"}}.",
                  role="coder", system=None, temperature=0.2, max_tokens=3500, response_format={"type": "json_object"})
    try:
        spec = json.loads(raw)
        name, code = spec["name"], spec["skill_py"]
    except (ValueError, KeyError):
        return "The coder model didn't produce a usable skill - describe it more precisely and try again."
    if not re.fullmatch(r"[a-z][a-z0-9_]{1,30}", name) or os.path.exists(os.path.join(SKILLS_DIR, name)):
        return f"Can't use the name '{name}' (invalid or already taken)."
    folder = os.path.join(STAGING, "new_" + name)
    shutil.rmtree(folder, ignore_errors=True)
    os.makedirs(folder)
    unfence = lambda c: re.sub(r"^\s*```\w*\s*\n|\n?```\s*$", "", c.strip())  # models wrap code in fences even inside JSON
    code = unfence(code)
    for attempt in range(2):  # one repair round: the compile error goes back to the coder model
        open(os.path.join(folder, "skill.py"), "w", encoding="utf-8").write(code)
        syntax = subprocess.run([sys.executable, "-m", "py_compile", os.path.join(folder, "skill.py")], capture_output=True, text=True, timeout=60)
        if syntax.returncode == 0 or attempt:
            break
        trace(session, "coder", "fixing a syntax error in the generated skill", status="start")
        code = unfence(llm.ask(f"This Python file fails to compile:\n{syntax.stderr.strip()[-600:]}\n\nFile:\n{code}\n\n"
                               "Return only the corrected complete file, no explanation.", role="coder", system=None,
                               temperature=0, max_tokens=3500))
    manifest = {"name": name, "version": "0.1.0", "description": spec.get("description", ""), "route": spec.get("route", ""),
                "needs": {"python": spec.get("python_packages", []), "models": [], "mcp": [], "apis": []},
                "priority": 45, "status": "working", "tier": "T1", "laya": False}
    json.dump(manifest, open(os.path.join(folder, "manifest.json"), "w", encoding="utf-8"), indent=2)
    if syntax.returncode != 0:
        return f"The generated skill doesn't compile:\n```\n{syntax.stderr.strip()[-400:]}\n```\nTry describing it differently."
    ok, err = selfcheck(folder)
    for round_ in range(3):  # fix rounds with the failing assertion; a skill that never passes is not offered
        if ok:
            break
        trace(session, "coder", f"its self-check failed - fix {round_ + 1}/3", status="start")
        code = unfence(llm.ask(f"This ISHTI skill's self-check fails:\n{err}\n\nFile:\n{code}\n\nFix the code (or a wrong "
                               "assertion). Make match() broad enough for natural phrasings of the request. Return only the "
                               "corrected complete file.", role="coder", system=None, temperature=0.1 * round_, max_tokens=3500))
        open(os.path.join(folder, "skill.py"), "w", encoding="utf-8").write(code)
        ok, err = selfcheck(folder)
    if not ok:
        from ishti.skills import log_need
        log_need("gap", "skillsmith", text, f"couldn't generate a working '{name}' skill (self-check kept failing)")
        return (f"I couldn't build a working '{name}' skill - its own test kept failing after 3 fixes, so I didn't "
                f"install it (logged in the needs log). Last error:\n```\n{err[-300:]}\n```")
    review = _review(folder, manifest, "the coder model") + "\nSelf-check: passes."
    preview = code if len(code) < 3500 else code[:3500] + "\n# ..."
    return ask_first(session, "skillsmith", f"{review}\n\n```python\n{preview}\n```\n\nInstall this new skill",
                     lambda: install_folder(folder, manifest, "generated"))


def op_list(m, text, session):
    from ishti.dispatcher import SKILLS
    extra = [(n, s.manifest.get("source"), s.status) for n, s in SKILLS.items() if s.manifest.get("source")]
    return ("Skills added from outside:\n" + "\n".join(f"- {n} ({src}, {st})" for n, src, st in extra)) if extra else \
        f"No added skills yet - all {len(SKILLS)} are built in. Say \"install the skill from <github url>\" or \"create a skill that ...\"."


def op_remove(m, text, session):
    from ishti.dispatcher import SKILLS
    name = m.group("name").lower()
    sk = SKILLS.get(name)
    if not sk:
        return f"There's no skill called {name}."
    if not sk.manifest.get("source"):
        return f"{name} is built in - I only remove skills that were added."

    def remove():
        shutil.rmtree(os.path.join(SKILLS_DIR, name), ignore_errors=True)
        SKILLS.pop(name, None)
        return f"Removed the {name} skill."
    return ask_first(session, "skillsmith", f"Remove the added skill {name}", remove)


if __name__ == "__main__":
    import tempfile
    cases = {"install the skill from https://github.com/x/y": "install", "create a skill that tells me a random quote": "create",
             "learn to convert roman numerals": "create", "list installed skills": "list", "remove the quotes skill": "remove"}
    for t, want in cases.items():
        got = next((n for n, rx in OPS if rx.search(t)), None)
        assert got == want, (t, got, want)
    d = tempfile.mkdtemp()
    assert validate(d)[1].startswith("it isn't")
    json.dump({"name": "demo_skill"}, open(os.path.join(d, "manifest.json"), "w"))
    open(os.path.join(d, "skill.py"), "w").write("import subprocess\ndef run(text, session):\n    return None\n")
    assert validate(d) == ({"name": "demo_skill"}, "")
    assert scan(d) == [("skill.py", "runs other programs")]
    print("[self-check] skillsmith OK - routing, validation, risk scan")
