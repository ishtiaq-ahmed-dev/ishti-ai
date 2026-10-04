"""Sandboxed terminal execution (B5): a restricted subprocess — never a shell — with
allow/deny lists from Settings, a confined working directory, capped output, hard timeout,
and the T2 confirmation gate for anything that changes the system.

Design: the LLM never picks arbitrary commands; it proposes one, the sandbox validates the
EXECUTABLE (shlex parsing, no shell, no interpolation) against the allow/deny lists, and
write-shaped commands go through ask_first. Long-running tools (pytest) get a generous
timeout; everything is captured, truncated and reported.
"""
from __future__ import annotations

import os
import re
import shlex
import subprocess
import time

from ishti import ROOT

MAX_OUTPUT = 6000
DEFAULT_TIMEOUT = 45

# write-shaped: these change state -> always T2 (even when their exe is allow-listed)
WRITE_HINTS = re.compile(
    r"\b(?:install|uninstall|remove|delete|push|commit|reset|clean|mv|move|rm|del|copy|cp|"
    r"mkdir|chmod|chown|pip\s+install|npm\s+(?:i|install)|git\s+(?:push|reset|clean|rm))\b", re.I)
# read-only: safe to run without confirmation when the exe is allow-listed
READ_ONLY = re.compile(
    r"^(?:git\s+(?:status|log|diff|show|branch)|pytest|pip\s+(?:list|show|freeze)|python\s+--version|"
    r"python\s+-[vc]\s|node\s+--version|npm\s+(?:ls|run\s+test)|dir|ls|echo|type|where|get-?location)\b", re.I)


def _lists() -> tuple[list, list]:
    try:
        from ishti.core.settings import get_settings_service
        s = get_settings_service().get().get("skills", {})
        return (s.get("terminal_allow") or [], s.get("terminal_deny") or [])
    except Exception:
        DEFAULT_ALLOW = ["git", "python", "pip", "pytest", "node", "npm", "dir", "ls", "echo"]
        DEFAULT_DENY = ["format", "rd", "rmdir", "del", "erase", "diskpart", "reg", "shutdown", "taskkill"]
        return DEFAULT_ALLOW, DEFAULT_DENY


def parse(command: str) -> dict:
    """{ok, exe, args, write, reason} — shlex-parsed (no shell), first token = executable.
    Deny-list wins over allow-list; unknown executables are refused (allow-list only)."""
    cmd = (command or "").strip().strip("`")
    if not cmd:
        return {"ok": False, "reason": "empty command"}
    # shell operators are refused, but only OUTSIDE quoted strings (a python -c "a;b" is one command)
    try:
        quoted_mask = re.sub(r'"[^"]*"|\'[^\']*\'', "", cmd)
    except Exception:
        quoted_mask = cmd
    if any(ch in quoted_mask for ch in ("|", "&&", "||", ";", ">", "<")):
        return {"ok": False, "reason": "shell operators are not allowed (one command, no shell)"}
    try:
        parts = shlex.split(cmd, posix=False)
    except ValueError as exc:
        return {"ok": False, "reason": f"unparseable command ({exc})"}
    if not parts:
        return {"ok": False, "reason": "empty command"}
    exe = parts[0].strip('"').lower()
    args = [p.strip('"') for p in parts[1:]]
    allow, deny = _lists()
    if any(exe == d.lower() or exe.startswith(d.lower()) for d in deny):
        return {"ok": False, "exe": exe, "reason": f"'{exe}' is deny-listed (Settings -> Skills)"}
    if allow and not any(exe == a.lower() or exe.startswith(a.lower()) for a in allow):
        return {"ok": False, "exe": exe,
                "reason": f"'{exe}' is not on the terminal allow-list (Settings -> Skills -> terminal_allow)"}
    write = bool(WRITE_HINTS.search(cmd)) and not READ_ONLY.match(cmd)
    return {"ok": True, "exe": exe, "args": args, "write": write,
            "reason": "read-only" if not write else "changes the system"}


