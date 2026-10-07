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
from ishti.test_mode import scratch_path

try:  # best-effort failure log (never breaks the module if logging is unavailable)
    from ishti.core.logutil import swallowed
except Exception:  # pragma: no cover
    def swallowed(*_a, **_k):
        return None


I = re.I
PATH = re.compile(r"[A-Za-z]:\\[^\"'<>|?*\n]+?(?=\s+(?:and|to|with|into|in|as)\s|\"|'|$|[,;])")
TEXT_EXT = {".txt", ".md", ".py", ".js", ".ts", ".tsx", ".json", ".csv", ".html", ".css", ".xml", ".yml", ".yaml",
            ".ini", ".cfg", ".log", ".sql", ".java", ".c", ".cpp", ".h", ".cs", ".go", ".rs", ".php", ".bat", ".ps1", ".tex"}
OPS = [
    # "Rename report_final_v2.docx to Q3_Report.docx" - a single named file with an extension (checklist #20).
    ("rename_file", re.compile(r"^\s*(?:please\s+)?rename\s+(?P<src>[^\s]+\.[a-z0-9]{2,5})\s+to\s+(?P<dst>[^\s]+\.[a-z0-9]{2,5})\s*$", I)),
    ("rename", re.compile(r"\b(?:batch\s+)?rename\s+(?:all\s+)?(?:the\s+)?(?:files|photos|pictures|images|videos|pdfs|documents|songs)\b", I)),
    ("unzip", re.compile(r"\b(?:unzip|extract|decompress|unpack)\s+.+\.(?:zip|7z)\b|\b(?:unzip|extract)\s+(?:the\s+)?(?:zip|archive)\b", I)),
    ("zip", re.compile(r"\b(?:zip|compress|archive)\s+(?:up\s+)?(?:the\s+|my\s+)?(?!.*\.(?:zip|7z)\b).+", I)),
    ("dupes_delete", re.compile(r"\b(?:delete|remove|clean\s+up|get\s+rid\s+of)\s+(?:the\s+|all\s+)?duplicates?\b", I)),
    ("dupes", re.compile(r"\b(?:find|show|list|any|check\s+for)\s+(?:the\s+)?duplicate(?:\s+files)?\b|\bduplicate\s+files\b", I)),
    # "compare three local TTS engines for Urdu support and recommend one" is research, not a file diff
    # (checklist #8). File compare owns: two concrete paths/names with "and/with/to" between them. When the
    # text is clearly about abstract things (engines, models, methods, services, tools...) rather than files,
    # it is not ours. Also drop it when web_search or deep_research would clearly own the query.
    ("compare", re.compile(r"\b(?:compare|diff)\s+.+\s+(?:and|with|to)\s+.+", I)),
    ("tag_add", re.compile(r"\btag\s+(?P<path>.+?)\s+(?:as|with)\s+(?P<tag>[\w -]{1,30})$", I)),
    ("tag_find", re.compile(r"\b(?:files|documents)\s+tagged\s+(?:as\s+|with\s+)?(?P<tag>[\w -]{1,30})$|\bshow\s+(?:my\s+)?tags\b", I)),
    ("grep", re.compile(r"\b(?:search|look|find)\s+(?:inside|in\s+the\s+contents?\s+of|within)\s+(?:the\s+|my\s+)?files?\b|\bfiles?\s+(?:that\s+)?(?:contain|containing|mention)\b", I)),
    # "Back up my whole project right now" (checklist #240): no path in the request, so resolve the project
    # by name and confirm scope + destination before copying.
    ("backup_project", re.compile(r"\b(?:back\s*?up|backup)\s+(?:my\s+|the\s+)?(?:whole\s+|entire\s+|main\s+)?"
                                  r"(?:project|repo|repository|code|files?|work|everything|pc|computer)\b", I)),
    ("backup", re.compile(r"\b(?P<act>back\s*up|backup|sync|mirror)\s+(?:my\s+|the\s+)?[\"']?(?P<src>[a-z]:[\\/][^\"']+?)[\"']?\s+(?:to|into|with|onto)\s+[\"']?(?P<dst>[a-z]:[\\/]?[^\"']*?)[\"']?\s*$", I)),
    ("empty_bin", re.compile(r"\bempty\s+(?:the\s+|my\s+)?(?:recycle\s+bin|trash)\b", I)),
    ("delete_referenced", re.compile(r"^\s*(?:please\s+)?(?:all\s+these\s+)?(?:delete|remove|trash)\s+(?:them|these|all\s+these|all\s+of\s+them|the\s+above(?:\s+files)?)\b", I)),
    ("delete", re.compile(r"^\s*(?:please\s+)?(?:delete|remove|trash)\s+(?:the\s+file\s+)?(?P<path>[A-Za-z]:\\.+?)\s*$", I)),
    ("delete_ambiguous", re.compile(r"^\s*(?:please\s+)?(?:delete|remove|trash)\s+(?:that\s+|this\s+|the\s+)?file\b", I)),
    # Checklist phrasings that mean "delete/copy/move something by name" but don't include a full path regex.
    ("delete_folder", re.compile(r"^\s*(?:please\s+)?(?:delete|remove|get\s+rid\s+of|trash)\s+(?:the\s+|my\s+)?[a-z0-9_\- ]*?folder\b", I)),
    ("copy", re.compile(r"^\s*(?:please\s+)?copy\s+.+\s+to\s+", I)),
    ("move", re.compile(r"^\s*(?:please\s+)?(?:move|cut|relocate)\s+.+\s+to\s+", I)),
]

