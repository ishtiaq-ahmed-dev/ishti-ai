"""Run Python in a throwaway container - Hermes Agent's Docker backend, for ISHTI: "run this in the sandbox:
```print(sum(range(10)))```", or the model's own scratch work (tables, statistics, parsing) without touching the PC.

The container has no network, a read-only root, all Linux capabilities dropped, no privilege escalation, 256 MB of
memory, one CPU, 64 processes, an unprivileged user, and a fresh temp folder as its only writable place; it is killed
after TIMEOUT_S. Your files are not visible inside it. Because nothing can leave it, it runs without asking.

The image is pinned by multi-arch digest (python:3.12-slim@sha256:...) so it cannot change underneath users.
To bump: run `docker buildx imagetools inspect python:3.12-slim` or query Docker Hub for the latest multi-arch index digest."""
import os
import re
import shutil
import subprocess
import tempfile
import uuid

# pinned by multi-arch digest; a floating tag could change underneath users.
# To bump: run `docker buildx imagetools inspect python:3.12-slim` or query Docker Hub for the manifest list digest.
DEFAULT_IMAGE = "python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b"
IMAGE = os.environ.get("ISHTI_SANDBOX_IMAGE") or DEFAULT_IMAGE
TIMEOUT_S = 60
OUTPUT_MAX = 4000
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
I = re.I | re.S
RUN = re.compile(r"^\s*(?:please\s+)?(?:run|execute|test|try)\s+(?:this|the\s+following|my|some)?\s*(?:python|py|code|script)?"
                 r"(?:\s+code)?\s*(?:safely\s*|in\s+(?:a|the)\s+sandbox\s*)?[:\-]?\s*(?P<code>```.+```)\s*$"
                 r"|^\s*(?:please\s+)?(?:run|execute)\s+(?:this|it|the\s+following)?\s*(?:python\s+|code\s+)?in\s+(?:a|the)\s+sandbox"
                 r"\s*[:\-]?\s*(?P<code2>.+)$", I)


def code_of(text: str) -> str:
    m = RUN.match(text.strip())
    if not m:
        return ""
    code = (m["code"] or m["code2"] or "").strip()
    fenced = re.match(r"```(?:python|py)?\s*\n?(.*?)```", code, I)
    return (fenced[1] if fenced else code).strip()


def _docker(*args, timeout=TIMEOUT_S + 15):
    docker = shutil.which("docker")
    if not docker:
        raise RuntimeError("Docker Desktop is needed for the sandbox - install it from docker.com and start it")
    return subprocess.run([docker, *args], capture_output=True, text=True, timeout=timeout, creationflags=NO_WINDOW,
                          encoding="utf-8", errors="replace")


def command(work: str, name: str) -> list:
    return ["run", "--rm", "--name", name, "--network", "none", "--read-only", "--tmpfs", "/tmp:rw,size=64m",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--pids-limit", "64", "--memory", "256m",
            "--cpus", "1", "--user", "65534:65534", "-v", f"{work}:/work", "-w", "/work", IMAGE, "python", "-I", "main.py"]


def run_python(code: str) -> tuple[int, str]:
    """(exit code, combined output). Raises RuntimeError when Docker isn't available."""
    if _docker("image", "inspect", IMAGE, timeout=30).returncode != 0:
        pulled = _docker("pull", IMAGE, timeout=600)  # first use only (~50 MB)
        if pulled.returncode != 0:
            raise RuntimeError(f"could not download the sandbox image ({(pulled.stderr or '').strip()[:160]})")
    work, name = tempfile.mkdtemp(prefix="ishti-sbx-"), f"ishti-sbx-{uuid.uuid4().hex[:8]}"
    try:
        with open(os.path.join(work, "main.py"), "w", encoding="utf-8") as f:
            f.write(code)
        try:
            p = _docker(*command(work, name))
        except subprocess.TimeoutExpired:
            _docker("rm", "-f", name, timeout=30)  # --rm does not fire when the client is killed
            return 124, f"Stopped after {TIMEOUT_S} s."
        out = (p.stdout or "") + (("\n" + p.stderr) if p.stderr else "")
        if p.returncode == 125 or "Cannot connect to the Docker daemon" in out or "error during connect" in out:
            raise RuntimeError("Docker Desktop isn't running - start it and try again")
        return p.returncode, out.strip()
    finally:
        shutil.rmtree(work, ignore_errors=True)


def match(text):
    return bool(code_of(text))


def run(text, session):
    code = code_of(text) or (text if "\n" in text or "print(" in text else "")
    if not code:
        return None
    try:
        rc, out = run_python(code)
    except RuntimeError as exc:
        return {"reply": f"The sandbox couldn't start: {exc}.", "sources": [], "verified": False}
    out = out[:OUTPUT_MAX] + ("\n... (cut)" if len(out) > OUTPUT_MAX else "")
    head = "Ran in the sandbox (no network, no access to your files)" + ("" if rc == 0 else f" - it exited with code {rc}")
    return {"reply": f"{head}:\n```\n{out or '(no output)'}\n```", "sources": [], "verified": rc == 0,
            **({} if rc == 0 else {"status": "failed"})}


if __name__ == "__main__":
    assert code_of("run this in the sandbox: ```print(1+1)```") == "print(1+1)"
    assert code_of("run this python:\n```python\nimport math\nprint(math.pi)\n```") == "import math\nprint(math.pi)"
    assert code_of("run in the sandbox print(2)") == "print(2)" and not code_of("run git status")
    assert "--network" in command("w", "n") and "none" in command("w", "n")
    print("sandbox self-check ok")