def execute(command: str, timeout: int = DEFAULT_TIMEOUT, cwd: str | None = None) -> dict:
    """Run ONE allow-listed command in a restricted subprocess. Returns
    {ok, output, code, ms, truncated}. The caller decides about T2 confirmation;
    this function assumes approval already happened."""
    p = parse(command)
    if not p["ok"]:
        return {"ok": False, "output": p["reason"], "code": -1, "ms": 0, "truncated": False}
    t0 = time.time()
    workdir = cwd or ROOT
    try:
        proc = subprocess.run([p["exe"], *p["args"]], cwd=workdir, capture_output=True,
                              text=True, timeout=timeout, shell=False,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        out = (proc.stdout or "") + (("\n[stderr] " + proc.stderr) if proc.stderr.strip() else "")
        code = proc.returncode
    except subprocess.TimeoutExpired:
        return {"ok": False, "output": f"timed out after {timeout}s", "code": -2,
                "ms": int((time.time() - t0) * 1000), "truncated": False}
    except FileNotFoundError:
        return {"ok": False, "output": f"'{p['exe']}' not found on this machine", "code": -3,
                "ms": 0, "truncated": False}
    except OSError as exc:
        return {"ok": False, "output": f"{type(exc).__name__}: {exc}", "code": -4,
                "ms": int((time.time() - t0) * 1000), "truncated": False}
    truncated = len(out) > MAX_OUTPUT
    return {"ok": code == 0, "output": out[:MAX_OUTPUT] + ("\n[output truncated]" if truncated else ""),
            "code": code, "ms": int((time.time() - t0) * 1000), "truncated": truncated}


def match(text: str) -> bool:
    """Deterministic trigger: explicit run/execute/terminal asks (before Laya)."""
    t = (text or "").lower().strip()
    if len(t.split()) > 25:
        return False
    # "Run 8 background searches at once and keep answering me" (checklist #75) is a background-task
    # request, not a shell command. "run <something>" only counts when the something looks like a command:
    # a known CLI tool, a flag, a path, or a quoted command - never plain English ("run a marathon").
    looks_like_cmd = bool(re.match(r"^(?:git|pytest|pip|npm|npx|node|python|py|ruff|winget|docker|kubectl|curl|ffmpeg|"
                                   r"dir|ls|cd|mkdir|where|tasklist|netstat|ipconfig)\b", t)
                         or re.search(r"\s(?:--?\w+|\./|\w:\\|\w+/\w+|\.(?:py|js|ts|sh|bat|ps1|exe)\b)", t)
                         or re.search(r"`[^`]+`|\"[^\"]+\"|'[^']+'", t))
    return bool(re.search(r"\b(?:run|execute)\s+(?:the\s+)?(?:command|terminal)\b", t)
                or (re.search(r"\bterminal\b", t) and re.search(r"\b(?:run|in|open|use)\b", t))
                or (t.startswith(("run ", "execute ", "terminal:", "$ ")) and looks_like_cmd)
                or re.match(r"^(?:git|pytest|pip|npm|node)\s", t))


def run(text: str, session) -> dict:
    """Skill entry: 'run git status', 'terminal: pytest -q', 'execute python -V'..."""
    from ishti.skills import trace, ask_first
    cmd = text
    m = re.search(r"(?:terminal|run|execute|command)\s*[::]?\s*(.+)", text, re.I)
    if m and not re.search(r"\b(?:in|on)\s+(?:the\s+)?terminal\b", m.group(1), re.I):
        cmd = m.group(1).strip()
    p = parse(cmd)
    if not p["ok"]:
        return {"reply": f"I won't run that: {p['reason']}.", "sources": [], "no_escalate": True}
    if p["write"]:
        return ask_first(session, "terminal", f"Run '{cmd[:100]}' (it changes the system)?",
                         lambda: _emit(execute(cmd), cmd, session))
    return _emit(execute(cmd), cmd, session)


def _emit(res: dict, cmd: str, session) -> dict | str:
    label = f"$ {cmd[:60]} -> {'ok' if res['ok'] else 'exit ' + str(res['code'])}"
    from ishti.skills import trace
    trace(session, "terminal", label, status="done" if res["ok"] else "error")
    out = res["output"].strip() or "(no output)"
    reply = f"```\n{out}\n```\n({res['ms']} ms, exit {res['code']})"
    return reply if isinstance(res, dict) else reply


if __name__ == "__main__":
    import tempfile
    import ishti.resilience  # noqa: F401  (make sure the venv stack loads)
    # parsing: allow/deny, shell-op refusal, write detection
    p = parse("git status")
    assert p["ok"] and not p["write"], p
    p2 = parse("pip install requests")
    assert p2["ok"] and p2["write"], p2
    p3 = parse("format c:")
    assert not p3["ok"] and "deny" in p3["reason"], p3
    p4 = parse("powershell Remove-Item foo")
    assert not p4["ok"], p4  # not on the allow-list
    p5 = parse("git status && del secrets")
    assert not p5["ok"], p5  # shell operators refused
    p6 = parse("del file.txt")
    assert not p6["ok"], p6  # deny-listed even with args
    # execution: real read-only command through the sandbox
    r = execute("python --version", timeout=30)
    assert r["ok"] and "Python" in r["output"], r
    r2 = execute("python -c \"print('hello-ishti')\"", timeout=30)
    assert r2["ok"] and "hello-ishti" in r2["output"], r2
    r3 = execute("nope_missing_exe --x", timeout=10)
    assert not r3["ok"] and ("allow-list" in r3["output"] or "not found" in r3["output"]), r3
    # timed-out command reports cleanly
    r4 = execute("python -c \"import time; time.sleep(20)\"", timeout=2)
    assert not r4["ok"] and "timed out" in r4["output"], r4
    # the T2 path is decided in run() — verify the decision logic only (no UI here)
    assert parse("git push origin main")["write"] is True
    assert parse("git log --oneline")["write"] is False
    print("[self-check] terminal OK — parse (allow/deny/no-shell/write-detect), execute (real cmd, missing exe, timeout)")