# A time word alone ("tomorrow", "Thursday", "1:30") is not a file operation: schedule analysis,
# back-to-back and double-booked questions, and video thumbnails all carry one (checklist #155, #156,
# #173). The old "move_time" entry matched any time word and even had no op_move_time to run - it
# would have crashed on a match. Reminder reschedules are reminders' job, not fileops'.
TIME_WORD = re.compile(r"\b(?:tomorrow|monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
                       r"next\s+week|next\s+month|at\s+\d|\d{1,2}:\d{2}|\d{1,2}\s?(?:am|pm))\b", I)
# "compress this video for YouTube", "trim the first 10 seconds off this video": the video/media
# skills own any clip being edited, never the archive tool (checklist #172, #176).
VIDEO_OR_MEDIA = re.compile(r"\b(?:video|clip|movie|footage|reel|film|youtube|instagram|tiktok|thumbnail|"
                            r"caption|subtitle|audio|song|track|podcast|voice\s*note)\b", I)


def match(text):
    # A media request that happens to use "compress"/"archive" belongs to the video/media skill.
    if VIDEO_OR_MEDIA.search(text) and not _paths(text):
        return False
    # "move my 3pm thing to tomorrow" is a reminder reschedule, not a file move (checklist #15).
    for _n, rx in OPS:
        if rx.search(text):
            if _n == "move" and TIME_WORD.search(text):
                return False
            # "compare three TTS engines for Urdu support and recommend one" is research, not a file diff (checklist #8).
            if _n == "compare" and re.search(r"\b(?:tts|engine|model|method|approach|service|tool|framework|library|solution|option|product|software|system|solution|algorithm|technique|strategy|platform|provider|vendor|api|protocol)\b", text, I):
                return False
            return True
    return False


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
def op_rename_file(m, text, session):
    """"Rename report_final_v2.docx to Q3_Report.docx": find the single file by name, confirm old -> new."""
    src, dst = m["src"].strip(), m["dst"].strip()
    src_path = src if os.path.isfile(src) else None
    if not src_path:
        from desktop.computer_use import get_computer_use_agent
        hits = get_computer_use_agent().search_pc(os.path.splitext(src)[0], max_results=5)
        exact = [h for h in hits if os.path.basename(h["path"]).lower() == src.lower()]
        if len(exact) == 1:
            src_path = exact[0]["path"]
        elif len(hits) == 1:
            src_path = hits[0]["path"]
        elif hits:
            return ("I found several matches for " + src + ": "
                    + ", ".join(h["path"] for h in hits[:4]) + ". Which one should I rename?")
    if not src_path or not os.path.isfile(src_path):
        return f"I couldn't find {src} in your user folders. Give me the exact path and I'll rename it."
    target = os.path.join(os.path.dirname(src_path), dst)
    if os.path.exists(target):
        return f"{target} already exists - pick a different name so nothing is overwritten."
    return ask_first(session, "fileops", f"Rename {os.path.basename(src_path)} to {dst}",
                     lambda: (os.rename(src_path, target),
                              f"Renamed {os.path.basename(src_path)} to {dst}.")[1])


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
        n, failed = 0, []
        for p in extra:
            try:
                send2trash(p)
                n += 1
            except OSError as _exc:
                swallowed('ishti/skills/fileops/skill.py:op_dupes_delete.trash', _exc)
                failed.append(os.path.basename(p))
        session["dupes"] = None
        msg = f"Moved {n} duplicate copies to the Recycle Bin (restore them from there if needed)."
        if failed:  # never claim the whole job was done when some copies could not be moved
            msg += (f" {len(failed)} could not be moved (in use or no permission), so they are still there: "
                    + ", ".join(failed[:5]) + (" ..." if len(failed) > 5 else ""))
        return msg
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
    db = sqlite3.connect(scratch_path(os.path.join(ROOT, "data", "tags.db")))
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
    if not word:
        return "What text should I look for? Put it in quotes."
    roots = _scope(text)
    if roots:
        return _rg(word, roots, session)
    folder = _folder(text) or os.path.expanduser("~\\Documents")
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


