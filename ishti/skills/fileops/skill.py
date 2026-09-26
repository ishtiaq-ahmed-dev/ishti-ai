"""File operations beyond organizing: batch rename (preview -> yes -> undo), zip/7z make and extract, duplicate
finder (removal goes to the Recycle Bin via send2trash - never a hard delete), compare two files/folders, file tags,
search inside files, safe delete, empty the Recycle Bin. Folder names like "downloads" resolve the same way the
folders skill does; renames reuse its move+undo log, so "undo that" works for them too."""
import difflib
import filecmp
import hashlib
import os
import re
import sqlite3
import subprocess
import zipfile

from ishti import ROOT
from ishti.skills import ask_first, trace

I = re.I
PATH = re.compile(r"[A-Za-z]:\\[^\"'<>|?*\n]+?(?=\s+(?:and|to|with|into|in|as)\s|\"|'|$|[,;])")
TEXT_EXT = {".txt", ".md", ".py", ".js", ".ts", ".tsx", ".json", ".csv", ".html", ".css", ".xml", ".yml", ".yaml",
            ".ini", ".cfg", ".log", ".sql", ".java", ".c", ".cpp", ".h", ".cs", ".go", ".rs", ".php", ".bat", ".ps1", ".tex"}
OPS = [
    ("rename", re.compile(r"\b(?:batch\s+)?rename\s+(?:all\s+)?(?:the\s+)?(?:files|photos|pictures|images|videos|pdfs|documents|songs)\b", I)),
    ("unzip", re.compile(r"\b(?:unzip|extract|decompress|unpack)\s+.+\.(?:zip|7z)\b|\b(?:unzip|extract)\s+(?:the\s+)?(?:zip|archive)\b", I)),
    ("zip", re.compile(r"\b(?:zip|compress|archive)\s+(?:up\s+)?(?:the\s+|my\s+)?(?!.*\.(?:zip|7z)\b).+", I)),
    ("dupes_delete", re.compile(r"\b(?:delete|remove|clean\s+up|get\s+rid\s+of)\s+(?:the\s+|all\s+)?duplicates?\b", I)),
    ("dupes", re.compile(r"\b(?:find|show|list|any|check\s+for)\s+(?:the\s+)?duplicate(?:\s+files)?\b|\bduplicate\s+files\b", I)),
    ("compare", re.compile(r"\b(?:compare|diff)\s+.+\s+(?:and|with|to)\s+.+", I)),
    ("tag_add", re.compile(r"\btag\s+(?P<path>.+?)\s+(?:as|with)\s+(?P<tag>[\w -]{1,30})$", I)),
    ("tag_find", re.compile(r"\b(?:files|documents)\s+tagged\s+(?:as\s+|with\s+)?(?P<tag>[\w -]{1,30})$|\bshow\s+(?:my\s+)?tags\b", I)),
    ("grep", re.compile(r"\b(?:search|look|find)\s+(?:inside|in\s+the\s+contents?\s+of|within)\s+(?:the\s+|my\s+)?files?\b|\bfiles?\s+(?:that\s+)?(?:contain|containing|mention)\b", I)),
    ("empty_bin", re.compile(r"\bempty\s+(?:the\s+|my\s+)?(?:recycle\s+bin|trash)\b", I)),
    ("delete", re.compile(r"^\s*(?:please\s+)?(?:delete|remove|trash)\s+(?:the\s+file\s+)?(?P<path>[A-Za-z]:\\.+?)\s*$", I)),
]


def match(text):
    return any(rx.search(text) for _, rx in OPS)


def run(text, session):
    for name, rx in OPS:
        if m := rx.search(text):
            trace(session, "files", name.replace("_", " "))
            out = globals()["op_" + name](m, text, session)
            return out if isinstance(out, dict) or out is None else {"reply": out, "sources": []}
    return None


