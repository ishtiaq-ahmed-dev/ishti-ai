"""Project boilerplate and Windows services.

scaffold: "create a new fastapi project called shop-api [in D:\\Projects]" - Python kinds (python, cli, fastapi, flask)
from small built-in templates, JS kinds through their official generators (Vite for react/vue/svelte/vanilla, create-next-app
for next); then git init with a first commit. Existing folders are never overwritten.
services: "status of the <name> service", "restart/start/stop the <name> service" (yes first). Most services need
admin: then Windows' own UAC prompt asks the owner (ISHTI never elevates silently)."""
import os
import re
import subprocess
import time

I = re.I
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
SCAFFOLD = re.compile(r"\b(?:create|make|start|scaffold|set\s*up|init(?:iali[sz]e)?|generate)\s+(?:a\s+|an\s+)?(?:new\s+)?"
                      r"(?P<kind>python|fastapi|flask|cli|react|vue|svelte|next(?:\.?js)?|node|vanilla|javascript|typescript)\s+"
                      r"(?:js\s+)?(?:project|app|api|boilerplate|starter|template)\b(?:\s+(?:called|named)\s+[\"']?(?P<name>[\w.-]+)[\"']?)?"
                      r"(?:\s+(?:in|at|under)\s+[\"']?(?P<where>[a-z]:[\\/][^\"'\n]*?)[\"']?)?\s*$", I)
RUN_SCRIPT = re.compile(r"\b(?:run|execute|start|launch)\s+(?:the\s+|my\s+)?(?:deploy(?:ment)?\s+|build\s+|release\s+)?(?:script\s+)?"
                        r"[\"']?(?P<path>[a-z]:[\\/][^\"'\n]+?\.(?:ps1|bat|cmd|sh))[\"']?\s*$", I)
SERVICE = re.compile(r"\b(?P<act>status\s+of|restart|start|stop|is)\s+(?:the\s+)?(?P<name>[\w .-]+?)\s+service\b(?:\s+running)?", I)
DEFAULT_ROOT = "D:\\Projects"

PY = {"README.md": "# {name}\n\n{desc}\n\n```\npython -m venv .venv\n.venv\\Scripts\\activate\npip install -r requirements.txt\n{run}\n```\n",
      ".gitignore": ".venv/\n__pycache__/\n*.pyc\n.env\ndist/\nbuild/\n.pytest_cache/\n",
      "tests/test_smoke.py": "from {pkg} import main\n\n\ndef test_main():\n    assert main() is not None\n"}
KINDS = {
    "python": {"requirements.txt": "pytest\n", "{pkg}/__init__.py": "def main():\n    return \"hello from {name}\"\n\n\nif __name__ == \"__main__\":\n    print(main())\n",
               "_run": "python -m {pkg}", "_desc": "A Python project."},
    "cli": {"requirements.txt": "pytest\n", "{pkg}/__init__.py": "import argparse\n\n\ndef main(argv=None):\n    p = argparse.ArgumentParser(prog=\"{name}\")\n    p.add_argument(\"who\", nargs=\"?\", default=\"world\")\n    args = p.parse_args(argv or [])\n    return f\"hello {{args.who}}\"\n\n\nif __name__ == \"__main__\":\n    import sys\n    print(main(sys.argv[1:]))\n",
            "_run": "python -m {pkg} you", "_desc": "A command-line tool."},
    "fastapi": {"requirements.txt": "fastapi\nuvicorn[standard]\npytest\nhttpx\n",
                "{pkg}/__init__.py": "from fastapi import FastAPI\n\napp = FastAPI(title=\"{name}\")\n\n\n@app.get(\"/health\")\ndef health():\n    return {{\"ok\": True}}\n\n\ndef main():\n    return app\n",
                "_run": "uvicorn {pkg}:app --reload", "_desc": "A FastAPI service."},
    "flask": {"requirements.txt": "flask\npytest\n",
              "{pkg}/__init__.py": "from flask import Flask\n\napp = Flask(__name__)\n\n\n@app.get(\"/\")\ndef index():\n    return \"hello from {name}\"\n\n\ndef main():\n    return app\n",
              "_run": "flask --app {pkg} run --debug", "_desc": "A Flask web app."},
}
VITE = {"react": "react-ts", "vue": "vue-ts", "svelte": "svelte-ts", "vanilla": "vanilla-ts", "typescript": "vanilla-ts",
        "javascript": "vanilla", "node": "vanilla-ts"}


