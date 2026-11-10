"""security_scan - local, deterministic security scanner for ISHTI's own surfaces (WP-ECC, matrix ECC-06).

Design ported from ECC's AgentShield concept (MIT pin in VENDOR_PINS.md): scan agent files, hooks,
MCP config, permissions and secrets - as ORIGINAL ISHTI rules re-derived for ISHTI's file layout.
No npm package is installed; nothing leaves this PC.

Surfaces scanned (all read-only):
  1. skill manifests  - tier/test/scope permission sanity
  2. skill code       - dynamic code, shell=True, secrets, verify=False, cleartext HTTP, dangerous deletes
  3. mcp config       - ishti/config.json mcp_servers: launcher allowlist, secrets in args
  4. tracked files    - files in the repo that must never be committed (.env, keys, token dumps)

Contract: run(text, session) -> reply dict | None; scan_all(root) -> list of finding dicts.
Finding = {"severity": high|medium|low, "surface", "file", "line", "rule", "detail", "fix"}.
Rules are deterministic regex/AST-lite checks - the same tree always yields the same report.
"""
from __future__ import annotations

import json
import os
import re

try:  # best-effort failure log (never breaks the module if logging is unavailable)
    from ishti.core.logutil import swallowed
except Exception:  # pragma: no cover
    def swallowed(*_a, **_k):
        return None


ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
SKILLS_DIR = os.path.join(ROOT, "ishti", "skills")