RG = os.path.join(ROOT, "data", "bin", "rg.exe")
SKIP = ["node_modules", ".git", "venv", "__pycache__", "AppData", "Windows", "Program Files", "Program Files (x86)",
        "$Recycle.Bin", "ProgramData", "System Volume Information", "site-packages", ".cache", ".ollama", "huggingface",
        "Docker", "WindowsApps", ".vscode", ".gradle", ".m2", ".nuget", "dist", "build"]


def _scope(text):
    """Drives to search when the owner says so: "whole drive / all drives / everywhere / on my PC", "D drive"."""
    if re.search(r"\b(?:all\s+(?:my\s+)?drives|whole\s+(?:pc|computer)|entire\s+(?:pc|computer)|everywhere|anywhere)\b", text, I):
        import psutil
        return [p.mountpoint for p in psutil.disk_partitions() if "fixed" in p.opts or p.fstype in ("NTFS", "exFAT")]
    m = re.search(r"\b(?:(?:the\s+|my\s+)?(?P<d>[a-z])\s*(?::|drive\b)|(?:whole|entire)\s+(?:the\s+)?drive\s+(?P<d2>[a-z])\b)", text, I)
    if m and re.search(r"\bdrive\b|[a-z]:\s*$|[a-z]:\s", text, I):
        d = (m["d"] or m["d2"]).upper() + ":\\"
        return [d] if os.path.isdir(d) else []
    return []


def _rg(word, roots, session, limit=25):
    """Whole-drive content search: ripgrep (data/bin/rg.exe) when present - seconds instead of minutes."""
    trace(session, "grep", f"searching {', '.join(roots)} for '{word}' - a whole drive takes a few minutes", status="start")
    if os.path.isfile(RG):
        cmd = [RG, "--files-with-matches", "--ignore-case", "--fixed-strings", "--max-filesize", "5M", "--no-messages",
               "--hidden", "--no-ignore"] + [a for d in SKIP for a in ("-g", f"!{d}")] + [word] + roots
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        hits = []
        for line in p.stdout:
            hits.append(line.strip())
            if len(hits) >= limit:
                p.kill()
                break
        p.wait()
    else:  # ponytail: plain walk of text files; install data/bin/rg.exe for speed
        hits = []
        for root in roots:
            for r, dirs, names in os.walk(root):
                dirs[:] = [d for d in dirs if d not in SKIP]
                hits += [os.path.join(r, n) for n in names if os.path.splitext(n)[1].lower() in TEXT_EXT
                         and _contains(os.path.join(r, n), word)]
                if len(hits) >= limit:
                    break
    trace(session, "grep", f"{len(hits)} files", status="done")
    if not hits:
        return f"No file on {', '.join(roots)} contains '{word}'."
    return f"Files on {', '.join(roots)} containing '{word}'" + (f" (first {limit})" if len(hits) >= limit else "") + ":\n" + \
        "\n".join(f"- {h}" for h in hits[:limit])