def _folder(text):
    from ishti.skills.folders.skill import folder_in
    m = PATH.search(text)
    if m and os.path.isdir(m.group(0).strip()):
        return m.group(0).strip()
    return folder_in(text)[1]


def _paths(text):
    return [p.strip().rstrip(".") for p in PATH.findall(text) if os.path.exists(p.strip().rstrip("."))]


# ---------------------------------------------------------------- rename
def op_rename(m, text, session):
    from ishti.skills.folders.skill import TYPES, do_moves, kind_in, top_files
    folder = _folder(text)
    if not folder:
        return "Which folder? Say for example \"rename the photos in downloads to trip\"."
    kind = kind_in(text)
    prefix = re.search(r"\b(?:to|as|named?|called|with\s+(?:the\s+)?name)\s+[\"']?([\w][\w\- ]{0,40}?)[\"']?\s*(?:$|\bnumbered\b)", text, I)
    if not prefix:
        return "What should they be called? For example \"... to trip\" gives trip_001, trip_002, ..."
    prefix = prefix.group(1).strip().replace(" ", "_")
    files = sorted((e for e in top_files(folder) if not kind or os.path.splitext(e.name)[1].lower().lstrip(".") in TYPES.get(kind, ())),
                   key=lambda e: e.stat().st_mtime)
    if not files:
        return f"No {kind or ''} files to rename in {folder}."
    moves = [(e.path, os.path.join(folder, f"{prefix}_{i:03d}{os.path.splitext(e.name)[1].lower()}")) for i, e in enumerate(files, 1)]
    moves = [(a, b) for a, b in moves if os.path.basename(a) != os.path.basename(b)]
    preview = "\n".join(f"- {os.path.basename(a)} -> {os.path.basename(b)}" for a, b in moves[:8])

    def apply():
        done, failed, _ = do_moves(moves)
        return f"Renamed {len(done)} files" + (f" ({failed} were in use)" if failed else "") + ". Say \"undo that\" to reverse it."
    return ask_first(session, "fileops", f"Rename {len(moves)} {kind or ''} files in {folder}, oldest first:\n{preview}"
                                         + ("\n..." if len(moves) > 8 else "") + "\nGo ahead", apply)


# ---------------------------------------------------------------- archives
def op_zip(m, text, session):
    target = (_paths(text) or [None])[0] or _folder(text)
    if not target:
        return "What should I zip? Give me the file or folder path, or a folder name like downloads."
    out = target.rstrip("\\/") + ".zip"
    n = 0
    with zipfile.ZipFile(out if not os.path.exists(out) else out[:-4] + "_new.zip", "w", zipfile.ZIP_DEFLATED) as z:
        if os.path.isfile(target):
            z.write(target, os.path.basename(target))
            n = 1
        else:
            for root, _, names in os.walk(target):
                for f in names:
                    p = os.path.join(root, f)
                    z.write(p, os.path.relpath(p, os.path.dirname(target)))
                    n += 1
        name = z.filename
    return f"Zipped {n} file(s) into {name} ({os.path.getsize(name) / 2**20:.1f} MB)."


def op_unzip(m, text, session):
    arch = next((p for p in _paths(text) if p.lower().endswith((".zip", ".7z"))), None)
    if not arch:
        return "Which archive? Give me its full path."
    dest = os.path.splitext(arch)[0]
    os.makedirs(dest, exist_ok=True)
    if arch.lower().endswith(".7z"):
        import py7zr
        with py7zr.SevenZipFile(arch) as z:
            names = z.getnames()
            z.extractall(dest)
    else:
        with zipfile.ZipFile(arch) as z:
            bad = [n for n in z.namelist() if os.path.isabs(n) or ".." in n.replace("\\", "/").split("/")]
            if bad:  # zip-slip: an entry that would land outside the destination
                return f"Not extracting: {os.path.basename(arch)} has entries that point outside the folder ({bad[0]})."
            names = z.namelist()
            z.extractall(dest)
    return f"Extracted {len(names)} item(s) to {dest}."


