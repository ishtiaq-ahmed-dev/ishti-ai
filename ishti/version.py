"""The product version, and a hash of the source this build is running from.

One source of truth for both, so that a measured number can name the revision that produced it:
a number with no revision attached is not evidence (docs/EVIDENCE.md).

``__version__`` is what a person reads. ``source_hash()`` is what a report stamps - sha256 over the
product's source and test files in a stable order, so it is the same on any machine and works for
the ZIP download that install.bat expects, where there is no git at all. When the folder *is* a
checkout, ``revision()`` adds the commit and whether the tree was dirty: the finer answer.

Stdlib only, and deliberately importing nothing else from ``ishti`` - ``python -m ishti --version``
reads this before anything boots, and an import cycle here would make the version unprintable
exactly when it is needed most.
"""
import hashlib
import os
import platform
import shutil
import subprocess

__version__ = "0.2.0"

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# What the hash covers: the code that runs, and the fixtures that grade it. docs/, data/, reports/
# and build output are not in it - they cannot change what ISHTI does.
SOURCE_DIRS = ("ishti", "interface", "desktop", "orchestrator", "tools", "tests")
SOURCE_FILES = ("install.bat", "start.bat", "stop.bat", "ISHTI.spec", "pytest.ini",
                "requirements.txt", "requirements-api.txt", "models_ladder.yaml")
_SKIP_DIRS = frozenset({"__pycache__", "node_modules", "dist", "build", ".git", "_drafts",
                        "_work", ".venv", "venv", ".pytest_cache", ".qa_nemotron"})
_SKIP_SUFFIX = (".pyc", ".pyo", ".pyd", ".so", ".dll")
_MAX_BYTES = 4_000_000   # a single file larger than this is hashed truncated, never skipped


def _iter_source_files():
    """Every hashed file as a repo-relative posix path, in sorted order (a walk order is not stable)."""
    for name in SOURCE_DIRS:
        for folder, subfolders, files in os.walk(os.path.join(ROOT, name)):
            subfolders[:] = sorted(d for d in subfolders if d not in _SKIP_DIRS)
            for fn in sorted(files):
                if fn.endswith(_SKIP_SUFFIX):
                    continue
                yield os.path.relpath(os.path.join(folder, fn), ROOT).replace("\\", "/")
    for name in SOURCE_FILES:
        if os.path.isfile(os.path.join(ROOT, name)):
            yield name


_hash_cache = None


def source_hash():
    """sha256 over the source files, path and content, cached for the life of the process."""
    global _hash_cache
    if _hash_cache is None:
        digest = hashlib.sha256()
        for rel in _iter_source_files():
            digest.update(rel.encode("utf-8"))
            digest.update(b"\0")
            try:
                with open(os.path.join(ROOT, rel), "rb") as fh:
                    digest.update(fh.read(_MAX_BYTES))
            except OSError:
                digest.update(b"<unreadable>")
            digest.update(b"\0")
        _hash_cache = digest.hexdigest()
    return _hash_cache


def _git(*args):
    """stdout of a git command in the project folder, or None when git is absent or the command failed."""
    exe = shutil.which("git")
    if not exe:
        return None
    try:
        out = subprocess.run([exe, *args], cwd=ROOT, capture_output=True, text=True, timeout=10,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() if out.returncode == 0 else None


def revision():
    """The short commit this folder is at, or None for a plain download with no git history."""
    return _git("rev-parse", "--short", "HEAD") or None


def revision_status():
    """"clean", "dirty" (the working tree differs from that commit), or "unknown" (no git)."""
    if not revision():
        return "unknown"
    status = _git("status", "--porcelain")
    if status is None:
        return "unknown"
    return "dirty" if status else "clean"


def info():
    """The version record: what /api/version returns and what an evidence report quotes."""
    return {"name": "ISHTI", "version": __version__,
            "revision": revision(), "revision_status": revision_status(),
            "source_hash": source_hash(), "source_hash_covers": list(SOURCE_DIRS),
            "python": platform.python_version(), "platform": platform.platform()}


def describe():
    """The block ``python -m ishti --version`` prints: copy-pasteable into a bug report or a report."""
    status = revision_status()
    rev = revision() or "no git checkout in this folder"
    if status == "dirty":
        rev += "  (working tree has uncommitted changes)"
    return "\n".join([
        f"ISHTI {__version__}",
        f"  revision  {rev}",
        f"  source    {source_hash()}",
        f"  covers    {', '.join(SOURCE_DIRS)}",
        f"  python    {platform.python_version()} on {platform.platform()}",
    ])


if __name__ == "__main__":
    assert isinstance(__version__, str) and __version__.count(".") == 2, __version__
    assert _git("--version") or True   # never raises when git is missing: a download has no git
    first = source_hash()
    assert len(first) == 64 and first == source_hash(), "the hash must be stable and cached"
    names = set(_iter_source_files())
    assert "ishti/version.py" in names and "interface/app/dist" not in " ".join(names), "scope"
    assert {"version", "revision", "revision_status", "source_hash"} <= set(info())
    assert describe().startswith(f"ISHTI {__version__}")
    print(describe())
    print("[self-check] version OK - stable source hash over the product, git revision when present")
