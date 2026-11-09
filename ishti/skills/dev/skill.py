"""Development: run code (the owner's own snippet in a sandbox folder, or a project script), git status/log/diff,
commit (local) and push (asks first), run a project's tests, lint with ruff, explain a codebase with the coder
model, test an API endpoint, summarise errors in a log file. Projects resolve from the owner's saved project
locations ("the ishti project" -> the ISHTI checkout) or a path in the request."""
import os
import re
import subprocess
import sys
import time

import requests

from ishti import ROOT
from ishti.skills import ask_first, trace
from ishti.test_mode import scratch_path

from ishti.skills.dev import scaffold
I = re.I
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
SANDBOX = scratch_path(os.path.join(ROOT, "data", "sandbox"))
CODE = re.compile(r"```(?:python|py)?\s*\n(?P<code>.+?)```", I | re.S)
OPS = [
    ("scaffold", scaffold.SCAFFOLD),
    ("run_script", scaffold.RUN_SCRIPT),
    ("service", scaffold.SERVICE),
    ("run_code", re.compile(r"\b(?:run|execute)\s+(?:this|the\s+following|my)?\s*(?:python\s+)?(?:code|script|snippet)\b.*```", I | re.S)),
    # FIX-8: both separators - users type D:/paths constantly
    ("run_file", re.compile(r"\b(?:run|execute)\s+(?P<path>[A-Za-z]:[\\/][^\s\"']+\.py)\b", I)),
    ("git_push", re.compile(r"\bpush\s+(?:my\s+|the\s+)?(?:changes|code|commits?)?\b.*\b(?:to\s+github|git|remote|origin)?|\bgit\s+push\b", I)),
    ("git_commit", re.compile(r"\bcommit\s+(?:my\s+|the\s+|all\s+)?(?:changes|work|code)\b|\bgit\s+commit\b", I)),
    ("git_status", re.compile(r"\bgit\s+(?:status|log|diff)\b|\b(?:what(?:'s| has)?\s+changed|uncommitted\s+changes|recent\s+commits)\b", I)),
    ("tests", re.compile(r"\brun\s+(?:the\s+|all\s+|my\s+)?tests?\b|\b(?:do|are)\s+(?:the\s+)?tests?\s+pass|\btest\s+status\b|\b(?:check|what|if)\s+(?:the\s+)?tests?\s+(?:are|is|passing|failing|ok|good)\b", I)),
    ("lint", re.compile(r"\b(?:lint|ruff|check\s+(?:the\s+)?code\s+(?:quality|style))\b", I)),
    ("explain", re.compile(r"\bexplain\s+(?:the\s+|my\s+)?(?:\w+\s+)?(?:codebase|project|repo(?:sitory)?|code\s*base)\b|\bhow\s+is\s+.+\s+(?:structured|organi[sz]ed)\b", I)),
    ("api_test", re.compile(r"\b(?:test|hit|call|ping|check)\s+(?:the\s+)?(?:api|endpoint|url)?\s*(?P<method>GET|POST|PUT|DELETE)?\s*(?P<url>https?://\S+)", I)),
    ("log_check", re.compile(r"\b(?:check|read|summari[sz]e|what(?:'s| is)\s+in)\s+(?:the\s+|my\s+)?(?:\w+\s+)?logs?\b|\berrors?\s+in\s+(?:the\s+)?log", I)),
    # "install this Python package for me: X" / "pip install X" / "add X to my project" - the owner
    # may run this in a project venv or globally; either way it changes the environment, so it asks first.
    ("pip_install", re.compile(r"\b(?:pip\s+)?install\s+(?:this\s+|the\s+)?(?:python\s+)?(?:package|library|module|package\s+named)\s+(?:for\s+me\s+)?\b|\bpip\s+install\b|\badd\s+(?:requests?|numpy|pandas|flask|fastapi|django|scikit|pytorch|tensorflow|nltk|beautifulsoup|bs4|selenium|pillow|cv2|redis|sqlalchemy|celery|celerybeat|\w+)\s+to\s+(?:my\s+)?(?:project|venv|environment|pc|python)\b", I)),
    # "Check if any of my Python dependencies are outdated" - a real pip check, T0 read-only
    # (checklist #163). Without this it fell to the chat brain, which would guess instead of running pip.
    ("deps_outdated", re.compile(r"\b(?:python\s+|pip\s+)?(?:dependencies|packages|requirements)\s+(?:are\s+|is\s+)?(?:outdated|old|out\s+of\s+date)\b|"
                                 r"\b(?:check|see|find\s+out)\s+(?:if|whether|which)?\s*(?:any\s+of\s+)?(?:my\s+)?(?:python\s+|pip\s+)?(?:dependencies|packages|requirements)\b.*\b(?:outdated|old|up\s+to\s+date|update)\b|"
                                 r"\bwhat(?:'s|\s+is)\s+out\s+of\s+date\b", I)),
]


