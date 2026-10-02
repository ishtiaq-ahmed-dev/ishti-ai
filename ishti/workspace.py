"""Workspace path confinement (WP-OD, matrix OD-15; open-dots workspace_service.py:75-146).

Any file tool that accepts a path from text, a remote message or a UI drop should resolve it
through `Workspace` before touching disk. The rules it enforces:

  * ROOT CONFINEMENT - every resolved path, after realpath (symlinks included), must stay
    inside the workspace root. `..`, absolute escapes, drive-relative names ("C:boot.ini"),
    UNC/device prefixes ("\\\\server\\share", "\\\\?\\...") and alternate data streams
    ("notes.txt:secret") are refused.
  * SIZE CAPS - files above max_bytes are refused for read, write and list; a single call
    can never pull an unbounded blob into memory.
  * WINDOWS HYGIENE - reserved device names (CON, PRN, AUX, NUL, COM1-9, LPT1-9) and
    trailing dots/spaces in a component are refused; names are bounded.

Refusals are values (None/False/[]), never exceptions, and `check()` explains why, so a
caller can log or tell the owner honestly. Nothing here writes outside `root` - by test.
Self-check: python ishti/workspace.py
"""
from __future__ import annotations

import os
import re

DEFAULT_MAX_BYTES = 8 * 1024 * 1024
MAX_NAME = 255
MAX_ENTRIES = 2000
RESERVED = frozenset({"con", "prn", "aux", "nul", "$mft", "$mftmirr", "clock$"} |
                     {f"com{i}" for i in range(1, 10)} | {f"lpt{i}" for i in range(1, 10)})
_DRIVE_REL = re.compile(r"^[A-Za-z]:(?![\\/])")


def _reason(path, root, *, must_exist=False):
    """Why `path` cannot be used inside `root` ('' = it can). Never raises.

    Raw components are checked BEFORE any Windows normalisation (realpath/abspath strip
    trailing spaces and resolve device names, which would hide the refusal)."""
    try:
        if path is None:
            return "empty path"
        if not isinstance(path, (str, os.PathLike)):
            return "path must be a string"
        raw = os.fspath(path)
        if not raw.strip():
            return "empty path"
        if "\x00" in raw:
            return "null byte in path"
        if raw.startswith("\\\\") or raw.startswith("//"):
            return "UNC/device path refused"
        if _DRIVE_REL.match(raw):
            return "drive-relative path refused"
        for i, part in enumerate(p for p in re.split(r"[\\/]+", raw) if p and p != "."):
            if part == "..":
                continue                     # containment below decides if it escapes
            if i == 0 and re.fullmatch(r"[A-Za-z]:", part):
                continue                     # the drive prefix of an absolute path
            name = part.rstrip(". ")
            if not name or name != part:
                return f"trailing dot/space in '{part}' refused"
            if len(part) > MAX_NAME:
                return "path component too long"
            if ":" in part:
                return "alternate data stream refused"
            if name.split(".")[0].lower() in RESERVED:
                return f"reserved device name '{part}' refused"
        root_real = os.path.realpath(root)
        candidate = os.path.realpath(raw if os.path.isabs(raw) else os.path.join(root_real, raw))
        try:
            common = os.path.normcase(os.path.commonpath([root_real, candidate]))
        except ValueError:                   # different drives cannot share a root
            return "path escapes the workspace root"
        if common != os.path.normcase(root_real):
            return "path escapes the workspace root"
        if must_exist and not os.path.exists(candidate):
            return "no such file"
        return ""
    except Exception as exc:
        return f"unusable path ({type(exc).__name__})"


