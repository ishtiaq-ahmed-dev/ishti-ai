"""Skill permission scoping (A5): what each skill may reach, declared in its manifest and enforced here.

manifest "scope": {
  "write": ["data", "~/Downloads", "user"],   where it may create/change/delete files ("user" = the owner's own
                                              areas: the home folder and non-system drives)
  "net":   ["open-meteo.com"] | ["*"] | [],   which internet domains its Python code may call (localhost - Ollama,
                                              SearXNG - is always allowed)
  "apps":  true | false                       may it launch/close/kill apps or drive the mouse and keyboard
}
Tier (T0-T3) decides whether an action needs the owner's yes; scope decides what the skill can touch at all.
Enforcement: while a skill runs (ishti.audit.context says which), Python file writes/deletes/moves and HTTP calls are
checked. System folders and ISHTI's own code are never writable by any skill (skillsmith may add skills). Code
outside a skill (the dispatcher, the server, background loops) is not restricted. Subprocesses (FFmpeg, robocopy)
are checked where the skill passes their output path (scope.check_write) - they can't be hooked.
A violation raises ScopeError; the dispatcher reports it like any skill failure and the audit log records it."""
import builtins
import os
import re
import shutil
import tempfile
import threading
import urllib.parse

from ishti import CONFIG, ROOT
from ishti.audit import current, record

HOME = os.path.expanduser("~")
DATA = os.path.dirname(CONFIG["memory"]["db_path"])
ALWAYS_WRITABLE = [DATA, tempfile.gettempdir(), os.path.join(ROOT, "orchestrator", "memory"),  # owner memory JSON
                   os.path.join(HOME, ".cache"), os.path.join(HOME, ".u2net"), os.path.join(HOME, ".matplotlib")]
PROTECTED = [os.environ.get("SystemRoot", r"C:\Windows"), r"C:\Program Files", r"C:\Program Files (x86)", r"C:\ProgramData",
             os.path.join(HOME, ".ssh"), os.path.join(HOME, "AppData", "Roaming", "Microsoft"),
             os.path.join(ROOT, "ishti"), os.path.join(ROOT, "interface"), os.path.join(ROOT, "orchestrator"),
             os.path.join(ROOT, "venv"), os.path.join(ROOT, ".git")]
SYSTEM_PROCESSES = {"csrss.exe", "lsass.exe", "winlogon.exe", "wininit.exe", "services.exe", "smss.exe", "svchost.exe",
                    "system", "registry", "dwm.exe", "explorer.exe", "ollama.exe", "ollama app.exe"}


class ScopeError(PermissionError):
    pass


def _norm(p):
    return os.path.normcase(os.path.abspath(os.path.expanduser(str(p))))


def _under(path, roots):
    p = _norm(path)
    return any(p == _norm(r) or p.startswith(_norm(r).rstrip("\\/") + os.sep) for r in roots)


_scopes = {}


def scope_of(skill):
    """The skill's declared scope (manifests are read once)."""
    if skill not in _scopes:
        import json
        path = os.path.join(ROOT, "ishti", "skills", skill, "manifest.json")
        if not os.path.isfile(path):
            skills_dir = os.path.join(ROOT, "ishti", "skills")
            for r, _, fs in os.walk(skills_dir):
                if "manifest.json" in fs and os.path.basename(r) == skill:
                    path = os.path.join(r, "manifest.json")
                    break
        try:
            with open_unchecked(path, encoding="utf-8") as f:
                _scopes[skill] = json.load(f).get("scope") or {}
        except OSError:
            _scopes[skill] = {}
    return _scopes[skill]


def _roots(scope):
    out = []
    for w in scope.get("write", []):
        if w == "data":
            out.append(DATA)
        elif w == "user":  # the owner's own areas: home + every drive except the system drive's system folders
            out += [HOME] + [f"{d}:\\" for d in "DEFGHIJKLMNOPQRSTUVWXYZ" if os.path.isdir(f"{d}:\\")]
        else:
            out.append(w if os.path.isabs(os.path.expanduser(w)) else os.path.join(ROOT, w))
    return out


def check_write(path, what="write"):
    """Raise ScopeError if the running skill may not change `path`."""
    skill = current().get("skill")
    if not skill or not path or str(path).startswith("\\\\.\\") or str(path) in ("nul", "NUL", os.devnull):
        return
    scope = scope_of(skill)
    if skill == "skillsmith" and _under(path, [os.path.join(ROOT, "ishti", "skills")]):
        return  # installing a skill is what it's for (after the owner's yes)
    if skill in ("architecture", "settings") and _norm(path) in (_norm(os.path.join(ROOT, "ishti", "config.json")),
                                                                 _norm(os.path.join(DATA, "settings.json")),
                                                                 _norm(os.path.join(DATA, "settings.secrets.json"))):
        return  # first-party config: the architecture registry and settings service ARE their managers
    if _under(path, PROTECTED) and not _under(path, ALWAYS_WRITABLE):
        _deny(skill, f"{what} {path}", "a protected system/ISHTI folder")
    if _under(path, ALWAYS_WRITABLE) or _under(path, _roots(scope)):
        return
    _deny(skill, f"{what} {path}", f"outside its write scope {scope.get('write', [])}")


def check_url(url):
    skill = current().get("skill")
    if not skill:
        return
    host = (urllib.parse.urlsplit(str(url)).hostname or "").lower()
    if host in ("localhost", "127.0.0.1", "::1") or not host:
        return
    allowed = scope_of(skill).get("net", [])
    if "*" in allowed or any(host == d or host.endswith("." + d) for d in allowed):
        return
    _deny(skill, f"connect to {host}", f"outside its network scope {allowed}")


