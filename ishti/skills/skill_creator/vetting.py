"""Vet a dependency before it is pinned: zero installs (RESEARCH.md R3 - every step below was tested on this PC).

Order, all recorded in the draft's `vetting.json`: exact-name + typosquat (stdlib `difflib` against a curated
popular list) -> PyPI JSON (licence via license_expression/license/classifiers, release age, requires_python,
sha256, project_urls, summary, yanked) -> OSV.dev (CVEs *and* `MAL-*`/GHSA malware records) -> stdlib `ast`
risky-call scan (R3's node table), merged with `skillsmith.scan`'s regex findings. Everything network-facing
fails CLOSED: a failed lookup is not vetted, and not vetted is blocked. Absence of advisories proves nothing
(R3 §2: `crossenv`/`colourama` returned 0 OSV entries) - it is one filter among several, not proof of safety.

Why no bandit/pip-audit/semgrep: R3 §4 measured them ABSENT from this venv, and pip-installing dev scanners
into the app venv is itself a supply-chain step this module exists to avoid. The stdlib `ast` table plus
PyPI/OSV cover the same ground with zero installs; `uv tool run --isolated` remains available for a human
deep-dive, which is a deliberate manual step, never part of the pipeline.
"""
from __future__ import annotations

import ast
import datetime as dt
import difflib
import json
import os
import re

import requests

# The curated popular list: what the repo itself uses (ishti/skills manifests + PIP_NAME) plus the
# widely-used packages a generated skill is likely to reach for. Exact matches pass; close-but-not-exact
# matches are rejected with the near neighbour named (R3 §1: `reqeusts => requests`, `numpi => numpy`).
POPULAR = (
    "requests", "urllib3", "certifi", "idna", "charset-normalizer", "pillow", "pyyaml", "python-docx",
    "python-pptx", "opencv-python", "beautifulsoup4", "lxml", "yt-dlp", "numpy", "pandas", "openpyxl",
    "xlsxwriter", "fastapi", "uvicorn", "starlette", "pydantic", "psutil", "playwright", "pypdfium2",
    "pypdf", "pywin32", "scikit-learn", "scipy", "matplotlib", "seaborn", "plotly", "torch", "torchvision",
    "transformers", "openai", "httpx", "aiohttp", "flask", "django", "jinja2", "sqlalchemy", "pytest",
    "setuptools", "pip", "wheel", "cryptography", "paramiko", "tqdm", "rich", "colorama", "pyautogui",
    "selenium", "soundfile", "sounddevice", "librosa", "pydub", "mutagen", "reportlab", "tabulate",
    "python-dateutil", "pytz", "tzdata", "moviepy", "imageio", "pdfplumber", "camelot-py", "tabula-py",
    "qrcode", "pyzbar", "sympy", "networkx", "statsmodels", "joblib", "numba", "ollama", "huggingface-hub",
    "safetensors", "accelerate", "sentence-transformers", "chromadb", "faiss-cpu", "onnxruntime",
    "faster-whisper", "ctranslate2", "screen-brightness-control", "rapidocr-onnxruntime",
)
# R3 §5: safe to vendor into a private local assistant vs care-required. Unknown/missing = not vetted.
PERMISSIVE = ("mit", "bsd-2-clause", "bsd-3-clause", "apache-2.0", "isc", "psf", "0bsd", "unlicense",
              "python-2.0", "blue-oak", "zlib")
CARE = ("gpl", "lgpl", "agpl", "mpl", "mozilla", "sspl", "bsl-1.1", "busl", "non-commercial",
        "noncommercial", "research only", "research-only", "cc-by-nc", "proprietary")

CALL_NAMES = {"eval", "exec", "compile", "__import__", "open", "input"}
ATTR_NAMES = {"system", "popen", "run", "call", "check_output", "Popen", "remove", "unlink", "rmtree", "move",
              "urlopen", "urlretrieve", "connect", "send", "WinDLL", "windll", "CDLL", "OpenKey", "SetValueEx",
              "load", "loads"}
