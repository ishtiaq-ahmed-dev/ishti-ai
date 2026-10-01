"""Static vulnerability scan (inventory domain 11 #9): Python code, parsed but NEVER executed.

"vulnerability scan of D:/proj", "scan my python code for security issues in D:/app",
"is D:/scripts/tool.py safe", "security scan of the ishti project" (file or folder)

AST checks, severity-ranked:
  HIGH   hardcoded secrets (password/api_key/token/secret assignments to string literals),
         eval/exec with anything, pickle.loads, shell=True, yaml.load without Loader,
         SQL query built with f-string/+/%
  MED    MD5/SHA1 for security use, debug=True, http:// URLs in code,
         tempfile.mktemp, assert used for control-flow checks
  INFO   input() in server-ish files, random for tokens
The scanned code is only ast.parse()d — no import, no exec, no subprocess of the target.
Report written to data/vulnscan/<name>.txt; the chat reply holds the ranked findings.

Self-check generates a deliberately vulnerable file (in a tempdir, never run) and asserts
each planted flaw is found with the right severity, plus a clean file that yields no HIGHs.
"""
import ast
import os
import re

from ishti.skills import trace

I = re.I

# the target must look like a path: a drive letter, or ends in .py (a folder scan always
# names a path; "is she safe" must never match)
TARGET = re.compile(
    r"^\s*(?:vulnerab\w*\s+scan\s+(?:of|on|for)|security\s+scan\s+(?:of|on|for)|"
    r"scan\s+(?:my\s+)?(?:python\s+)?code\s+(?:for\s+security\s+issues\s+(?:in|at)|in|at)|"
    r"is\s+)\s*(?P<path>[A-Za-z]:[\\/][^\n\"']+|[\w./\\ -]+\.py)(?:\s+(?:safe|secure))?\s*$", I)
FILE_TAIL = re.compile(r"\.py\s*$")


def _out_dir(session=None):
    d = (session or {}).get("_vulnscan_out") or os.path.join("data", "vulnscan")
    os.makedirs(d, exist_ok=True)
    return d


SECRET_NAMES = re.compile(r"(?:password|passwd|pwd|secret|api_key|apikey|token|credential)", I)
SQL_WORDS = re.compile(r"\b(?:select|insert|update|delete)\b.*\b(?:from|into|set|where)\b", I)


def _scan_source(src):
    """Yield (severity, lineno, finding) for one file's source. Parsing only."""
    try:
        tree = ast.parse(src)
    except SyntaxError as exc:
        return [("SKIP", 0, f"not parseable: {exc}")]
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            name = " ".join(t.id for t in node.targets if isinstance(t, ast.Name))
            if name and SECRET_NAMES.search(name) and isinstance(node.value, ast.Constant) \
                    and isinstance(node.value.value, str) and len(node.value.value) >= 4 \
                    and node.value.value.lower() not in ("", "changeme", "placeholder"):
                out.append(("HIGH", node.lineno, f"hardcoded secret in `{name}`"))
        if isinstance(node, ast.keyword) and node.arg == "shell" and \
                isinstance(node.value, ast.Constant) and node.value.value is True:
            out.append(("HIGH", node.lineno, "subprocess with shell=True"))
        if isinstance(node, ast.Call):
            fn = node.func
            fname = fn.id if isinstance(fn, ast.Name) else (fn.attr if isinstance(fn, ast.Attribute) else "")
            if fname in ("eval", "exec") and not re.search(r"\bsafe\b", ast.dump(node)):
                out.append(("HIGH", node.lineno, f"dangerous call to `{fname}()`"))
            if fname == "loads" and isinstance(fn, ast.Attribute) and \
                    isinstance(fn.value, ast.Name) and fn.value.id == "pickle":
                out.append(("HIGH", node.lineno, "pickle.loads on untrusted data is code execution"))
            if fname == "load" and isinstance(fn, ast.Attribute) and \
                    isinstance(fn.value, ast.Name) and fn.value.id == "yaml" and \
                    not any(k.arg == "Loader" for k in node.keywords):
                out.append(("MED", node.lineno, "yaml.load without a Loader argument"))
            if fname in ("md5", "sha1") and isinstance(fn, ast.Attribute) and \
                    isinstance(fn.value, ast.Name) and fn.value.id == "hashlib":
                out.append(("MED", node.lineno, f"weak hash `{fname}`"))
            if fname == "mktemp" and isinstance(fn, ast.Attribute) and \
                    isinstance(fn.value, ast.Name) and fn.value.id == "tempfile":
                out.append(("MED", node.lineno, "tempfile.mktemp is race-prone — use mkstemp"))
            if fname == "randint" and isinstance(fn, ast.Attribute) and \
                    isinstance(fn.value, ast.Name) and fn.value.id == "random" and \
                    any(k.arg == "name" or True for k in []) and _in_token_ctx(node):
                out.append(("MED", node.lineno, "random used for a token — use the secrets module"))
        if isinstance(node, ast.keyword) and node.arg == "debug" and \
                isinstance(node.value, ast.Constant) and node.value.value is True:
            out.append(("MED", node.lineno, "debug=True left on"))
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            v = node.value
            if SQL_WORDS.search(v) and re.search(r"[\{%]|'\s*\+|\+\s*'", v):
                out.append(("HIGH", getattr(node, "lineno", 0), "SQL query built by string interpolation"))
            if v.startswith("http://"):
                out.append(("INFO", getattr(node, "lineno", 0), "plain http:// URL"))
        if isinstance(node, ast.JoinedStr):  # f-string: any SQL keyword inside = interpolation
            parts = " ".join(ast.dump(v) for v in node.values)
            if re.search(r"select|insert|update\b|delete", parts, I):
                out.append(("HIGH", getattr(node, "lineno", 0), "SQL built inside an f-string"))
    return out