def match(text):
    return any(rx.search(text) for _, rx in OPS) and not (dict(OPS)["git_push"].search(text) and not re.search(r"\b(?:git|github|commit|code|changes|repo)\b", text, I))


def op_scaffold(m, text, session):
    # A scaffold downloads and runs a starter generator from npm (create-next-app / create-vite) with the owner's
    # rights: that is the owner's call, every time.
    # ponytail: generators stay @latest (their maintainers ship fixes there); pin them here if a supply-chain
    # incident ever makes that the wrong trade-off.
    return ask_first(session, "dev", "Create this project? (it downloads a starter generator from npm and runs it)",
                     lambda: scaffold.op_scaffold(m, text, session))


def op_run_script(m, text, session):
    return scaffold.op_run_script(m, text, session, ask_first)


def op_service(m, text, session):
    return scaffold.op_service(m, text, session, ask_first)


def run(text, session):
    for name, rx in OPS:
        if m := rx.search(text):
            if name == "git_push" and not re.search(r"\b(?:git|github|commit|code|changes|repo|origin|remote)\b", text, I):
                continue
            trace(session, "dev", name.replace("_", " "))
            out = globals()["op_" + name](m, text, session)
            return out if isinstance(out, dict) or out is None else {"reply": out, "sources": []}

    # If no specific op matched but the text looks like a pip install, try the pip_install op directly.
    if re.search(r"\b(?:pip\s+)?install\b", text, I) and re.search(r"\b(?:package|library|module|python|requests?|numpy|pandas|flask|fastapi|django|scikit|pytorch|tensorflow|redis|sqlalchemy)\b", text, I):
        m = re.search(r"\bpip\s+install\s+(?:--\w+\s+)*\[?([a-zA-Z0-9._-]+)\]?", text, I) or re.search(r"install\s+(?:this\s+|the\s+)?(?:python\s+)?(?:package|library|module|package\s+named)\s+(?:for\s+me\s+)?(?::\s*)?(?P<pkg>[a-zA-Z][a-zA-Z0-9._-]{1,40})\b", text, I)
        if m:
            pkg = (m.group(1) if m.lastindex == 1 else m.group("pkg")).strip().strip("'[]")
            trace(session, "dev", "pip_install (fallback)")
            return op_pip_install(type("M", (), {"group": lambda s, g: pkg})(), text, session)
    return None


def project(text):
    """(name, path) of the project the request is about: an explicit path, a saved project, else ISHTI itself."""
    from ishti.facts import get_facts, norm
    m = re.search(r"[A-Za-z]:\\[^\"'<>|?*\n]+?(?=\s|$|[,;])", text)
    if m and os.path.isdir(m.group(0)):
        return os.path.basename(m.group(0).rstrip("\\")), m.group(0)
    f = get_facts()
    with f.lock:
        rows = f.db.execute("SELECT entity, value FROM facts WHERE attr='location'").fetchall()
    t = f" {norm(text)} "
    for entity, path in rows:
        if os.path.isdir(path) and f" {norm(entity)} " in t or norm(entity).split()[-1:] and f" {norm(entity).split()[-1]} " in t:
            return entity, path
    return "ISHTI", ROOT


def sh(cmd, cwd, timeout=300):
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=timeout, creationflags=NO_WINDOW)
    return p.returncode, (p.stdout + p.stderr).strip()


def tail(text, n=25):
    lines = text.splitlines()
    return "\n".join(lines[-n:])


# ---------------------------------------------------------------- running code
def op_run_code(m, text, session):
    code = CODE.search(text)
    if not code:
        return "Put the code in a ```python``` block."
    os.makedirs(SANDBOX, exist_ok=True)
    path = os.path.join(SANDBOX, f"snippet_{int(time.time())}.py")
    with open(path, "w", encoding="utf-8") as f:
        f.write(code.group("code"))
    trace(session, "python", "running your snippet in data/sandbox (30 s limit)", status="start")
    try:
        rc, out = sh([sys.executable, path], SANDBOX, timeout=30)
    except subprocess.TimeoutExpired:
        return "Stopped after 30 s (it didn't finish)."
    trace(session, "python", f"exit code {rc}", status="done" if rc == 0 else "error")
    return f"Exit code {rc}:\n```\n{tail(out, 40) or '(no output)'}\n```"