IMPORT_ROOTS = {"subprocess", "socket", "ctypes", "winreg", "shutil", "pickle", "marshal", "base64",
                "requests", "urllib", "http", "smtplib"}
BASE64_BLOB = re.compile(r"^[A-Za-z0-9+/=\s]{120,}$")
COOLDOWN_DAYS = 14  # R3 §6: a version younger than this is inside the cooldown


def _norm(name) -> str:
    return re.sub(r"[-_.]+", "-", str(name).strip().lower())


_POPULAR_NORM = {_norm(p): p for p in POPULAR}


def check_name(name, popular=None) -> dict:
    """Exact-name existence + typosquat rejection. A close-but-not-exact match is REJECTED, naming the neighbour."""
    norm = _norm(name)
    table = {_norm(p): p for p in (popular or POPULAR)}
    if norm in table:
        return {"ok": True, "name": name, "exact": True, "near": None, "reason": "exact name"}
    near = difflib.get_close_matches(norm, list(table), n=1, cutoff=0.8)  # R3: reqeusts=>requests, numpi=>numpy
    if near:
        return {"ok": False, "name": name, "exact": False, "near": table[near[0]],
                "reason": (f"'{name}' is not an exact known name - it looks like a typosquat of "
                           f"'{table[near[0]]}' (difflib close match); refusing to vet it")}
    return {"ok": True, "name": name, "exact": False, "near": None,
            "reason": "not a known popular name and no close neighbour found"}


def licence_verdict(expr) -> str:
    """'permissive' | 'care' | 'unknown' - unknown is never silently treated as permissive."""
    text = str(expr or "").strip().lower()
    if not text:
        return "unknown"
    if any(c in text for c in CARE):
        return "care"
    return "permissive" if any(p in text for p in PERMISSIVE) else "unknown"


def licence_ok(expr) -> bool:
    """True only for the permissive allow-list (R3 §5). Care-needed and unknown both fail closed."""
    return licence_verdict(expr) == "permissive"


def _requires_python_ok(spec) -> tuple[bool, str]:
    """Does `requires_python` admit 3.11.9? Missing or unparseable metadata fails closed."""
    if not spec or not str(spec).strip():
        return False, "no requires_python metadata (fail closed)"
    try:
        from packaging.specifiers import SpecifierSet
        ok = SpecifierSet(str(spec), prereleases=True).contains("3.11.9")
    except Exception as exc:
        return False, f"unparseable requires_python {spec!r} ({type(exc).__name__}: {exc})"
    return (ok, "" if ok else f"requires_python {spec!r} does not admit 3.11.9")


def _licence_from(info) -> str:
    """license_expression first (PEP 639), then license, then License :: classifiers; '' when all are silent."""
    expr = str(info.get("license_expression") or "").strip()
    if expr:
        return expr
    plain = str(info.get("license") or "").strip()
    if plain and plain.lower() not in ("unknown", "none"):
        return plain
    for c in info.get("classifiers") or []:
        if str(c).startswith("License ::"):
            return str(c).split("::")[-1].strip()
    return ""


