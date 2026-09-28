"""Git operations, safe subset (inventory domain 7 #33).

"git status of the ishti project", "git log of D:/proj", "show branches of D:/proj",
"which branch am I on in D:/proj", "git diff of D:/proj" - read-only, run directly.
"commit my changes in D:/proj with message fix readme", "push D:/proj", "pull D:/proj",
"stash my changes in D:/proj" - state-changing: the exact command is shown and the action
is queued for T2 confirmation; nothing runs until the owner says yes.

Never matched, never built: force-push, hard reset to remote states, clean -fd outside the
confirm gate wording, rebase, filter-branch - destructive history rewriting is refused in
code, not gated (the ledger's rule: refused things are never attempted).

How commands run: git is invoked with cwd=<repo> via subprocess, output captured, never
shell=True. Path comes from the request or falls back to the ISHTI checkout itself.
Self-check builds a throwaway git repo in a tempdir (git is on PATH here) and never touches
the real checkout's state; the confirm queue writes only into a tempdir DB.
"""
import os
import re
import subprocess

from ishti.skills import trace

I = re.I

PATH = r"(?P<path>[A-Za-z]:[\\/][^\"']+?|[\w./\\ -]+?)(?:\s+(?:repo|project|repository|folder|dir|directory))?"

READS = [
    ("status", re.compile(r"^\s*(?:git\s+)?status\b(?:\s+of\b|\s+for\b)?\s*" + PATH + r"\s*$", I)),
    # "log" must be git-flavoured: "git log ...", or bare "log of/for <repo>";
    # "log into my portal" / "log sleep 7.5 hours" are other skills'
    ("log", re.compile(r"^\s*(?:git\s+log\b|log\s+(?:of|for)\b)(?:\s+(?:of|for)\b)?\s*" + PATH + r"\s*$", I)),
    ("diff", re.compile(r"^\s*(?:git\s+)?diff\b(?:\s+of\b|\s+for\b)?\s*" + PATH + r"\s*$", I)),
    ("branches", re.compile(r"^\s*(?:git\s+)?(?:show\s+|list\s+)?branches\b(?:\s+of\b|\s+for\b)?\s*" + PATH + r"\s*$", I)),
    ("current", re.compile(r"^\s*(?:which|what)\s+branch\s+am\s+i\s+on\b(?:\s+(?:in|on)\s+)?" + PATH + r"\s*$", I)),
]

# refused outright - history rewriting and remote destruction are never offered, gated or not.
# Any "clean" request is refused: removing untracked files is destructive by nature.
REFUSE = re.compile(r"\bforce\s+push\b|\bpush\s+--force\b|\bf-push\b|\B--force\b|"
                    r"\bhard\s+reset\b|\breset\s+--hard\b|\bfilter-branch\b|\brebase\b|"
                    r"\bclean\b|\bdelete\s+(?:the\s+)?(?:remote|origin)\b", I)

# writes: every variant needs a real target - a path-ish token after "in/on/of" (with a word
# boundary so "of subscription" can never parse as "of" + path) or an absolute drive path.
WRITES = [
    ("commit", re.compile(r"^\s*(?:git\s+)?commit\b.*?\s+(?:in|on|of)\s+" + PATH, I)),
    ("push", re.compile(r"^\s*(?:git\s+)?push\b(?:\s+to\s+\w+)?(?:\s+(?:in|on|of)\s+" + PATH + r"|\s+(?P<repo2>[A-Za-z]:[\\/][^\"']+))?\s*$", I)),
    ("pull", re.compile(r"^\s*(?:git\s+)?pull\b(?:\s+(?:in|on|of)\s+" + PATH + r"|\s+(?P<repo2>[A-Za-z]:[\\/][^\"']+))?\s*$", I)),
    ("add", re.compile(r"^\s*(?:git\s+)?add\b.*?\s+(?:in|on|of)\s+" + PATH, I)),
    ("stash", re.compile(r"^\s*(?:git\s+)?stash\b(?:\s+my\s+changes)?(?:\s+(?:in|on|of)\s+" + PATH + r")?\s*$", I)),
    ("checkout", re.compile(r"^\s*(?:git\s+)?(?:checkout|switch)\s+to\s+\S+.*?\s+(?:in|on|of)\s+" + PATH, I)),
]