def op_run_file(m, text, session):
    path = m.group("path")
    if not os.path.isfile(path):
        return f"{path} doesn't exist."
    try:
        rc, out = sh([sys.executable, path], os.path.dirname(path), timeout=120)
    except subprocess.TimeoutExpired:
        return "Stopped after 2 minutes."
    return f"{os.path.basename(path)} exited with {rc}:\n```\n{tail(out, 40) or '(no output)'}\n```"


# ---------------------------------------------------------------- git
def op_git_status(m, text, session):
    name, path = project(text)
    rc, status = sh(["git", "status", "--short", "--branch"], path, timeout=30)
    if rc != 0:
        return f"{name} ({path}) isn't a git repository."
    _, log = sh(["git", "log", "--oneline", "-5"], path, timeout=30)
    changed = [l for l in status.splitlines()[1:] if l.strip()]
    return (f"**{name}** - {status.splitlines()[0].lstrip('# ')}\n"
            + (f"{len(changed)} uncommitted change(s) in the working tree (status flag: M modified, D deleted, ?? new):\n"
               + "\n".join(f"- {l}" for l in changed[:20]) if changed else "Nothing uncommitted - the working tree is clean.")
            + f"\n\nRecent commits:\n```\n{log}\n```")


def op_git_commit(m, text, session):
    name, path = project(text)
    rc, status = sh(["git", "status", "--short"], path, timeout=30)
    if rc != 0:
        return f"{name} isn't a git repository."
    if not status.strip():
        return f"Nothing to commit in {name}."
    msg = re.search(r"(?:message|saying|with|:)\s+[\"']?(?P<m>[^\"']{3,120})[\"']?\s*$", text, I)
    if msg:
        message = msg.group("m").strip()
    else:
        from ishti import llm
        _, diff = sh(["git", "diff", "--stat"], path, timeout=30)
        message = llm.ask(f"Write a one-line git commit message (imperative, under 70 characters) for these changes:\n{diff[:3000]}\n{status[:1500]}",
                          role="coder", system=None, temperature=0.2, max_tokens=40).strip().strip('"').splitlines()[0]
    files = len(status.splitlines())

    def commit():
        sh(["git", "add", "-A"], path, timeout=60)
        rc2, out = sh(["git", "commit", "-m", message], path, timeout=60)
        return f"Committed {files} file(s) in {name}: \"{message}\"" if rc2 == 0 else f"Commit failed:\n```\n{tail(out, 12)}\n```"
    return ask_first(session, "dev", f"Commit {files} changed file(s) in {name} as \"{message}\"", commit)


def op_git_push(m, text, session):
    name, path = project(text)
    rc, ahead = sh(["git", "status", "-sb"], path, timeout=30)
    if rc != 0:
        return f"{name} isn't a git repository."
    head = ahead.splitlines()[0]

    def push():
        rc2, out = sh(["git", "push"], path, timeout=180)
        return f"Pushed {name}." if rc2 == 0 else f"Push failed:\n```\n{tail(out, 12)}\n```"
    return ask_first(session, "dev", f"Push {name} to its remote ({head.lstrip('# ')})", push)


# ---------------------------------------------------------------- tests, lint, explain
def op_tests(m, text, session):
    name, path = project(text)
    if os.path.isfile(os.path.join(path, "package.json")) and '"test"' in open(os.path.join(path, "package.json"), encoding="utf-8").read():
        cmd = ["npm", "test", "--silent"]
    elif any(os.path.isdir(os.path.join(path, d)) for d in ("tests", "test")) or os.path.isfile(os.path.join(path, "pytest.ini")):
        cmd = [sys.executable, "-m", "pytest", "-q", "--maxfail=5"]
    elif os.path.isfile(os.path.join(path, "ishti", "check.py")):
        # No --no-laya: the flag went with the Laya routing checkpoint, and ishti.check's routing eval is
        # deterministic now, so there is nothing to skip.
        cmd = [sys.executable, "-m", "ishti.check"]
    else:
        return f"I don't see a test setup in {name} (no tests/ folder, pytest.ini or npm test)."
    trace(session, "tests", f"{' '.join(cmd[-3:])} in {name}", status="start")
    try:
        rc, out = sh(cmd, path, timeout=900)
    except subprocess.TimeoutExpired:
        return "The tests ran for over 15 minutes; stopped."
    trace(session, "tests", "passed" if rc == 0 else "failed", status="done" if rc == 0 else "error")
    # Show the real failures, not the last 20 lines of deprecation warnings: pytest's FAILED lines plus the
    # final "N failed, M passed" count. A summary that hides failures is exactly what checklist #159 warns about.
    lines = out.splitlines()
    fails = [l for l in lines if l.strip().startswith(("FAILED", "ERROR"))]
    summary = next((l.strip() for l in reversed(lines) if re.search(r"\b\d+\s+(?:failed|passed|error|skipped)", l)), "")
    shown = "\n".join(fails[:25] + ([summary] if summary else [])) if (fails or summary) else tail(out, 20)
    return f"Tests in {name} {'passed' if rc == 0 else 'FAILED'}:\n```\n{shown}\n```"