def _contains(path, word):
    try:
        if os.path.getsize(path) > 5 << 20:
            return False
        with open(path, encoding="utf-8", errors="ignore") as f:
            return word.lower() in f.read().lower()
    except OSError:
        return False


# ---------------------------------------------------------------- backup / sync
def robocopy(src, dst, mirror=False):
    """(ok, summary). robocopy exit codes below 8 mean success (1 = files copied, 0 = nothing new)."""
    args = ["robocopy", src, dst, "/MIR" if mirror else "/E", "/XO", "/R:1", "/W:1", "/NP", "/NFL", "/NDL", "/MT:8"]
    p = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), timeout=7200)
    stats = re.search(r"Files\s*:\s*(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)", p.stdout)
    total, copied, _, _, failed, extra = map(int, stats.groups()) if stats else (0, 0, 0, 0, 0, 0)
    ok = p.returncode < 8
    return ok, (f"{copied} copied, {total - copied - failed} already up to date" + (f", {failed} failed" if failed else "")
                + (f", {extra} removed from the copy" if mirror and extra else ""))


def op_backup_project(m, text, session):
    """"Back up my whole project": resolve the project by name, then confirm scope + destination
    before cloning it (checklist #240). Nothing is copied until the owner says yes."""
    from ishti.skills.dev.skill import project
    name, path = project(text)
    if not os.path.isdir(path):
        return f"I couldn't find a project folder for '{name}' - give me its path and I'll back it up."
    dst = os.path.join(os.path.expanduser("~"), "Backups", name)
    size = sum(os.path.getsize(os.path.join(r, n)) for r, _, ns in os.walk(path) for n in ns) / 1e9

    def go():
        os.makedirs(dst, exist_ok=True)
        trace(session, "robocopy", f"backing up {name}", status="start")
        ok, summary = robocopy(path, dst, False)
        trace(session, "robocopy", summary, status="done" if ok else "error")
        return (f"Backed up {name} ({path}) to {dst}. {summary}." if ok else f"Backup failed: {summary}.")
    return ask_first(session, "fileops",
                     f"Back up {name} ({path}, {size:.1f} GB) to {dst} (new and changed files; nothing deleted)", go)


def op_backup(m, text, session):
    src, dst, act = m["src"].strip().rstrip("\\/"), m["dst"].strip(), m["act"].lower()
    if not os.path.isdir(src):
        return f"{src} isn't a folder."
    mirror = act in ("sync", "mirror")
    if not mirror:  # a backup keeps its own folder inside the destination: E:\Backups\Projects
        dst = os.path.join(dst, os.path.basename(src))
    if os.path.abspath(dst).lower().startswith(os.path.abspath(src).lower()):
        return "The destination can't be inside the folder itself."
    size = sum(os.path.getsize(os.path.join(r, n)) for r, _, ns in os.walk(src) for n in ns
               if os.path.isfile(os.path.join(r, n))) / 1e9
    question = (f"Mirror {src} to {dst} ({size:.1f} GB) - files in {dst} that aren't in {src} will be DELETED"
                if mirror else f"Back up {src} ({size:.1f} GB) to {dst} (new and changed files; nothing is deleted)")

    def go():
        trace(session, "robocopy", f"{'mirroring' if mirror else 'backing up'} {src}", status="start")
        ok, summary = robocopy(src, dst, mirror)
        trace(session, "robocopy", summary, status="done" if ok else "error")
        return f"{'Synced' if mirror else 'Backed up'} {src} -> {dst}: {summary}." if ok else f"Robocopy failed: {summary}."
    return ask_first(session, "fileops", question, go)