DEFAULT_REPO = os.getcwd()
_QUEUE = []   # pending confirmed-writes for this session; the T2 gate owns the real flow


def _git(repo, *args):
    """Run one git command in the repo; capture output; never shell; friendly on failure."""
    if not os.path.isdir(os.path.join(repo or "", ".git")):
        return f"Not a git repository: {repo}"
    try:
        out = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=30)
        text = (out.stdout or "").strip()
        err = (out.stderr or "").strip()
        return text or err or ""     # "" is a real answer (clean porcelain prints nothing)
    except FileNotFoundError:
        return "git is not installed or not on PATH."
    except subprocess.TimeoutExpired:
        return "git timed out after 30 s."
    except Exception as exc:
        return f"git failed: {exc}"


def _repo_of(m, text):
    """The repo a write/read targets: the regex's path if it really is a directory, else a
    greedy re-scan of the (message-stripped) request, else the ISHTI checkout itself."""
    if m:
        p = (m.groupdict().get("path") or m.groupdict().get("repo2") or "").strip().strip("\"'")
        if p and os.path.isdir(p):
            return p
    m2 = re.search(r"\b(?:in|on|of)\s+([A-Za-z]:[\\/][^\"']+|[\w./\\ -]+)", text, I)
    if m2:
        cand = m2.group(1).strip().strip("\"'")
        if os.path.isdir(cand):
            return cand
    if re.search(r"\b(?:ishti|this)\s+(?:repo|project)\b", text, I):
        return DEFAULT_REPO
    return DEFAULT_REPO


def _steps_for(kind, text):
    """The exact git steps a confirmed write will run (lists of args, git implied)."""
    if kind == "commit":
        msg = commit_message_from(text) or "ISHTI: owner-confirmed commit"
        return [["add", "-A"], ["commit", "-m", msg]]
    if kind == "push":
        return [["push"]]
    if kind == "pull":
        return [["pull"]]
    if kind == "add":
        return [["add", "-A"]]
    if kind == "stash":
        return [["stash", "push", "-m", "ISHTI stash"]]
    if kind == "checkout":
        m = re.search(r"\b(?:checkout|switch)\s+to\s+([\w./-]+)", text, I)
        return [["checkout", m.group(1)]] if m else [["checkout"]]
    return []


def _queue_write(kind, repo, text):
    """State-changing command: show the exact steps, queue them for the owner's yes."""
    steps = _steps_for(kind, text)
    _QUEUE.append({"kind": kind, "repo": repo, "steps": steps, "request": text})
    shown = " && ".join("git " + " ".join(s) for s in steps) or "(nothing)"
    return (f"**{kind}** in `{repo}` is state-changing, so I won't run it without your yes.\n"
            f"Commands: `{shown}`\n"
            f"Say \"go ahead\" to run it (T2 gate), or \"cancel\".")


def confirm(index=0):
    """Run one queued write after the owner's yes. Returns the joined git output."""
    if not _QUEUE or index >= len(_QUEUE):
        return "Nothing queued."
    item = _QUEUE.pop(index)
    outs = [_git(item["repo"], *args) for args in item["steps"]]
    return "\n".join(o for o in outs if o and o != "(no output)") or "(no output)"


def commit_message_from(text):
    m = re.search(r"\b(?:with\s+(?:the\s+)?message|saying|titled)\s+(?P<msg>[^;]+)$", text, I)
    return m.group("msg").strip().strip("\"'") if m else None


def match(text):
    t = text.strip()
    if REFUSE.search(t):
        return True           # we own the refusal: routing here shows the refusal, never the LLM
    if any(rx.match(t) for _k, rx in READS):
        return True
    return any(rx.match(t) for _k, rx in WRITES)