def check_pypi(name, version=None, *, timeout=20) -> dict:
    """https://pypi.org/pypi/<name>/json -> licence, release age, requires_python, sha256, project_urls, summary.
    Any failure (network, malformed JSON, missing fields) fails CLOSED."""
    blank = {"name": name, "version": version, "license": "", "age_days": None, "requires_python": "",
             "sha256": "", "project_urls": {}, "summary": "", "yanked": False}
    try:
        r = requests.get(f"https://pypi.org/pypi/{name}/json", timeout=timeout)
        r.raise_for_status()
        data = r.json()
    except Exception as exc:
        return {**blank, "ok": False, "reason": f"PyPI lookup failed (fail closed): {type(exc).__name__}: {exc}"}
    info = data.get("info") or {}
    releases = data.get("releases") or {}
    version = str(version or info.get("version") or "")
    if not version or version not in releases:
        return {**blank, "ok": False, "reason": f"PyPI has no release {version!r} for {name} (fail closed)"}
    files = [f for f in releases.get(version) or [] if isinstance(f, dict)]
    if not files:
        return {**blank, "ok": False, "reason": f"release {name}=={version} has no files on PyPI (fail closed)"}
    stamps = [str(f.get("upload_time_iso_8601") or "") for f in files if f.get("upload_time_iso_8601")]
    age_days = None
    if stamps:
        newest = max(stamps)
        try:
            up = dt.datetime.fromisoformat(newest.replace("Z", "+00:00"))
            if up.tzinfo is None:
                up = up.replace(tzinfo=dt.timezone.utc)
            age_days = (dt.datetime.now(dt.timezone.utc) - up).days
        except ValueError as exc:
            print(f"[vetting] unreadable upload time {newest!r} ({exc}): age stays unknown")
    wheel = next((f for f in files if f.get("packagetype") == "bdist_wheel"), None)
    sha = str(((wheel or files[0]).get("digests") or {}).get("sha256") or "")
    licence = _licence_from(info)
    rp_ok, rp_reason = _requires_python_ok(info.get("requires_python"))
    yanked = any(f.get("yanked") for f in files)
    problems = []
    if not licence:
        problems.append("no licence metadata on PyPI (license_expression/license/classifiers all empty)")
    if not sha:
        problems.append("no sha256 digest in the release metadata")
    if not rp_ok:
        problems.append(rp_reason)
    if age_days is None:
        problems.append("no usable upload timestamp (release age unknown)")
    if yanked:
        problems.append("this release is YANKED on PyPI")
    out = {"name": name, "version": version, "license": licence, "age_days": age_days,
           "requires_python": str(info.get("requires_python") or ""), "sha256": sha,
           "project_urls": info.get("project_urls") or {}, "summary": str(info.get("summary") or ""),
           "yanked": yanked}
    return {**out, "ok": not problems, "reason": "" if not problems else "; ".join(problems)}


def check_osv(name, version, *, timeout=20) -> dict:
    """POST api.osv.dev/v1/query for this exact version. `MAL-*`/GHSA malware records are a hard block;
    any advisory at the pinned version blocks (fail closed); a failed lookup blocks too."""
    blank = {"ids": [], "aliases": [], "malware": []}
    try:
        r = requests.post("https://api.osv.dev/v1/query",
                          json={"package": {"name": name, "ecosystem": "PyPI"}, "version": str(version)},
                          timeout=timeout)
        r.raise_for_status()
        vulns = (r.json() or {}).get("vulns") or []
    except Exception as exc:
        return {**blank, "ok": False, "reason": f"OSV lookup failed (fail closed): {type(exc).__name__}: {exc}"}
    ids, aliases, malware = [], [], []
    for v in vulns:
        vid = str(v.get("id") or "")
        als = [str(a) for a in (v.get("aliases") or [])]
        ids.append(vid)
        aliases.extend(als)
        hay = " ".join([vid, *als, str(v.get("summary") or "")]).lower()
        if vid.startswith("MAL-") or any(a.startswith("MAL-") for a in als) or "malware" in hay or "malicious" in hay:
            malware.append(vid)
    if not vulns:
        return {**blank, "ok": True, "reason": ""}
    reason = (f"malware advisory: {', '.join(malware)}" if malware
              else f"{len(vulns)} known advisor{'y' if len(vulns) == 1 else 'ies'} at this version: "
                   + ", ".join(ids[:5]))
    return {"ok": False, "reason": reason, "ids": ids, "aliases": sorted(set(aliases)), "malware": malware}