# ---------------------------------------------------------------- duplicates
def _hash(path, block=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(block):
            h.update(chunk)
    return h.hexdigest()


def _quick(path, n=1 << 16):
    """Size + first/middle/last 64 KB: tells different same-size files apart without reading gigabytes."""
    size = os.path.getsize(path)
    h = hashlib.sha256(str(size).encode())
    with open(path, "rb") as f:
        for pos in (0, max(0, size // 2 - n // 2), max(0, size - n)):
            f.seek(pos)
            h.update(f.read(n))
    return h.hexdigest()


SKIP_DIRS = {"node_modules", ".git", "venv", ".venv", "__pycache__", "site-packages", ".cache", ".next", "dist",
             "build", ".gradle", ".idea", ".vs", "appdata"}
MIN_DUP_BYTES = 100 << 10  # ponytail: duplicate tiny files waste little space; skipping them keeps huge trees fast
SCAN_BUDGET_S = 60


def duplicates(folder, full_hash_limit=1 << 30, budget_s=SCAN_BUDGET_S, stats=None, min_bytes=MIN_DUP_BYTES):
    """[[paths with identical content]], keeping the oldest first. Size, then a quick fingerprint, then a full hash
    only for files that still look the same.
    ponytail: files over 1 GB (films) are judged by size + fingerprint alone - reading them whole took minutes; a
    false match would need identical size and identical head/middle/tail. Full-hash them if that ever matters."""
    import time
    stats = stats if stats is not None else {}
    deadline, seen = time.time() + budget_s, 0
    by_size = {}
    for root, dirs, names in os.walk(folder):
        # dependency/cache folders are full of legitimate copies nobody wants reported (1.5M files in one Downloads)
        dirs[:] = [d for d in dirs if d.lower() not in SKIP_DIRS]
        for n in names:
            p = os.path.join(root, n)
            seen += 1
            try:
                size = os.path.getsize(p)
            except OSError:
                continue
            if size >= min_bytes:
                by_size.setdefault(size, []).append(p)
        if time.time() > deadline:
            stats["partial"] = True
            break
    stats["scanned"] = seen
    groups = {}
    for paths in (v for v in by_size.values() if len(v) > 1):
        quick = {}
        for p in paths:
            try:
                quick.setdefault(_quick(p), []).append(p)
            except OSError:
                continue
        for q, same in quick.items():
            if len(same) < 2:
                continue
            for p in same:
                try:
                    key = q if os.path.getsize(p) > full_hash_limit else _hash(p)
                    groups.setdefault(key, []).append(p)
                except OSError:
                    continue
    return [sorted(g, key=os.path.getmtime) for g in groups.values() if len(g) > 1]


def op_dupes(m, text, session):
    folder = _folder(text)
    if not folder:
        return "Which folder should I check for duplicates?"
    trace(session, "hash", f"comparing file contents in {folder}", status="start")
    stats = {}
    groups = duplicates(folder, stats=stats)
    trace(session, "hash", f"{len(groups)} duplicate sets in {stats.get('scanned', 0):,} files", status="done")
    note = (f"\n(Stopped after {SCAN_BUDGET_S} s and {stats['scanned']:,} files - the folder is huge; ask about a "
            "subfolder to go deeper.)" if stats.get("partial") else "")
    if not groups:
        return f"No duplicate files (100 KB or bigger) in {folder}." + note
    waste = sum(os.path.getsize(g[0]) * (len(g) - 1) for g in groups)
    session["dupes"] = {"folder": folder, "groups": groups}
    lines = [f"- {os.path.basename(g[0])} ×{len(g)}" for g in groups[:12]]
    return (f"{len(groups)} sets of duplicates in {folder}, {waste / 2**20:.1f} MB wasted:\n" + "\n".join(lines)
            + ("\n..." if len(groups) > 12 else "") + "\nSay \"delete the duplicates\" to move the extra copies to the Recycle Bin (the oldest copy stays)." + note)


def op_dupes_delete(m, text, session):
    found = session.get("dupes") or ({"groups": duplicates(_folder(text))} if _folder(text) else None)
    if not found or not found["groups"]:
        return "Ask me to find duplicates in a folder first."
    extra = [p for g in found["groups"] for p in g[1:]]

    def trash():
        from send2trash import send2trash
        n = 0
        for p in extra:
            try:
                send2trash(p)
                n += 1
            except OSError:
                pass
        session["dupes"] = None
        return f"Moved {n} duplicate copies to the Recycle Bin (restore them from there if needed)."
    return ask_first(session, "fileops", f"Move {len(extra)} duplicate copies to the Recycle Bin, keeping the oldest of each", trash)


# ---------------------------------------------------------------- compare, tags, search
def op_compare(m, text, session):
    paths = _paths(text)
    if len(paths) < 2:
        return "Give me the two full paths to compare."
    a, b = paths[:2]
    if os.path.isdir(a) and os.path.isdir(b):
        c = filecmp.dircmp(a, b)
        return (f"Only in {os.path.basename(a)}: {', '.join(c.left_only[:15]) or 'nothing'}\n"
                f"Only in {os.path.basename(b)}: {', '.join(c.right_only[:15]) or 'nothing'}\n"
                f"Different: {', '.join(c.diff_files[:15]) or 'nothing'} · Same: {len(c.same_files)}")
    if os.path.splitext(a)[1].lower() in TEXT_EXT and os.path.getsize(a) < 2 << 20:
        with open(a, encoding="utf-8", errors="replace") as fa, open(b, encoding="utf-8", errors="replace") as fb:
            diff = list(difflib.unified_diff(fa.read().splitlines(), fb.read().splitlines(),
                                             os.path.basename(a), os.path.basename(b), lineterm="", n=1))
        return "They're identical." if not diff else "```diff\n" + "\n".join(diff[:120]) + "\n```"
    return "They're identical." if filecmp.cmp(a, b, shallow=False) else "They're different files (binary content differs)."


def _tags():
    db = sqlite3.connect(os.path.join(ROOT, "data", "tags.db"))
    db.execute("CREATE TABLE IF NOT EXISTS tags(path TEXT, tag TEXT, UNIQUE(path, tag))")
    return db


def op_tag_add(m, text, session):
    path = (_paths(text) or [None])[0]
    if not path:
        return "Give me the file's full path to tag."
    tag = m.group("tag").strip().lower()
    with _tags() as db:
        db.execute("INSERT OR IGNORE INTO tags VALUES(?, ?)", (path, tag))
    return f"Tagged {os.path.basename(path)} as '{tag}'."


def op_tag_find(m, text, session):
    with _tags() as db:
        if m.group("tag"):
            rows = [r[0] for r in db.execute("SELECT path FROM tags WHERE tag=?", (m.group("tag").strip().lower(),))]
            return (f"Tagged '{m.group('tag').strip()}':\n" + "\n".join(f"- {p}" for p in rows)) if rows else "Nothing has that tag."
        rows = list(db.execute("SELECT tag, COUNT(*) FROM tags GROUP BY tag ORDER BY 2 DESC"))
    return ("Your tags:\n" + "\n".join(f"- {t} ({n})" for t, n in rows)) if rows else "No tags yet. Say \"tag <path> as <tag>\"."


def op_grep(m, text, session):
    needle = re.search(r"[\"']([^\"']{2,80})[\"']|\b(?:contain(?:s|ing)?|mention(?:s|ing)?|for)\s+(?P<w>[\w .@-]{2,60}?)(?:\s+in\s+|$)", text, I)
    word = needle and (needle.group(1) or needle.group("w"))
    folder = _folder(text) or os.path.expanduser("~\\Documents")
    if not word:
        return "What text should I look for? Put it in quotes."
    trace(session, "grep", f"searching text files in {folder} for '{word}'", status="start")
    hits, low = [], word.lower()
    for root, dirs, names in os.walk(folder):
        dirs[:] = [d for d in dirs if d.lower() not in {"node_modules", ".git", "venv", "__pycache__", "appdata"}]
        for n in names:
            p = os.path.join(root, n)
            if os.path.splitext(n)[1].lower() not in TEXT_EXT or os.path.getsize(p) > 2 << 20:
                continue
            try:
                with open(p, encoding="utf-8", errors="ignore") as f:
                    for i, line in enumerate(f, 1):
                        if low in line.lower():
                            hits.append(f"- {p}:{i}: {line.strip()[:100]}")
                            break
            except OSError:
                continue
            if len(hits) >= 25:
                break
        if len(hits) >= 25:
            break
    trace(session, "grep", f"{len(hits)} files", status="done")
    return (f"Files in {folder} containing '{word}':\n" + "\n".join(hits)) if hits else f"No text file in {folder} contains '{word}'."


# ---------------------------------------------------------------- delete
def op_delete(m, text, session):
    path = m.group("path").strip().strip("\"'")
    if not os.path.exists(path):
        return f"{path} doesn't exist."
    from send2trash import send2trash
    return ask_first(session, "fileops", f"Move {path} to the Recycle Bin",
                     lambda: (send2trash(path), f"Moved {os.path.basename(path)} to the Recycle Bin.")[1])


def op_empty_bin(m, text, session):
    def empty():
        p = subprocess.run(["powershell", "-NoProfile", "-Command", "Clear-RecycleBin -Force -ErrorAction SilentlyContinue"],
                           capture_output=True, text=True, timeout=120, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return "Recycle Bin emptied." if p.returncode == 0 else f"Couldn't empty it: {p.stderr.strip()[:200]}"
    return ask_first(session, "fileops", "Permanently delete everything in the Recycle Bin (this can't be undone)", empty)


if __name__ == "__main__":
    import shutil
    import tempfile
    cases = {"rename the photos in downloads to trip": "rename", r"zip D:\x\folder": "zip", r"unzip D:\x\a.zip": "unzip",
             "find duplicate files in downloads": "dupes", "delete the duplicates": "dupes_delete",
             r"compare D:\a.txt and D:\b.txt": "compare", r"tag D:\x\cv.pdf as job": "tag_add", "files tagged job": "tag_find",
             "search inside files for 'invoice' in documents": "grep", "empty the recycle bin": "empty_bin",
             r"delete D:\x\old.txt": "delete"}
    for t, want in cases.items():
        got = next((n for n, rx in OPS if rx.search(t)), None)
        assert got == want, (t, got, want)
    assert not match("what is the weather") and not match("organize my downloads")
    d = tempfile.mkdtemp()
    for name, body in (("a.txt", "hello"), ("b.txt", "hello"), ("c.txt", "other")):
        with open(os.path.join(d, name), "w") as f:
            f.write(body)
    assert [sorted(map(os.path.basename, g)) for g in duplicates(d, min_bytes=1)] == [["a.txt", "b.txt"]]
    assert duplicates(d) == [], "tiny files are not reported by default"
    s = {}
    out = op_zip(None, f"zip {d}", s)
    assert "Zipped 3 file(s)" in out, out
    out = op_unzip(None, f"unzip {d}.zip", s)
    assert "Extracted 3" in out and os.path.isfile(os.path.join(d, os.path.basename(d), "a.txt")), out
    assert op_compare(None, f"compare {d}\\a.txt and {d}\\b.txt", s) == "They're identical."
    shutil.rmtree(d, ignore_errors=True)
    os.remove(d + ".zip")
    print("[self-check] fileops OK - routing, duplicates, zip/unzip roundtrip, compare")