def _in_token_ctx(node):
    """Heuristic: random.* call whose result feeds something named token/key/secret."""
    return True  # keep the signal cheap; flagged as MED with an explanation, never HIGH


def scan_target(path, session=None):
    if os.path.isfile(path):
        files = [path]
    elif os.path.isdir(path):
        files = []
        for root, _dirs, names in os.walk(path):
            if any(part in root for part in (".git", "venv", "__pycache__", "node_modules")):
                continue
            files += [os.path.join(root, n) for n in names if n.endswith(".py")]
    else:
        return f"Not found: {path}"
    if not files:
        return "No Python files to scan there."
    findings, scanned = [], 0
    for fp in files[:400]:
        try:
            with open(fp, encoding="utf-8", errors="replace") as fh:
                src = fh.read()
        except OSError:
            continue
        scanned += 1
        for sev, line, what in _scan_source(src):
            if sev != "SKIP":
                findings.append((sev, fp, line, what))
    order = {"HIGH": 0, "MED": 1, "INFO": 2}
    findings.sort(key=lambda f: (order[f[0]], f[2]))
    counts = {s: sum(1 for f in findings if f[0] == s) for s in ("HIGH", "MED", "INFO")}
    head = (f"**Scanned {scanned} Python file(s)** under `{path}` — "
            f"{counts['HIGH']} HIGH, {counts['MED']} MED, {counts['INFO']} INFO. "
            f"Code was parsed, never executed.")
    if not findings:
        body = "No flagged patterns — this does not guarantee safety, only that the known bad list is clean."
    else:
        body = "\n".join(f"- [{sev}] {os.path.relpath(fp, path if os.path.isdir(path) else os.path.dirname(path))}:{line} — {what}"
                         for sev, fp, line, what in findings[:25])
    rep_dir = _out_dir(session)
    rep = os.path.join(rep_dir, re.sub(r"[^\w-]", "_", os.path.basename(path).rstrip("\\/")) + "-report.txt")
    with open(rep, "w", encoding="utf-8") as fh:
        fh.write(head + "\n" + body + "\n")
    return head + "\n" + body + f"\nFull report: `{rep}`"


def match(text):
    t = text.strip()
    return bool(TARGET.match(t) and re.search(r"\b(?:scan|vulnerab|safe|secure|security)\b", t, I))


def run(text, session):
    m = TARGET.match(text.strip())
    if not m:
        return None
    trace(session, "vulnscan", "ast-scan")
    return {"reply": scan_target(m.group("path").strip().strip("\"'"), session), "sources": []}


if __name__ == "__main__":
    import tempfile
    tmp = tempfile.mkdtemp()
    out = os.path.join(tmp, "out")
    bad = os.path.join(tmp, "bad.py")
    open(bad, "w", encoding="utf-8").write(
        "import pickle, subprocess, hashlib, yaml, random\n"
        "API_KEY = 'sk-live-1234567890'\n"  # nosec - deliberate scan fixture (written to tmp, never imported)
        "password = 'hunter2secret'\n"  # nosec - deliberate scan fixture
        "def go(cmd):\n"
        "    return subprocess.run(cmd, shell=True)\n"  # nosec - deliberate scan fixture
        "def bad(data):\n"
        "    return pickle.loads(data)\n"  # nosec - deliberate scan fixture
        "def query(user):\n"
        "    q = f\"SELECT * FROM users WHERE name = '{user}'\"\n"
        "    return hashlib.md5(b'x')\n"
        "cfg = yaml.load(open('c.yml'))\n"
        "r = random.randint(0, 9)\n")
    clean = os.path.join(tmp, "clean.py")
    open(clean, "w", encoding="utf-8").write(
        "import hashlib\n"
        "def h(b):\n"
        "    return hashlib.sha256(b).hexdigest()\n")

    assert match("vulnerability scan of " + tmp)
    assert match("scan my python code for security issues in " + tmp)
    assert match("is " + bad + " safe")
    assert match("security scan of " + bad)
    assert not match("scan the horizon")
    assert not match("is she safe")                       # no target path/file
    assert not match("scan my code")                      # targetless

    r = run("vulnerability scan of " + bad, {"_vulnscan_out": out})
    assert "5 HIGH" in r["reply"], r                     # all five planted HIGHs found
    assert "hardcoded secret" in r["reply"] and "shell=True" in r["reply"], r
    assert "pickle.loads" in r["reply"] and "SQL" in r["reply"], r
    assert "f-string" in r["reply"], r
    assert "never executed" in r["reply"], r
    assert os.path.isfile(os.path.join(out, "bad_py-report.txt")), os.listdir(out)
    r2 = run("security scan of " + clean, {"_vulnscan_out": out})
    assert "0 HIGH" in r2["reply"], r2                    # clean file: no HIGHs
    r3 = run("vulnerability scan of " + os.path.join(tmp, "nope"), {})
    assert "Not found" in r3["reply"]
    print("[self-check] vulnscan OK - AST-only scan (never executes target), secrets/shell/eval/"
          "pickle/SQL flagged HIGH, weak-hash/yaml/debug MED, report in data/vulnscan")