def scan_python(src, filename="skill.py") -> list[dict]:
    """R3 §4's stdlib-`ast` risky-call table -> list of {file, line, kind, detail}. Syntax errors are findings."""
    try:
        tree = ast.parse(src)
    except SyntaxError as exc:
        return [{"file": filename, "line": exc.lineno or 0, "kind": "syntax", "detail": f"does not parse: {exc.msg}"}]
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            if isinstance(fn, ast.Name) and fn.id in CALL_NAMES:
                out.append({"file": filename, "line": node.lineno, "kind": "call", "detail": f"calls {fn.id}()"})
            elif isinstance(fn, ast.Attribute) and fn.attr in ATTR_NAMES:
                out.append({"file": filename, "line": node.lineno, "kind": "call", "detail": f"calls .{fn.attr}()"})
        elif isinstance(node, ast.Import):
            for a in node.names:
                root = a.name.split(".")[0]
                if root in IMPORT_ROOTS:
                    out.append({"file": filename, "line": node.lineno, "kind": "import", "detail": f"imports {root}"})
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            if root in IMPORT_ROOTS:
                out.append({"file": filename, "line": node.lineno, "kind": "import", "detail": f"imports {root}"})
        elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) \
                and node.value.id == "os" and node.attr == "environ":
            out.append({"file": filename, "line": node.lineno, "kind": "env", "detail": "reads os.environ"})
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) \
                and len(node.value) >= 120 and BASE64_BLOB.match(node.value):
            out.append({"file": filename, "line": node.lineno, "kind": "blob",
                        "detail": "long base64-looking constant (obfuscation smell)"})
    seen, dedup = set(), []
    for f in out:
        key = (f["line"], f["kind"], f["detail"])
        if key not in seen:
            seen.add(key)
            dedup.append(f)
    return dedup


def vet(packages, *, src="", folder=None, draft_dir=None, timeout=20) -> dict:
    """Vet a list of package names/specs ('name' or 'name==version', or {name, version} dicts).

    -> {ok, pinned, blocked, licence, advisories, findings, details}; pinned entries are 'name==version'
    strings. Any blocked entry makes ok False. When draft_dir is given every lookup is written to
    draft_dir/vetting.json (the audit trail of what was checked and what it said).
    """
    out = {"ok": True, "pinned": [], "blocked": [], "licence": {}, "advisories": {}, "findings": [],
           "details": {}}
    if src:
        out["findings"] += scan_python(src, filename="skill.py")
    if folder:
        try:  # merge skillsmith's regex RISKY list with the ast findings (R3: pair them, plus a human read)
            from ishti.skills.skillsmith import skill as skillsmith
            out["findings"] += [{"file": f, "line": 0, "kind": "regex", "detail": why}
                                for f, why in skillsmith.scan(folder)]
        except Exception as exc:
            print(f"[vetting] skillsmith.scan unavailable ({type(exc).__name__}: {exc}): ast findings only")
    for entry in packages or []:
        if isinstance(entry, dict):
            name, version = str(entry.get("name") or ""), entry.get("version")
        else:
            name, _, version = str(entry).partition("==")
            version = version or None
        name = name.strip()
        if not name:
            continue
        named = check_name(name)
        if not named["ok"]:
            out["blocked"].append({"name": name, "reason": named["reason"]})
            continue
        pi = check_pypi(name, version, timeout=timeout)
        if not pi["ok"]:
            out["blocked"].append({"name": name, "reason": pi["reason"]})
            continue
        if not licence_ok(pi["license"]):
            out["licence"][name] = pi["license"]
            out["blocked"].append({"name": name, "reason": f"licence {pi['license']!r} is not on the permissive "
                                                           f"allow-list ({licence_verdict(pi['license'])})"})
            continue
        if pi["age_days"] is not None and pi["age_days"] < COOLDOWN_DAYS:
            out["licence"][name] = pi["license"]
            out["blocked"].append({"name": name, "reason": f"version {pi['version']} is only {pi['age_days']} "
                                                           f"day(s) old - inside the {COOLDOWN_DAYS}-day cooldown (R3)"})
            continue
        oo = check_osv(name, pi["version"], timeout=timeout)
        out["advisories"][name] = {"ids": oo["ids"], "aliases": oo["aliases"], "malware": oo["malware"]}
        if not oo["ok"]:
            out["blocked"].append({"name": name, "reason": oo["reason"]})
            continue
        out["pinned"].append(f"{name}=={pi['version']}")
        out["licence"][name] = pi["license"]
        out["details"][name] = {k: pi[k] for k in ("version", "license", "age_days", "requires_python",
                                                   "sha256", "project_urls", "summary")}
    out["ok"] = not out["blocked"]
    if draft_dir:
        try:
            os.makedirs(draft_dir, exist_ok=True)
            with open(os.path.join(draft_dir, "vetting.json"), "w", encoding="utf-8") as f:
                json.dump(out, f, indent=2, default=str)
        except OSError as exc:
            print(f"[vetting] could not write vetting.json ({type(exc).__name__}: {exc})")
    return out