def require_apps(action):
    skill = current().get("skill")
    if skill and not scope_of(skill).get("apps"):
        _deny(skill, action, "it isn't allowed to control apps")


def check_process(name):
    require_apps(f"stop {name}")
    if name.lower() in SYSTEM_PROCESSES:
        _deny(current().get("skill", "?"), f"stop {name}", "a Windows system process")


def _deny(skill, action, why):
    record(f"BLOCKED: {action}", why, skill=skill)
    raise ScopeError(f"the {skill} skill isn't allowed to {action} ({why})")


# ---------------------------------------------------------------- hooks (installed once, only bite inside a skill)
_orig = {}
open_unchecked = builtins.open
WRITE_MODE = re.compile(r"[wax+]")


def _open(file, mode="r", *a, **k):
    if current().get("skill") and isinstance(file, (str, bytes, os.PathLike)) and WRITE_MODE.search(str(mode)):
        check_write(os.fsdecode(file))
    return _orig["open"](file, mode, *a, **k)


def _wrap_path_fn(name, fn, argpos=(0,), what="change"):
    def wrapped(*a, **k):
        if current().get("skill"):
            for i in argpos:
                if len(a) > i:
                    check_write(os.fsdecode(a[i]) if isinstance(a[i], (str, bytes, os.PathLike)) else a[i], what)
        return fn(*a, **k)
    wrapped.__name__ = name
    return wrapped


def _kill_check():
    if current().get("skill"):  # only inside a request: background loops aren't the kill switch's business
        from ishti import killswitch
        killswitch.check()


def install():
    if _orig:
        return
    _orig["open"] = builtins.open
    builtins.open = _open
    for mod, name, pos, what in ((os, "remove", (0,), "delete"), (os, "unlink", (0,), "delete"), (os, "rmdir", (0,), "delete"),
                                 (os, "rename", (0, 1), "move"), (os, "replace", (0, 1), "move"), (os, "mkdir", (0,), "create"),
                                 (shutil, "rmtree", (0,), "delete"), (shutil, "move", (0, 1), "move"),
                                 (shutil, "copyfile", (1,), "write"), (shutil, "copy2", (1,), "write")):
        _orig[f"{mod.__name__}.{name}"] = getattr(mod, name)
        setattr(mod, name, _wrap_path_fn(name, getattr(mod, name), pos, what))
    try:
        import requests.sessions
        orig_request = requests.sessions.Session.request

        def request(self, method, url, *a, **k):
            check_url(url)
            _kill_check()  # every web request is also a kill-switch checkpoint (searches, page fetches)
            return orig_request(self, method, url, *a, **k)
        requests.sessions.Session.request = request
    except ImportError:
        pass
    import urllib.request
    orig_open = urllib.request.OpenerDirector.open

    def opener_open(self, fullurl, *a, **k):
        check_url(fullurl.full_url if hasattr(fullurl, "full_url") else fullurl)
        _kill_check()
        return orig_open(self, fullurl, *a, **k)
    urllib.request.OpenerDirector.open = opener_open


if __name__ == "__main__":
    import json
    from ishti.audit import context
    install()
    missing = [d for d in os.listdir(os.path.join(ROOT, "ishti", "skills"))
               if os.path.isfile(os.path.join(ROOT, "ishti", "skills", d, "manifest.json"))
               and "scope" not in json.load(open(os.path.join(ROOT, "ishti", "skills", d, "manifest.json"), encoding="utf-8"))]
    assert not missing, f"manifests without a scope: {missing}"
    tmp = os.path.join(tempfile.gettempdir(), "ishti_scope_test.txt")
    with context(skill="weather"):
        open(tmp, "w").write("ok")  # temp is always writable
        for bad in (os.path.join(HOME, "Desktop", "x.txt"), r"C:\Windows\x.txt", os.path.join(ROOT, "ishti", "evil.py")):
            try:
                open(bad, "w")
                raise AssertionError(f"weather wrote {bad}")
            except ScopeError:
                pass
        try:
            import requests
            requests.get("https://example.com", timeout=5)
            raise AssertionError("weather reached example.com")
        except ScopeError:
            pass
        check_url("https://api.open-meteo.com/v1/forecast")  # its own API is fine
        check_url("http://127.0.0.1:11434/api/tags")  # the local model always is
        try:
            require_apps("close notepad")
            raise AssertionError("weather may control apps")
        except ScopeError:
            pass
    with context(skill="fileops"):
        check_write(os.path.join(HOME, "Documents", "report.txt"))  # the owner's own files
        try:
            check_write(r"C:\Windows\System32\drivers\etc\hosts")
            raise AssertionError("fileops may edit the hosts file")
        except ScopeError:
            pass
    with context(skill="desktop"):
        try:
            check_process("lsass.exe")
            raise AssertionError("desktop may kill lsass")
        except ScopeError:
            pass
    open(os.path.join(HOME, "Desktop", "ishti_scope_ok.txt"), "w").close()  # outside a skill: not restricted
    os.remove(os.path.join(HOME, "Desktop", "ishti_scope_ok.txt"))
    os.remove(tmp)
    print("[self-check] scope OK - every manifest scoped; file writes, network and app control limited per skill; "
          "system/ISHTI folders and system processes protected")