# ---------------------------------------------------------------- delete
def op_delete(m, text, session):
    path = m.group("path").strip().strip("\"'")
    if not os.path.exists(path):
        return f"{path} doesn't exist."
    from send2trash import send2trash
    return ask_first(session, "fileops", f"Move {path} to the Recycle Bin",
                     lambda: (send2trash(path), f"Moved {os.path.basename(path)} to the Recycle Bin.")[1])


def op_delete_folder(m, text, session):
    """"Delete my old project folder" / "delete the downloads folder": resolve the folder by name, then
    the standard T2 gate. Never guesses: an unresolved name asks for the exact path."""
    from ishti.skills.folders.skill import folder_in
    _, path = folder_in(text)
    if not path or not os.path.isdir(path):
        return ("I couldn't find that folder on this PC. Tell me the exact path (for example "
                "\"delete D:\\old_project\") and I'll show you what's inside before removing it.")
    inside = len(os.listdir(path))
    from send2trash import send2trash
    return ask_first(session, "fileops",
                     f"Move the folder {path} ({inside} item(s) inside) to the Recycle Bin",
                     lambda: (send2trash(path), f"Moved {os.path.basename(path)} ({inside} item(s)) to the Recycle Bin.")[1])


def op_delete_referenced(m, text, session):
    from ishti.reference_resolver import resolve_reference
    ref = resolve_reference(text, session)
    files = (ref.get("files") if ref else []) or []
    if not files:
        return "Which files do you want to delete? Tell me their names or paths."

    file_lines = []
    file_paths = []
    for f in files:
        path = f.get("path")
        name = f.get("name") or (os.path.basename(path) if path else "")
        size = f.get("size", "")
        size_str = f" ({size})" if size else ""
        file_lines.append(f"• {name}{size_str}")
        if path:
            file_paths.append(path)

    summary = "\n".join(file_lines)

    def do_delete():
        from send2trash import send2trash
        deleted, failed = [], []
        for p in file_paths:
            try:
                if os.path.exists(p):
                    send2trash(p)
                    deleted.append(os.path.basename(p))
            except Exception as _exc:
                swallowed('fileops:op_delete_referenced', _exc)
                failed.append(os.path.basename(p))
        if failed:
            return f"Moved {len(deleted)} file(s) to the Recycle Bin: {', '.join(deleted)}. {len(failed)} could not be moved: {', '.join(failed)}."
        return f"Moved {len(deleted)} file(s) to the Recycle Bin:\n" + "\n".join(f"• {fn}" for fn in deleted)

    return ask_first(session, "fileops", f"Move these {len(files)} files to the Recycle Bin:\n\n{summary}", do_delete)


def op_delete_ambiguous(m, text, session):
    """"Delete that file" / "remove this file" - check reference resolver first."""
    from ishti.reference_resolver import resolve_reference
    ref = resolve_reference(text, session)
    if ref and ref.get("files"):
        return op_delete_referenced(m, text, session)
    return ("Which file? Tell me the name or path (for example 'delete C:\\Users\\me\\doc.txt' or "
            "'delete the report file'). If you mean a recent file, say what it's called.")


def op_copy(m, text, session):
    """"Copy my resume to the JQ AI client folder": resolve source + destination, confirm both, then copy."""
    from ishti.skills.folders.skill import folder_in
    parts = re.split(r"\s+to\s+", text, maxsplit=1, flags=re.I)
    if len(parts) != 2:
        return "Say it as \"copy <file> to <folder>\" and I'll do it."
    src_text, dst_text = parts[0], parts[1]
    src_text = re.sub(r"^\s*(?:please\s+)?copy\s+", "", src_text, flags=I).strip(" \"'")
    dst_name, dst = folder_in(dst_text)
    src_path = None
    if os.path.exists(src_text):
        src_path = src_text
    else:
        from desktop.computer_use import get_computer_use_agent
        hits = get_computer_use_agent().search_pc(src_text, max_results=3)
        if len(hits) == 1:
            src_path = hits[0]["path"]
        elif hits:
            return ("I found several files matching that: "
                    + ", ".join(h["path"] for h in hits[:3]) + ". Which one?")
    if not src_path or not os.path.exists(src_path):
        return f"I couldn't find '{src_text}' in your user folders. Give me the exact file name or path."
    if not dst or not os.path.isdir(dst):
        return (f"I couldn't find the destination folder '{dst_text.strip()}'. "
                "Tell me the exact folder or path and I'll copy it there.")
    import shutil
    dst_file = os.path.join(dst, os.path.basename(src_path))
    return ask_first(session, "fileops", f"Copy {src_path} to {dst}",
                     lambda: (shutil.copy2(src_path, dst_file), f"Copied {os.path.basename(src_path)} to {dst}.")[1])