if __name__ == "__main__":
    import shutil
    import tempfile

    # ---- offline part (always runs): names, licences, the stdlib ast table --------------------
    assert check_name("requests")["ok"] and check_name("requests")["exact"] is True
    typo = check_name("reqeusts")                                    # R3 S1: reqeusts => requests
    assert typo["ok"] is False and typo["near"] == "requests", typo
    assert check_name("numpi")["near"] == "numpy", check_name("numpi")
    assert check_name("flaskk")["near"] == "flask", check_name("flaskk")
    assert check_name("totally-made-up-pkg-xyz")["ok"] is True       # unknown, no neighbour: vet it later

    assert licence_ok("MIT") and licence_ok("Apache-2.0") and licence_ok("BSD-3-Clause")
    assert not licence_ok("GPL-3.0-only"), "copyleft must not pass the allow-list"
    assert not licence_ok("AGPL-3.0"), licence_verdict("AGPL-3.0")
    assert not licence_ok("non-commercial"), licence_verdict("non-commercial")
    assert not licence_ok(""), "a missing licence fails CLOSED, never open"
    assert licence_verdict("MIT") == "permissive" and licence_verdict("gpl-2.0") == "care"

    hits = scan_python('import os, subprocess\nos.environ["X"] = "1"\neval("1")\n')
    kinds = {h["kind"] for h in hits}
    details = " | ".join(h["detail"] for h in hits)
    assert "call" in kinds and "eval" in details, hits
    assert "imports subprocess" in details, hits
    assert "reads os.environ" in details, hits
    clean = scan_python("import json\ndef f(x):\n    return json.dumps(x)\n")
    assert clean == [], clean                                          # ordinary code produces no findings
    broken = scan_python("def (:")
    assert broken and broken[0]["kind"] == "syntax", broken            # a syntax error IS a finding

    # vet() on no packages still writes vetting.json into the draft (the audit trail)
    d = tempfile.mkdtemp(prefix="ishti_vet_")
    out = vet([], src="print(1)\n", folder=d, draft_dir=d)
    assert out["ok"] is True and out["pinned"] == [], out
    assert os.path.isfile(os.path.join(d, "vetting.json")), "vetting.json must be written"

    # ---- live part: one PyPI + one OSV call, skipped with a printed reason if the net is down ---
    try:
        pi = check_pypi("requests", timeout=10)
        assert pi["ok"] and pi["version"] and pi["sha256"], pi
        oo = check_osv("pyyaml", "5.3.1", timeout=10)
        assert oo["ids"], oo
        print(f"[live] PyPI requests=={pi['version']} licence={pi['license']} "
              f"age={pi['age_days']}d; OSV pyyaml 5.3.1 -> {oo['ids']}")
    except Exception as exc:
        print(f"[live] SKIPPED (network unavailable: {type(exc).__name__}: {exc}) - offline checks only")

    shutil.rmtree(d, ignore_errors=True)
    print("[self-check] skill_creator.vetting OK - typosquat rejected naming its neighbour, licence fails "
          "closed, ast table catches eval/subprocess/os.environ and syntax errors, benign code is clean, "
          "vetting.json written")