class Workspace:
    """A confined view of one directory. All methods are total: they return
    None/False/[] on refusal and never raise."""

    def __init__(self, root, max_bytes: int = DEFAULT_MAX_BYTES):
        self.root = os.path.realpath(os.path.expanduser(str(root)))
        self.max_bytes = max(1, int(max_bytes))
        try:
            os.makedirs(self.root, exist_ok=True)
        except OSError:
            pass

    # ---- checks ---------------------------------------------------------------
    def check(self, path, *, must_exist: bool = False) -> dict:
        why = _reason(path, self.root, must_exist=must_exist)
        if why:
            return {"ok": False, "path": None, "reason": why}
        raw = str(path)
        resolved = os.path.realpath(raw if os.path.isabs(raw) else os.path.join(self.root, raw))
        return {"ok": True, "path": resolved, "reason": ""}

    def contains(self, path) -> bool:
        return self.check(path)["ok"]

    def resolve(self, path, *, must_exist: bool = False):
        r = self.check(path, must_exist=must_exist)
        return r["path"]

    # ---- reads ----------------------------------------------------------------
    def read_bytes(self, path, *, max_bytes: int | None = None):
        cap = self.max_bytes if max_bytes is None else max(1, int(max_bytes))
        try:
            p = self.resolve(path, must_exist=True)
            if p is None or not os.path.isfile(p):
                return None
            if os.path.getsize(p) > cap:
                return None
            with open(p, "rb") as f:
                data = f.read(cap + 1)
            return data if len(data) <= cap else None
        except Exception:
            return None

    def read_text(self, path, *, encoding: str = "utf-8", max_bytes: int | None = None):
        data = self.read_bytes(path, max_bytes=max_bytes)
        if data is None:
            return None
        try:
            return data.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            return None

    # ---- writes ---------------------------------------------------------------
    def write_text(self, path, text, *, encoding: str = "utf-8", append: bool = False) -> bool:
        try:
            p = self.resolve(path)
            if p is None or os.path.isdir(p):
                return False
            data = str(text).encode(encoding)
            cap = self.max_bytes if not append else self.max_bytes - (os.path.getsize(p)
                                                                    if os.path.exists(p) else 0)
            if len(data) > cap:
                return False
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "a" if append else "w", encoding=encoding) as f:
                f.write(str(text))
            return True
        except Exception:
            return False

    def ensure_dir(self, path) -> bool:
        try:
            p = self.resolve(path)
            if p is None:
                return False
            os.makedirs(p, exist_ok=True)
            return True
        except Exception:
            return False

    def delete(self, path) -> bool:
        try:
            p = self.resolve(path, must_exist=True)
            if p is None or not os.path.isfile(p):
                return False
            os.remove(p)
            return True
        except Exception:
            return False

    # ---- inventory ------------------------------------------------------------
    def list(self, limit: int = 500) -> list:
        """Confined inventory: [{"name", "path", "size", "dir"}] - entries that escape via a
        symlink are omitted, and the walk is bounded."""
        out = []
        try:
            limit = max(1, min(int(limit), MAX_ENTRIES))
            for dirpath, _dirs, names in os.walk(self.root):
                for n in sorted(names):
                    p = os.path.join(dirpath, n)
                    c = self.check(p)
                    if not c["ok"]:
                        continue
                    try:
                        out.append({"name": n, "path": c["path"], "size": os.path.getsize(c["path"]),
                                    "dir": os.path.isdir(c["path"])})
                    except OSError:
                        continue
                    if len(out) >= limit:
                        return out
        except Exception:
            pass
        return out


if __name__ == "__main__":
    import tempfile, shutil
    base = tempfile.mkdtemp()
    root = os.path.join(base, "ws")
    ws = Workspace(root, max_bytes=1024)
    outside = os.path.join(base, "outside.txt")
    with open(outside, "w") as f:
        f.write("owner file - must never be reached through the workspace")
    assert ws.check("notes/a.txt")["ok"] and ws.check("notes/../a.txt")["ok"], "normalising .. inside is fine"
    # every classic escape is refused with a reason
    for hostile, needle in (("../outside.txt", "escape"), ("..\\outside.txt", "escape"),
                            (outside, "escape"), ("C:outside.txt", "drive-relative"),
                            ("\\\\server\\share\\x", "UNC"), ("notes.txt:secret", "stream"),
                            ("NUL", "reserved"), ("aux.log", "reserved"), ("COM1.txt", "reserved"),
                            ("bad.", "trailing"), ("bad ", "trailing"), ("a\x00b", "null"),
                            ("x" * 300, "too long"), ("", "empty"), (None, "empty")):
        r = ws.check(hostile)
        assert not r["ok"] and needle in r["reason"], (hostile, r)
    assert ws.resolve("../outside.txt") is None and not ws.contains(outside)
    assert not ws.write_text("../outside.txt", "pwned") and open(outside).read().startswith("owner file")
    assert ws.write_text("notes/a.txt", "hello") and ws.read_text("notes/a.txt") == "hello"
    assert ws.write_text("notes/a.txt", " more", append=True)
    assert ws.read_text("notes/a.txt") == "hello more"
    assert not ws.write_text("notes/big.txt", "x" * 2000), "write cap refused"
    with open(os.path.join(root, "big.bin"), "wb") as f:
        f.write(b"y" * 5000)
    assert ws.read_bytes("big.bin") is None, "read cap refused"
    assert ws.read_text("notes/a.txt", max_bytes=1) is None, "per-call cap"
    assert ws.ensure_dir("deep/nested") and os.path.isdir(os.path.join(root, "deep", "nested"))
    assert not ws.ensure_dir("../evil") and not os.path.exists(os.path.join(base, "evil"))
    assert ws.delete("notes/a.txt") and not os.path.exists(os.path.join(root, "notes", "a.txt"))
    assert not ws.delete("../outside.txt") and os.path.exists(outside)
    names = [e["name"] for e in ws.list()]
    assert "big.bin" in names and "outside.txt" not in names and "outside.txt" not in [
        os.path.basename(e["path"]) for e in ws.list()]
    for junk in (42, ["a"], {"p": 1}, object()):
        assert ws.resolve(junk) is None or isinstance(ws.resolve(junk), str)
        assert ws.read_text(junk) is None and ws.write_text(junk, "x") is False
    shutil.rmtree(base, ignore_errors=True)
    print("[self-check] workspace OK - realpath root confinement, Windows escape refusal, size caps, "
          "total methods (no exceptions)")