def run(text, session):
    t = text.strip()
    if REFUSE.search(t):
        return {"reply": "That git operation (force push / hard reset / history rewrite / forced "
                         "clean) is one I refuse by design - it can destroy work irrecoverably. "
                         "Ask me for a backup branch instead.", "sources": []}
    for kind, rx in READS:
        m = rx.match(t)
        if m:
            repo = _repo_of(m, t)
            trace(session, "gitops", kind)
            out = _git(repo, kind if kind != "current" else "branch",
                       *("--oneline", "-n", "10") if kind == "log" else ())
            if out == "":  # git prints nothing for a clean status/diff - make that a real answer
                out = {"diff": "No changes.", "status": "Working tree clean."}.get(kind, "(no output)")
            return {"reply": out, "sources": []}
    for kind, rx in WRITES:
        m = rx.match(t)
        if m:
            # the message is words, not a path: strip it before hunting for the repo
            t_clean = re.sub(r"\s+(?:with\s+(?:the\s+)?message|saying|titled)\s+.*$", "", t, flags=I)
            repo = _repo_of(m, t_clean)
            trace(session, "gitops", f"{kind} (T2 queued)")
            return {"reply": _queue_write(kind, repo, t), "sources": []}
    return None


if __name__ == "__main__":
    import tempfile
    tmp = tempfile.mkdtemp()
    repo = os.path.join(tmp, "proj")
    os.makedirs(repo)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    open(os.path.join(repo, "a.txt"), "w").write("hello\n")
    subprocess.run(["git", "add", "a.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "first"], cwd=repo, check=True)

    _QUEUE.clear()
    assert match("git status of " + repo)
    assert match("status of the ishti project")
    assert match("git log of " + repo)
    assert match("show branches of " + repo)
    assert match("which branch am I on in " + repo)
    assert match("commit my changes in " + repo + " with message fix readme")
    assert match("push " + repo)
    assert match("stash my changes")
    # refused classes route to US (match -> refusal reply), never to the LLM
    for bad in ("force push to origin", "git reset --hard of " + repo, "rebase the repo onto main",
                "git clean -fd in " + repo, "git clean -dfx in " + repo, "delete the remote origin"):
        assert match(bad), bad
        r = run(bad, {})
        assert r is not None and "refuse" in r["reply"].lower(), (bad, r)
    assert not match("what is git")                       # a question, not an operation
    assert not match("git blame of " + repo)              # not in the subset (yet)
    # 'log' belongs to other skills unless it is git-flavoured
    assert not match("log into my university portal and download the timetable")
    assert not match("log in to github with my saved login")
    assert not match("log sleep 7.5 hours")
    r = run("git status of " + repo, {})
    assert "nothing to commit" in r["reply"].lower() or "clean" in r["reply"].lower() or "On " in r["reply"], r
    r2 = run("git log of " + repo, {})
    assert "first" in r2["reply"], r2
    r3 = run("which branch am I on in " + repo, {})
    assert "*" in r3["reply"] or "master" in r3["reply"] or "main" in r3["reply"], r3
    # writes queue for T2, nothing runs until confirmed
    open(os.path.join(repo, "edit.txt"), "w").write("pending\n")   # a real pending change
    r4 = run("commit my changes in " + repo + " with message fix readme", {})
    assert "T2" in r4["reply"] and "git add -A" in r4["reply"] and "fix readme" in r4["reply"], r4
    assert _git(repo, "status", "--porcelain") != "", "commit must not run before the owner's yes"
    out = confirm()
    assert "fix readme" in _git(repo, "log", "--oneline", "-n", "1"), (out, _git(repo, "log", "--oneline"))
    assert _git(repo, "status", "--porcelain") == "", "confirmed commit must clean the tree"
    # a second commit with no message given: ISHTI's default, still only on a yes
    open(os.path.join(repo, "b.txt"), "w").write("two\n")
    run("commit my changes in " + repo, {})
    out2 = confirm()
    assert "b.txt" in out2, out2
    assert _git(repo, "status", "--porcelain") == ""
    # not-a-repo is a friendly miss
    assert "not a git repository" in run("git status of " + tmp, {})["reply"].lower()
    print("[self-check] gitops OK - reads direct, writes T2-queued with the exact command, "
          "force/reset/rebase refused in code, nothing touches the owner's checkout")