def op_deps_outdated(m, text, session):
    """Real `pip list --outdated` for the project (checklist #163): never a guess.

    Uses the project's own venv interpreter when it has one, else this interpreter. When no
    requirements/pyproject is found, says where it looked rather than inventing an answer."""
    name, path = project(text)
    req = next((p for p in ("requirements.txt", "pyproject.toml", "setup.py", "Pipfile") if os.path.isfile(os.path.join(path, p))), None)
    venv_py = next((os.path.join(path, v, "Scripts", "python.exe") for v in ("venv", ".venv")
                    if os.path.isfile(os.path.join(path, v, "Scripts", "python.exe"))), None)
    py = venv_py or sys.executable
    trace(session, "pip", f"list --outdated in {name}", status="start")
    try:
        rc, out = sh([py, "-m", "pip", "list", "--outdated", "--format", "columns", "--disable-pip-version-check"], path, timeout=180)
    except subprocess.TimeoutExpired:
        return f"The pip check in {name} took too long; stopped."
    if rc != 0:
        return f"I ran pip in {name} but it errored (no requirements file found: {req or 'none'}):\n```\n{tail(out, 15)}\n```"
    lines = [ln for ln in out.splitlines() if ln.strip()]
    if len(lines) <= 2:
        return (f"Nothing outdated in {name} ({os.path.basename(py)}; "
                f"{'requirements file: ' + req if req else 'no requirements file found - checked the interpreter directly'}).")
    return (f"Outdated Python packages in {name} (from `pip list --outdated`, {'requirements file: ' + req if req else 'interpreter ' + os.path.basename(py)}):\n"
            f"```\n{tail(out, 25)}\n```")


def op_lint(m, text, session):
    name, path = project(text)
    ruff = os.path.join(os.path.dirname(sys.executable), "ruff.exe")
    rc, out = sh([ruff if os.path.isfile(ruff) else "ruff", "check", ".", "--exclude", "venv,node_modules,.venv,data",
                  "--statistics", "--quiet"], path, timeout=180)
    if rc == 0:
        return f"ruff: no problems in {name}."
    return f"ruff found issues in {name} (count, rule, name; [*] = auto-fixable):\n```\n{tail(out, 25)}\n```\nI don't change code without your okay."


def op_explain(m, text, session):
    from ishti import llm
    name, path = project(text)
    readme = next((os.path.join(path, p) for p in ("README.md", os.path.join(".github", "README.md"))
                   if os.path.isfile(os.path.join(path, p))), None)
    tree = []
    for root, dirs, files in os.walk(path):
        dirs[:] = [d for d in dirs if d not in {"venv", ".venv", "node_modules", ".git", "__pycache__", "data", "dist", "build"}]
        depth = root[len(path):].count(os.sep)
        if depth > 2:
            continue
        tree.append("  " * depth + os.path.basename(root) + "/ " + " ".join(f for f in files[:12] if not f.endswith((".pyc", ".map"))))
    intro = open(readme, encoding="utf-8", errors="ignore").read(3500) if readme else ""
    trace(session, "model", f"reading {name}'s structure with the coder model", status="start")
    out = llm.ask(f"Project: {name}\nREADME excerpt:\n{intro}\n\nFolder tree:\n" + "\n".join(tree[:120])
                  + "\n\nExplain this codebase to its owner: what it does, the main parts and where they live, how a "
                    "request flows through it. Short sections, file paths in backticks.", role="coder", max_tokens=900)
    trace(session, "model", "done", status="done")
    return out