def scaffold(kind, name, root=DEFAULT_ROOT):
    """Create the project; returns (path, how to run it)."""
    kind = kind.lower().replace(".js", "").replace("nextjs", "next")
    path = os.path.join(root, name)
    if os.path.exists(path):
        raise FileExistsError(path)
    os.makedirs(root, exist_ok=True)
    if kind in KINDS:
        pkg = re.sub(r"\W+", "_", name).strip("_").lower() or "app"
        spec = {**PY, **KINDS[kind]}
        run_cmd = spec["_run"].format(pkg=pkg)
        for rel, body in spec.items():
            if rel.startswith("_"):
                continue
            target = os.path.join(path, rel.format(pkg=pkg))
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, "w", encoding="utf-8") as f:
                f.write(body.format(name=name, pkg=pkg, desc=spec["_desc"], run=run_cmd))
    elif kind == "next":
        _run(["npx", "--yes", "create-next-app@latest", name, "--ts", "--eslint", "--app", "--use-npm", "--yes"], root)
        run_cmd = "npm run dev"
    else:
        _run(["npm", "create", "--yes", "vite@latest", name, "--", "--template", VITE.get(kind, "vanilla-ts")], root)
        run_cmd = "npm install && npm run dev"
    if not os.path.isdir(os.path.join(path, ".git")):
        _run(["git", "init", "-q"], path)
        _run(["git", "add", "-A"], path)
        _run(["git", "-c", "user.name=ISHTI", "-c", "user.email=ishti@localhost", "commit", "-q", "-m", "Project scaffold"], path)
    return path, run_cmd


def _run(args, cwd):
    p = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=600, shell=args[0] in ("npm", "npx"),
                       creationflags=NO_WINDOW)
    if p.returncode:
        raise RuntimeError((p.stderr or p.stdout).strip()[-300:])
    return p.stdout


def op_run_script(m, text, session, ask_first):
    """Deploy/build scripts: run after a yes (they change things), output tail shown."""
    import shlex
    path = m["path"].strip()
    if not os.path.isfile(path):
        return f"I can't find {path}."
    ext = os.path.splitext(path)[1].lower()
    cmd = {"ps1": ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", path], "sh": ["bash", path]}.get(
        ext[1:], ["cmd", "/c", path])

    def go():
        t0 = time.time()
        p = subprocess.run(cmd, cwd=os.path.dirname(path), capture_output=True, text=True, timeout=1800,
                           encoding="utf-8", errors="replace", creationflags=NO_WINDOW)
        out = ((p.stdout or "") + ("\n" + p.stderr if p.stderr else "")).strip()
        tail = "\n".join(out.splitlines()[-25:])
        return (f"{os.path.basename(path)} {'finished' if p.returncode == 0 else f'FAILED (exit {p.returncode})'} in "
                f"{time.time() - t0:.0f} s.\n```\n{tail or '(no output)'}\n```")
    return ask_first(session, "dev", f"Run {os.path.basename(path)} (in {os.path.dirname(path)}) - scripts can change things", go)


def op_scaffold(m, text, session):
    kind = m["kind"]
    name = m["name"] or f"new-{kind.lower().replace('.', '')}-app"
    try:
        path, run_cmd = scaffold(kind, name, m["where"] or DEFAULT_ROOT)
    except FileExistsError as exc:
        return f"{exc} already exists - pick another name."
    except Exception as exc:
        return f"Couldn't create it: {exc}"
    return f"Created a {kind} project at {path} (git initialised, first commit made). Run it: `cd \"{path}\"` then `{run_cmd}`."