# --------------------------------------------------------------------------- rules
# (rule_id, severity, regex, detail-verb, fix) - re-derived for ISHTI, not copied rule text.
CODE_RULES = [
    ("dyn-code", "high", r"\b(eval|exec)\s*\(", "dynamic code execution", "replace with explicit parsing/whitelist"),
    ("shell-true", "high", r"subprocess\.[a-z_]+\([^)]*shell\s*=\s*True", "shell=True (command injection surface)", "pass an argv list with shell=False"),
    ("os-system", "high", r"\bos\.system\s*\(", "os.system call", "use run_cmd(argv) with a list"),
    ("pickle-load", "high", r"\bpickle\.loads?\s*\(", "pickle deserialization (arbitrary code if input is untrusted)", "use json for local state"),
    ("yaml-unsafe", "medium", r"\byaml\.load\s*\((?![^)]*SafeLoader)", "yaml.load without SafeLoader", "yaml.safe_load"),
    ("verify-false", "medium", r"verify\s*=\s*False", "TLS verification disabled", "remove verify=False (pin your CA instead)"),
    ("cleartext-http", "medium",
     r"(?<!s)http://"
     r"(?!(?:127\.0\.0\.1|localhost|0\.0\.0\.0)(?:[:/]|\b))"
     r"(?!(?:[\w-]+\.)*(?:w3\.org|openxmlformats\.org|purl\.org|schemas\.microsoft\.com|apache\.org)(?:[/\b']))"
     r"[A-Za-z0-9][^\s'\"]*",
     "cleartext http:// URL (not https)", "use https:// for anything not loopback"),
    ("plain-secret", "high", r"""(?i)\b(api[_-]?key|token|password|secret)\b\s*[:=]\s*["'][A-Za-z0-9_\-]{12,}["']""", "possible hardcoded credential", "move it to Windows Credential Manager / config with token_key"),
    ("aws-id", "high", r"\bAKIA[0-9A-Z]{16}\b", "AWS access key id in source", "rotate the key and remove it"),
    ("gh-token", "high", r"\bgh[pousr]_[A-Za-z0-9]{20,}\b", "GitHub token in source", "rotate the token and remove it"),
    ("openai-key", "high", r"\bsk-[A-Za-z0-9]{20,}\b", "API key pattern in source", "rotate and remove"),
    ("pem-block", "high", r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----", "private key in source", "rotate the key and remove it"),
    ("rm-rf", "medium", r"shutil\.rmtree\s*\(|\bos\.remove\s*\(", "destructive file call", "confirm the path is sandboxed (data/ or tmp)"),
    ("danger-flag", "high", r"--dangerously-skip|--no-sandbox|disable-web-security", "safety-bypass flag", "remove the bypass flag"),
]

MANIFEST_RULES = [
    ("t2-no-test", "medium", lambda m, p: m.get("tier") == "T2" and not m.get("test_file"),
     "T2 skill without a declared test_file", "declare a test_file before enabling"),
    ("net-wildcard", "medium", lambda m, p: "*" in (m.get("scope", {}).get("net") or []),
     "network scope is a wildcard (any host)", "list explicit hosts in scope.net"),
    ("status-drift", "low", lambda m, p: m.get("status") == "working" and not os.path.isfile(
        os.path.join(os.path.dirname(os.path.abspath(p)), "skill.py")),
     "status=working but skill.py missing", "set status to broken/disabled or restore skill.py"),
    ("t0-apps", "medium", lambda m, p: m.get("tier") == "T0" and m.get("scope", {}).get("apps") is True,
     "T0 (silent) skill controls apps", "T0 must never drive apps - raise the tier"),
    ("missing-tier", "medium", lambda m, p: m.get("tier") not in ("T0", "T1", "T2", "T3"),
     "manifest has no valid tier", "add tier T0-T3"),
]

# Launchers an MCP server may use (local, inspectable tools). Anything else = finding.
from ishti.mcp_client import MCP_LAUNCHERS, SERVERS_PATH as MCP_SERVERS_PATH  # one allowlist: what runs = what's scanned


def _rel(path: str, root: str | None = None) -> str:
    """Repo-relative path for reports; falls back to the full path when roots differ (other drive)."""
    try:
        return os.path.relpath(path, root or ROOT)
    except ValueError:
        return path


def _finding(severity, surface, file, line, rule, detail, fix):
    return {"severity": severity, "surface": surface, "file": file, "line": line,
            "rule": rule, "detail": detail, "fix": fix}


SELF = os.path.join("ishti", "skills", "security_scan", "skill.py")  # fixed repo-relative location:
# only the scanner's own file is exempt, so a new skill cannot claim the exemption by name.


def scan_code(path: str, root: str | None = None):
    out = []
    try:
        if os.path.normcase(_rel(path, root)) == os.path.normcase(SELF):
            return out  # the scanner's own rule table + fixtures are not findings (documented self-exclusion)
    except ValueError:
        pass
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except OSError:
        return out
    # Precision rule (documented recall trade-off): ISHTI convention puts test fixtures inside the
    # `if __name__ == "__main__":` self-check block, which never runs in production - pattern rules
    # skip that block so security fixtures are not reported as vulnerabilities.
    main_indent = None
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if main_indent is None and stripped.startswith('if __name__ == "__main__":'):
            main_indent = len(line) - len(line.lstrip())
            continue
        if main_indent is not None:
            if not stripped:
                continue
            if (len(line) - len(line.lstrip())) > main_indent:
                continue
            main_indent = None  # dedented back to module level: the block ended
        if stripped.startswith("#"):
            continue  # comments explain risks; they are not code
        for rule, sev, rx, verb, fix in CODE_RULES:
            if re.search(rx, line):
                if "nosec" in line:          # explicit, reviewed suppression
                    continue
                out.append(_finding(sev, "code", _rel(path, root), i, rule, verb, fix))
    return out


def scan_manifest(path: str):
    out = []
    try:
        with open(path, encoding="utf-8") as f:
            m = json.load(f)
    except (OSError, ValueError) as exc:
        return [_finding("high", "manifest", _rel(path), 1, "manifest-unreadable",
                         f"manifest does not parse: {exc}", "fix the JSON")]
    for rule, sev, pred, verb, fix in MANIFEST_RULES:
        try:
            if pred(m, path):
                out.append(_finding(sev, "manifest", _rel(path), 0, rule, verb, fix))
        except Exception:
            continue
    return out


def scan_mcp(config_path: str):
    out = []
    try:
        with open(config_path, encoding="utf-8") as f:
            cfg = json.load(f)
    except (OSError, ValueError) as exc:
        return [_finding("high", "mcp", _rel(config_path), 1, "mcp-unreadable",
                         f"config does not parse: {exc}", "fix the JSON")]
    servers = cfg.get("mcp_servers") or {}
    rel = _rel(config_path)
    for name, spec in servers.items():
        cmd = str(spec.get("command") or "")
        args = [str(a) for a in (spec.get("args") or [])]
        if os.path.basename(cmd.split(".")[0]) not in MCP_LAUNCHERS and os.path.basename(cmd) not in MCP_LAUNCHERS:
            out.append(_finding("medium", "mcp", rel, 0, "mcp-launcher",
                                f"server '{name}' launches '{cmd}' (not on the local launcher allowlist)",
                                "review the launcher; allowlist it deliberately if intended"))
        joined = " ".join(args)
        for rule, sev, rx, verb, fix in CODE_RULES:
            if rule in ("cleartext-http", "dyn-code"):
                continue
            if re.search(rx, joined):
                out.append(_finding(sev, "mcp", rel, 0, rule,
                                    f"server '{name}' args: {verb}", fix))
        if re.search(r"(?i)\b(token|secret|password|api[_-]?key)\b\s*=\s*\S+", joined):
            out.append(_finding("high", "mcp", rel, 0, "mcp-arg-secret",
                                f"server '{name}' appears to embed a secret in its args",
                                "put it in env as keyring:<name> (say: set mcp secret <name> <value>), never inline"))
        if spec.get("token_key") and os.path.isfile(os.path.join(ROOT, "data", "mcp_tokens.json")):
            pass  # indirection present: fine
    return out


def scan_git_tracked(repo_root: str = ROOT):
    """Files that must never be tracked: .env, private keys, token dumps (git ls-files, offline)."""
    import subprocess
    out = []
    try:
        p = subprocess.run(["git", "-C", repo_root, "ls-files"], capture_output=True, timeout=30)
        files = (p.stdout or b"").decode("utf-8", "replace").splitlines()
    except Exception:
        return out
    bad = re.compile(r"(^|/)\.env$|id_rsa|id_ed25519|\.pem$|credentials\.json$|.*token.*\.txt$", re.I)
    for f in files:
        if bad.search(f) and not f.endswith(".example") and ".example" not in f:
            out.append(_finding("high", "git", f, 0, "tracked-secret",
                                "file that looks like credentials is tracked by git",
                                "git rm --cached it, rotate whatever it held, add to .gitignore"))
    return out


def scan_all(root: str | None = None):
    root = root or ROOT
    findings = []
    skills_dir = os.path.join(root, "ishti", "skills")
    if os.path.isdir(skills_dir):
        for d in sorted(os.listdir(skills_dir)):
            folder = os.path.join(skills_dir, d)
            man = os.path.join(folder, "manifest.json")
            if os.path.isfile(man):
                findings += scan_manifest(man)
            if os.path.isdir(folder):
                for f in sorted(os.listdir(folder)):
                    if f.endswith(".py"):
                        findings += scan_code(os.path.join(folder, f), root=root)
    cfg = os.path.join(root, "ishti", "config.json")
    for cfg in (cfg, MCP_SERVERS_PATH):  # hand-edited config.json and the servers added in chat
        if os.path.isfile(cfg):
            findings += scan_mcp(cfg)
    findings += scan_git_tracked(root)
    order = {"high": 0, "medium": 1, "low": 2}
    findings.sort(key=lambda f: (order.get(f["severity"], 3), f["file"], f["line"]))
    return findings


def report(findings, limit: int = 40) -> str:
    if not findings:
        return "Security scan: 0 findings - manifests, code, MCP config and tracked files look clean."
    counts = {}
    for f in findings:
        counts[f["severity"]] = counts.get(f["severity"], 0) + 1
    head = (f"Security scan: {len(findings)} finding(s) - "
            f"high {counts.get('high', 0)}, medium {counts.get('medium', 0)}, low {counts.get('low', 0)}.")
    lines = [head, "", "| sev | surface | file:line | rule | detail | fix |", "|---|---|---|---|---|---|"]
    for f in findings[:limit]:
        loc = f["file"] + (f":{f['line']}" if f["line"] else "")
        row = [f["severity"], f["surface"], loc, f["rule"], f["detail"], f["fix"]]
        lines.append("| " + " | ".join(str(c).replace("|", "/").replace("\n", " ") for c in row) + " |")
    if len(findings) > limit:
        lines.append(f"\n({len(findings) - limit} more not shown - ask for 'security scan json'.)")
    return "\n".join(lines)


_TRIGGERS = [
    re.compile(r"\bsecurity[- ]scan\b", re.I),
    re.compile(r"\bscan\b.+\b(security|skills|mcp|secrets)\b", re.I),
    re.compile(r"\baudit\b.+\b(security|manifest|mcp)\b", re.I),
    re.compile(r"\bcheck\b.+\bsecrets?\b.+\b(repo|repo|code|files)\b", re.I),
]


def match(text: str) -> bool:
    return any(rx.search(str(text or "")) for rx in _TRIGGERS)


def run(text, session):
    t = str(text or "").strip()
    if not t or not match(t):
        return None
    from ishti.skills import trace
    as_json = bool(re.search(r"\bjson\b", t, re.I))
    try:
        trace(session, "security_scan", "scanning ISHTI surfaces (local, read-only)", status="done")
    except Exception as _exc:
        swallowed('ishti/skills/security_scan/skill.py:run', _exc)
    findings = scan_all()
    if as_json:
        return {"reply": "```\n" + json.dumps(findings, indent=2, ensure_ascii=False) + "\n```"}
    return {"reply": report(findings)}


# --------------------------------------------------------------------------- self-check
if __name__ == "__main__":
    import tempfile
    tmp = tempfile.mkdtemp()
    os.makedirs(os.path.join(tmp, "ishti", "skills"))
    # seeded bad manifest + bad code + bad mcp config must ALL be caught
    bad_man = {"name": "evil", "tier": "T2", "scope": {"net": ["*"], "apps": True}, "status": "working"}
    folder = os.path.join(tmp, "ishti", "skills", "evil")
    os.makedirs(folder)
    json.dump(bad_man, open(os.path.join(folder, "manifest.json"), "w"))
    os.makedirs(os.path.join(tmp, "ishti", "skills", "sneaky"))
    json.dump({"name": "sneaky", "tier": "T0", "scope": {"net": [], "apps": True}, "status": "working"},
              open(os.path.join(tmp, "ishti", "skills", "sneaky", "manifest.json"), "w"))
    open(os.path.join(folder, "skill.py"), "w").write(
        "import os, subprocess\nAPI_KEY = \"sk-abcdefghijklmnop123456\"\n"
        "subprocess.run('rm -rf /', shell=True)\neval(input())\nrequests.get('http://x.test', verify=False)\n")
    os.makedirs(os.path.join(tmp, "ishti"), exist_ok=True)
    json.dump({"mcp_servers": {"weird": {"command": "evil.exe", "args": ["--token=abcd1234efgh5678"]}}},
              open(os.path.join(tmp, "ishti", "config.json"), "w"))
    fs = scan_all(tmp)
    rules = {f["rule"] for f in fs}
    for need in ("t2-no-test", "net-wildcard", "t0-apps", "plain-secret", "shell-true",
                 "dyn-code", "verify-false", "cleartext-http", "mcp-launcher", "mcp-arg-secret"):
        assert need in rules, f"missed rule {need}: {sorted(rules)}"
    assert fs and fs[0]["severity"] in ("high", "medium", "low")
    # nosec suppression works
    d2 = os.path.join(tmp, "ishti", "skills", "evil2")
    os.makedirs(d2)
    open(os.path.join(d2, "skill.py"), "w").write("x = eval(y)  # nosec - reviewed: whitelisted grammar\n")
    assert not any(f["rule"] == "dyn-code" and "evil2" in f["file"] for f in scan_all(tmp))
    # report shape
    rep = report(fs)
    assert "finding" in rep and "| sev |" in rep
    # run() routing: trigger answers, other text declines
    assert run("zzz nope", {}) is None
    out = run("security scan json", {})
    assert out and out["reply"].lstrip().startswith("```")
    print(f"[self-check] security_scan OK - {len(fs)} seeded findings caught, rules={sorted(rules)}")