# ---------------------------------------------------------------- api, logs
def op_api_test(m, text, session):
    url, method = m.group("url").rstrip(".,"), (m.group("method") or "GET").upper()
    if method != "GET":
        return ask_first(session, "dev", f"Send a {method} request to {url} (it may change data there)",
                         lambda: _request(method, url))
    return _request(method, url)


def _request(method, url):
    t0 = time.time()
    try:
        r = requests.request(method, url, timeout=20)
    except requests.RequestException as exc:
        return f"{method} {url} failed: {type(exc).__name__}: {str(exc)[:150]}"
    ms = (time.time() - t0) * 1000
    body = r.text[:600]
    return (f"**{method} {url}** -> {r.status_code} {r.reason} in {ms:.0f} ms, {len(r.content):,} bytes, "
            f"{r.headers.get('content-type', '?')}\n```\n{body}\n```")


def op_pip_install(m, text, session):
    # Pick the package name: after "pip install" or after "install ... package ..."
    pkg = re.search(r"\bpip\s+install\s+(?:--\w+\s+)*\[?([a-zA-Z0-9._-]+)\]?", text, I)
    if pkg:
        name = pkg.group(1).rstrip(".,;:")
    else:
        # "package for me: requests" - the old pattern required whitespace after "me", so the colon in
        # "for me: requests" made the capture fall through to the word "for" and the gate asked to
        # "pip install for" (checklist 36). The tail is now optional-spaces and the name is screened
        # against filler words, so a bad capture asks which package instead of gating a nonsense install.
        m2 = re.search(r"install\s+(?:this\s+|the\s+)?(?:python\s+)?(?:package|library|module|package\s+named)\s*"
                       r"(?:for\s+me\s*)?(?::\s*)?(?P<pkg>[a-zA-Z][a-zA-Z0-9._-]{1,40})\b", text, I)
        if m2:
            name = m2.group("pkg")
        else:
            m3 = re.search(r"add\s+([a-zA-Z][a-zA-Z0-9._-]{1,40})\s+to", text, I)
            name = m3.group(1) if m3 else ""
        if name.lower() in {"for", "to", "it", "me", "the", "this", "that", "my", "a", "an"}:
            name = ""
    if not name:
        return "Which package?"
    name = name.strip().strip("'[]")

    def install():
        rc, out = sh([sys.executable, "-m", "pip", "install", name, "-q"], ROOT, timeout=180)
        if rc == 0:
            return f"Installed {name}.​"
        return f"pip install {name} failed:\n```\n{out[-600:]}\n```"

    return ask_first(session, "dev", f"Run `pip install {name}` on this PC", install)


def op_log_check(m, text, session):
    path = re.search(r"[A-Za-z]:\\[^\s\"']+", text)
    path = path.group(0) if path else scratch_path(os.path.join(ROOT, "data", "logs", "error_records.md"))
    if not os.path.isfile(path):
        return f"{path} doesn't exist."
    with open(path, encoding="utf-8", errors="replace") as f:
        lines = f.readlines()[-400:]
    errors = [l.strip() for l in lines if re.search(r"\b(?:error|exception|traceback|failed|fatal|critical)\b", l, I)]
    return (f"{os.path.basename(path)}: {len(lines)} recent lines, {len(errors)} with errors. Latest:\n"
            + "\n".join(f"- {e[:160]}" for e in errors[-10:])) if errors else f"No errors in the last {len(lines)} lines of {os.path.basename(path)}."


if __name__ == "__main__":
    cases = {"run this python code ```print(1)```": "run_code", r"run D:\x\tool.py": "run_file",
             "git status of the ishti project": "git_status", "commit my changes in ishti": "git_commit",
             "push the ishti code to github": "git_push", "run the tests": "tests", "lint the ishti project": "lint",
             "explain the ishti codebase": "explain", "test the api GET https://api.github.com": "api_test",
             "check the error log": "log_check",
             "install this python package for me: requests": "pip_install",
             "pip install numpy": "pip_install",
             "add pandas to my project": "pip_install"}
    for t, want in cases.items():
        got = next((n for n, rx in OPS if rx.search(t)), None)
        assert got == want, (t, got, want)
    assert not match("push the car") and not match("what's the weather")
    s = {}
    out = op_run_code(CODE.search("```python\nprint(6*7)\n```"), "run this code ```python\nprint(6*7)\n```", s)
    assert "42" in out, out
    print("[self-check] dev OK - routing, sandboxed snippet run")