# ---------------------------------------------------------------- Windows services
def _ps(cmd):
    p = subprocess.run(["powershell", "-NoProfile", "-Command", cmd], capture_output=True, text=True, timeout=60,
                       creationflags=NO_WINDOW)
    return p.returncode, (p.stdout or "").strip(), (p.stderr or "").strip()


def find_service(name):
    """(service name, display name, status) best matching what the owner said, or None."""
    safe = name.replace("'", "")
    code, out, _ = _ps(f"Get-Service | Where-Object {{ $_.Name -like '*{safe}*' -or $_.DisplayName -like '*{safe}*' }} | "
                       "Select-Object -First 1 | ForEach-Object { \"$($_.Name)|$($_.DisplayName)|$($_.Status)\" }")
    return tuple(out.split("|", 2)) if out.count("|") == 2 else None


def op_service(m, text, session, ask_first):
    act = m["act"].lower()
    found = find_service(m["name"].strip())
    if not found:
        return f"I can't find a Windows service called \"{m['name'].strip()}\"."
    name, display, status = found
    if act in ("status of", "is"):
        return f"{display} ({name}) is {status.lower()}."
    verb = {"restart": "Restart-Service", "start": "Start-Service", "stop": "Stop-Service"}[act]

    def go():
        code, _, err = _ps(f"{verb} -Name '{name}' -Force -ErrorAction Stop")
        if code and re.search(r"access|denied|permission|cannot open", err, I):
            # needs admin: Windows asks the owner through its own UAC prompt
            _ps(f"Start-Process powershell -Verb RunAs -WindowStyle Hidden -ArgumentList '-NoProfile','-Command','{verb} -Name {name} -Force'")
            return f"{display} needs administrator rights - approve the Windows prompt and it will {act}."
        if code:
            return f"Couldn't {act} {display}: {err[-200:]}"
        return f"{display}: {act} done - now {find_service(name)[2].lower()}."
    return ask_first(session, "dev", f"{act.title()} the {display} service (currently {status.lower()})", go)


if __name__ == "__main__":
    import tempfile
    for t, want in {"create a new fastapi project called shop-api": ("fastapi", "shop-api", None),
                    "make a react app named dashboard in D:/Code": ("react", "dashboard", "D:/Code"),
                    "scaffold a python project": ("python", None, None)}.items():
        m = SCAFFOLD.search(t)
        assert m and (m["kind"], m["name"], m["where"]) == want, (t, m and m.groupdict())
    root = tempfile.mkdtemp()
    path, how = scaffold("fastapi", "shop-api", root)
    assert os.path.isfile(os.path.join(path, "shop_api", "__init__.py")) and "uvicorn shop_api:app" in how
    assert subprocess.run(["git", "log", "--oneline"], cwd=path, capture_output=True, text=True, timeout=60).stdout.strip().endswith("Project scaffold")
    p = subprocess.run(["python", "-c", "import sys; sys.path.insert(0, '.'); import shop_api; print(shop_api.main().title)"],
                       cwd=path, capture_output=True, text=True, timeout=60)
    assert p.stdout.strip() == "shop-api" or "No module named 'fastapi'" in p.stderr, p.stderr
    try:
        scaffold("python", "shop-api", root)
        raise AssertionError("overwrote an existing folder")
    except FileExistsError:
        pass
    m = RUN_SCRIPT.search(r"run the deploy script D:\\proj\\deploy.ps1")
    assert m and m["path"].endswith("deploy.ps1")
    script = os.path.join(root, "build.bat")
    open(script, "w").write("@echo building...\n@echo done")
    s = {}
    q = op_run_script(RUN_SCRIPT.search(f"run {script}"), "", s, lambda sess, sk, question, action: (sess.update(action=action), question)[1])
    assert "Run build.bat" in q and "finished" in s["action"]() and "done" in s["action"]()
    m = SERVICE.search("what's the status of the print spooler service")
    assert m and m["name"].strip() == "print spooler"
    assert find_service("spooler")[0] == "Spooler" and find_service("no such thing 123") is None
    print("[self-check] scaffold OK - routing, fastapi project + git commit, no overwrite, service lookup")