def op_move(m, text, session):
    """"Move X to Y": the same resolution as copy, then a T2 gate (a move can lose the original)."""
    from ishti.skills.folders.skill import folder_in
    parts = re.split(r"\s+to\s+", text, maxsplit=1, flags=re.I)
    if len(parts) != 2:
        return "Say it as \"move <file> to <folder>\" and I'll do it."
    src_text, dst_text = parts[0], parts[1]
    src_text = re.sub(r"^\s*(?:please\s+)?(?:move|cut|relocate)\s+", "", src_text, flags=I).strip(" \"'")
    _, dst = folder_in(dst_text)
    src_path = src_text if os.path.exists(src_text) else None
    if not src_path:
        from desktop.computer_use import get_computer_use_agent
        hits = get_computer_use_agent().search_pc(src_text, max_results=3)
        src_path = hits[0]["path"] if len(hits) == 1 else None
        if hits and len(hits) > 1:
            return "I found several files matching that: " + ", ".join(h["path"] for h in hits[:3]) + ". Which one?"
    if not src_path or not os.path.exists(src_path):
        return f"I couldn't find '{src_text}' in your user folders. Give me the exact file name or path."
    if not dst or not os.path.isdir(dst):
        return f"I couldn't find the destination folder '{dst_text.strip()}'. Tell me the exact folder or path."
    import shutil
    dst_file = os.path.join(dst, os.path.basename(src_path))
    return ask_first(session, "fileops", f"Move {src_path} to {dst}",
                     lambda: (shutil.move(src_path, dst_file), f"Moved {os.path.basename(src_path)} to {dst}.")[1])


def op_empty_bin(m, text, session):
    def empty():
        p = subprocess.run(["powershell", "-NoProfile", "-Command", "Clear-RecycleBin -Force -ErrorAction SilentlyContinue"],
                           capture_output=True, text=True, timeout=120, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return "Recycle Bin emptied." if p.returncode == 0 else f"Couldn't empty it: {p.stderr.strip()[:200]}"
    return ask_first(session, "fileops", "Permanently delete everything in the Recycle Bin (this can't be undone)", empty)


if __name__ == "__main__":
    for t, want in {"back up D:/Projects to E:/Backups": "backup", "sync D:\\Notes with E:\\Notes": "backup",
                    "search the whole drive D for files containing 'invoice'": "grep"}.items():
        assert next((n for n, rx in OPS if rx.search(t)), None) == want, t
    assert _scope("find files containing 'x' on the D drive") == ["D:\\"] and _scope("files containing x in my notes") == []
    import tempfile
    a, b = tempfile.mkdtemp(), tempfile.mkdtemp()
    os.makedirs(os.path.join(a, "sub"))
    for n, body in {"one.txt": "alpha", os.path.join("sub", "two.md"): "needle here"}.items():
        open(os.path.join(a, n), "w").write(body)
    assert robocopy(a, b) == (True, "2 copied, 0 already up to date") and robocopy(a, b)[1].startswith("0 copied")
    open(os.path.join(b, "extra.txt"), "w").write("x")
    assert robocopy(a, b, mirror=True)[1].endswith("1 removed from the copy") and not os.path.exists(os.path.join(b, "extra.txt"))
    assert any(h.endswith("two.md") for h in _rg("NEEDLE", [a], {}).splitlines())
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
