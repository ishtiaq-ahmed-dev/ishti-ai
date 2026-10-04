"""Folders skill: questions about a folder, and organizing one.
  "how many pdfs are in my downloads folder"  /  "largest files in desktop"  /  "what's in my documents"
  "arrange / organize / clean up my downloads folder"  -> preview, wait for yes, move, keep an undo log
  "undo that" / "undo the organize"                    -> move everything back
Folder names are typo-tolerant ("dwonload"); saved project locations and explicit paths work too.
Organizing only moves top-level files into type subfolders: nothing is overwritten or deleted,
partial downloads and files touched in the last 2 minutes are left alone."""
import datetime as dt
import difflib
import glob
import json
import os
import re
import shutil
import time

from ishti import ROOT
from ishti.skills import answer_of, trace

I = re.I
TYPES = {"PDFs": {"pdf"}, "Documents": {"doc", "docx", "txt", "md", "rtf", "odt"},
         "Spreadsheets": {"xls", "xlsx", "csv", "ods"}, "Presentations": {"ppt", "pptx", "odp"},
         "Images": {"jpg", "jpeg", "png", "gif", "webp", "bmp", "svg", "heic", "ico", "tif", "tiff"},
         "Videos": {"mp4", "mov", "mkv", "avi", "webm", "wmv", "flv", "m4v"},
         "Audio": {"mp3", "wav", "m4a", "flac", "ogg", "aac", "opus"},
         "Archives": {"zip", "rar", "7z", "tar", "gz", "bz2", "xz", "iso"},
         "Installers": {"exe", "msi", "msix", "appx", "apk"},
         "Code": {"py", "js", "ts", "html", "css", "json", "ipynb", "java", "c", "cpp", "sql", "sh", "bat"}}
WORDS = {"pdf": "PDFs", "document": "Documents", "doc": "Documents", "spreadsheet": "Spreadsheets",
         "excel": "Spreadsheets", "sheet": "Spreadsheets", "presentation": "Presentations", "slide": "Presentations",
         "image": "Images", "photo": "Images", "picture": "Images", "screenshot": "Images", "video": "Videos",
         "movie": "Videos", "audio": "Audio", "song": "Audio", "music": "Audio", "archive": "Archives",
         "zip": "Archives", "installer": "Installers", "setup": "Installers", "exe": "Installers", "code": "Code"}
SKIP = {".crdownload", ".part", ".partial", ".tmp", ".download", ".opdownload"}
ORGANIZE = re.compile(r"\b(?:arrange|(?:re)?organi[sz]e|clean\s*up|tidy(?:\s+up)?|sort(?:\s+out)?)\b", I)
SMART = re.compile(r"\b(?:by|using|with)\s+(?:the\s+|their\s+)?(?:file\s+)?(?:names?|contents?|topics?|subjects?)\b|"
                   r"\b(?:contents?|properly|related|topics?|smart|reorgani[sz]e|audios?)\b", I)
UNDO = re.compile(r"^undo\b(?:.*\b(?:organi|arrang|clean|sort|that|last|it)\w*)?\W*$", I)
ASK = re.compile(r"\b(?:how\s+many|count|list|show|what(?:'s|\s+is|\s+are)|largest|biggest|newest|latest|"
                 r"oldest|recent)\b", I)
VIDEO_EDIT = re.compile(r"\b(?:edit|trim|cut|compress|caption|subtitle|overlay|title|reframe)\b.*\bvideos?\b|"
                        r"\bvideos?\b.*\b(?:edit|trim|cut|compress|caption|subtitle|overlay|reframe)\b", I)
UNDO_DIR = os.path.join(ROOT, "data", "undo")


VERBS = ("open", "delete", "remove", "copy", "move", "list", "show", "what", "whats", "what's", "which", "how",
         "count", "find", "search", "locate", "organize", "arrange", "clean", "tidy", "sort", "check", "trash",
         "get", "rid", "is", "in", "on", "from", "the", "my", "a", "an", "and", "also", "please", "me", "for",
         "old", "new", "there", "here", "that", "this", "it", "s")


def folder_name_candidate(text, known=None):
    """The folder name a request is about, without resolving to a path.

    Returns the candidate name (lowercase) or None. Handles the checklist phrasings:
    "What's in my Projects folder?" -> 'projects', "delete the old_project folder" -> 'old_project',
    "open my JQ AI client folder" -> 'jq ai client'. Leading verbs/stop-words are skipped so
    "open my project" yields 'project', never 'open my'.
    """
    if known is None:
        known = known_folders()
    t = text.lower()
    # 1) explicit "X folder" / "X directory" / "X dir" - take everything between "my"/start and the keyword
    m = re.search(r"\b(?:my\s+|the\s+)?([a-z][a-z0-9_\-]*(?:\s+[a-z][a-z0-9_\-]*)*)\s+(?:folder|directory|dir)\b", t)
    if m:
        words = [w for w in m.group(1).split() if w not in VERBS]
        name = " ".join(words).strip()
        if name and name not in known:
            return name
        if name in known:
            return name  # "downloads folder" -> downloads (folder_in resolves the path)
    # 2) bare "my projects" / "my project" / "old_project" (no trailing 'folder')
    m2 = re.search(r"\bmy\s+([a-z][a-z0-9_\-]*(?:\s+[a-z][a-z0-9_\-]*)?)\b", t)
    if m2:
        words = [w for w in m2.group(1).split() if w not in VERBS]
        name = " ".join(words).strip()
        if name:
            return name
    # 3) a bare token that is already a known folder name ("list downloads", "organize pictures")
    for tok in re.findall(r"[a-z]+", t):
        if tok in known:
            return tok
    return None


def known_folders():
    names = {"downloads": "{374DE290-123F-4565-9164-39C4925E467B}", "desktop": "Desktop", "documents": "Personal",
             "pictures": "My Pictures", "videos": "My Video", "music": "My Music"}
    out = {k: os.path.join(os.path.expanduser("~"), k.capitalize()) for k in names}
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders") as key:
            for k, v in names.items():
                out[k] = os.path.expandvars(winreg.QueryValueEx(key, v)[0])  # honours redirected folders
    except OSError:
        pass
    return out


def folder_in(text):
    """(name, path) of the folder the text talks about, or (None, None)."""
    from ishti.skills.memory.skill import extract_path
    p = extract_path(text)
    if p and os.path.isdir(p):
        return os.path.basename(p.rstrip("\\/")) or p, p
    known = known_folders()
    tokens = re.findall(r"[a-z]+", text.lower())
    for tok in tokens:  # "dwonload" -> downloads; "pictures" as a folder only when followed by "folder"
        hit = difflib.get_close_matches(tok if tok.endswith("s") else tok + "s", known, n=1, cutoff=0.75)
        if hit and not (hit[0] in ("pictures", "videos", "music", "documents")
                        and not re.search(rf"\b{tok}\s+(?:folder|directory|dir)\b|\b(?:in|on|from)\s+(?:my\s+|the\s+)?{tok}\b", text, I)):
            return hit[0].capitalize(), known[hit[0]]
    from ishti.facts import get_facts
    for entity, attr, value in get_facts().mentioned_in(text):
        if attr == "location" and os.path.isdir(value):
            return entity, value
    # "Projects folder", "old_project folder", "my project": an explicit folder name without a known
    # special-folder mapping is still a real folder request. Treat project-like names as folder candidates so
    # "what's in my Projects folder", "open my project" and "old_project folder" resolve instead of
    # falling to the chat brain. "projects" (plural) is very likely a real user folder on this machine.
    cand = folder_name_candidate(text, known=known_folders())
    if cand:
        for base in (cand, cand.capitalize(), cand + "s"):
            cand_path = os.path.join(os.path.expanduser("~"), base)
            if os.path.isdir(cand_path):
                return base, cand_path
        # Common project-ish folder names the owner uses: fall back to workspace roots so "my project" /
        # "Projects folder" resolve on a machine that keeps them outside the profile.
        for root in (os.path.expanduser("~"), os.path.join(os.path.expanduser("~"), "Desktop"),
                     os.path.join(os.path.expanduser("~"), "Documents"),
                     os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..")):
            for base in (cand, cand.capitalize(), cand + "s"):
                cand_path = os.path.join(root, base)
                if os.path.isdir(cand_path):
                    return base, cand_path
    return None, None


def kind_in(text):
    for tok in re.findall(r"[a-z]+", text.lower()):
        base = tok[:-1] if tok.endswith("s") else tok
        if base in WORDS and not (base in ("picture", "video", "music", "document") and re.search(
                rf"\b{tok}\s+(?:folder|directory)\b", text, I)):
            return WORDS[base]
    m = re.search(r"\.([a-z0-9]{2,5})\b", text, I)
    return m and next((k for k, exts in TYPES.items() if m.group(1).lower() in exts), None)


def kind_of(name):
    ext = os.path.splitext(name)[1].lower().lstrip(".")
    return next((k for k, exts in TYPES.items() if ext in exts), "Other")


def junk(name):
    """Office lock files, thumbnails and desktop.ini are not the owner's files."""
    n = name.lower()
    return n.startswith("~$") or n in ("desktop.ini", "thumbs.db", ".ds_store")


def top_files(path):
    with os.scandir(path) as it:
        return [e for e in it if e.is_file(follow_symlinks=False) and not junk(e.name)]


def all_files(path, depth=2):
    """Files in the folder and its subfolders (two levels covers the type folders organizing creates)."""
    out, stack = [], [(path, 0)]
    while stack:
        d, lvl = stack.pop()
        try:
            with os.scandir(d) as it:
                for e in it:
                    if e.is_dir(follow_symlinks=False) and lvl < depth:
                        stack.append((e.path, lvl + 1))
                    elif e.is_file(follow_symlinks=False) and not junk(e.name):
                        out.append(e)
        except OSError:
            continue
    return out


def size(n):
    return f"{n / 1e9:.1f} GB" if n >= 1e9 else f"{n / 1e6:.1f} MB" if n >= 1e6 else f"{n / 1e3:.0f} KB"


def match(text):
    t = text.strip()
    if UNDO.match(t):
        return True
    # "Deploy my latest commit" / "push my latest changes" / "revert the last commit": a git/deploy
    # action, not a folder question. ASK fires on "latest"/"last" and folder_name_candidate reads the
    # rest as a folder name, so folders used to claim these (checklist #164).
    if re.search(r"\b(?:deploy|redeploy|release|ship|push|merge|revert|rollback|roll\s+back)\b", t, I) \
            and re.search(r"\b(?:commit|branch|repo|repository|changes?|code|build|deployment|production|staging)\b", t, I):
        return False
    # "Play my latest Obscura video" / "total runtime of all videos in my folder" (checklist #171, #175): a
    # video task. ASK fires on "latest"/"total", but only a real folder keeps it ours.
    if re.search(r"\bvideos?\b|\bclips?\b", t, I) \
            and re.search(r"\b(?:play|watch|runtime|total\s+duration|how\s+long|duration)\b", t, I) \
            and not folder_in(t)[1]:
        return False
    # "Show me everything you know about my family" / "what do you know about X" (checklist #241): a memory
    # recall, not a folder listing. "my family" was being read as a folder name.
    if re.search(r"\b(?:everything|what)\s+(?:you\s+)?know(?:s)?\s+about\b|\bshow\s+me\s+everything\s+about\b|"
                 r"\bwhat\s+do\s+you\s+know\s+about\b|\bdo\s+you\s+remember\b", t, I):
        return False
    if not ORGANIZE.search(t) and VIDEO_EDIT.search(t):  # "edit the latest video in downloads" is the video skill's
        return False
    # "Pull the latest news on local LLMs, pick the most relevant one, and draft a short note" is research +
    # writing, not a folder question (checklist #66). The word "latest" trips ASK, and "channel"/"note" can
    # look like folder names - so folder skills yield to research/drafting when the text is clearly about news,
    # research, drafting or content creation rather than a real folder on disk.
    if re.search(r"\b(?:news|headline|research|investigate|research|draft|write\s+(?:a\|an\|me\s+)?(?:a\|an\s+)?(?:post|article|note|report|essay|email|message|letter)|summarize|pull\s+the\s+latest|pick\s+(?:the\s+)?most\s+relevant|channel)\b", t, I):
        return False
    # "What's in my Projects folder?", "Open my Downloads folder", "list my project folder", "how many pdfs in
    # downloads": a folder question/action. Either the folder resolves to a real path (folder_in) or it is an
    # explicit folder name (folder_name_candidate) - run() answers honestly when the name has no real folder.
    if ASK.search(t) or ORGANIZE.search(t):
        return bool(folder_in(t)[1] or folder_name_candidate(t))
    return False


def plan_moves(path, now=None):
    now, moves = now or time.time(), []
    for e in top_files(path):
        ext = os.path.splitext(e.name)[1].lower()
        if ext in SKIP or e.name.lower() in ("desktop.ini", "thumbs.db") or e.name.startswith("~$") \
                or now - e.stat().st_mtime < 120:
            continue  # downloading, system, lock file, or just touched
        moves.append((e.path, os.path.join(path, kind_of(e.name), e.name)))
    return moves


def free_name(dst):
    base, ext, n = *os.path.splitext(dst), 1
    while os.path.exists(dst):
        dst, n = f"{base} ({n}){ext}", n + 1
    return dst


def do_moves(moves):
    done, failed = [], 0
    for src, dst in moves:
        try:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            final = free_name(dst)  # never overwrite
            shutil.move(src, final)
            done.append((src, final))
        except OSError:
            failed += 1  # in use / permission: leave it where it is
    os.makedirs(UNDO_DIR, exist_ok=True)
    log = os.path.join(UNDO_DIR, f"organize_{dt.datetime.now():%Y%m%d_%H%M%S}.json")
    with open(log, "w", encoding="utf-8") as f:
        json.dump(done, f, indent=1)
    return done, failed, log


def undo_last():
    logs = sorted(glob.glob(os.path.join(UNDO_DIR, "organize_*.json")))
    if not logs:
        return "Nothing to undo."
    with open(logs[-1], encoding="utf-8") as f:
        done = json.load(f)
    back = 0
    for src, dst in reversed(done):
        if os.path.exists(dst) and not os.path.exists(src):
            shutil.move(dst, src)
            back += 1
    for d in {os.path.dirname(dst) for _, dst in done}:
        if os.path.isdir(d) and not os.listdir(d):
            os.rmdir(d)
    os.replace(logs[-1], logs[-1] + ".undone")
    return f"Undone: moved {back} of {len(done)} files back."


def run(text, session):
    t = text.strip()
    p = session.get("pending")
    if p and p.get("skill") == "folders":
        session["pending"] = None
        answer = answer_of(t)
        if answer is None:
            return None if not match(t) else run(t, session)  # moved on: handle the new message normally
        if answer == "no":
            return {"reply": "OK, nothing moved.", "sources": []}
        trace(session, "move", f"moving {len(p['moves'])} files", status="start")
        done, failed, _ = do_moves(p["moves"])
        trace(session, "move", f"moved {len(done)} files", status="done", failed=failed)
        return {"reply": f"Moved {len(done)} files in {p['name']} into type folders"
                         + (f"; {failed} skipped (in use)" if failed else "") + ". Say 'undo that' to put them back.",
                "sources": []}
    if UNDO.match(t):
        return {"reply": undo_last(), "sources": []}
    name, path = folder_in(t)
    if not path:  # a named folder that doesn't exist: say so plainly, never invent contents
        cand = folder_name_candidate(t) or name or "that folder"
        return {"reply": f"I couldn't find a folder called '{cand}' in your profile folders (Downloads, Desktop, "
                         f"Documents, Pictures, Videos, Music). If it's somewhere else, give me the exact path.",
                "sources": []}
    scanned = top_files(path) if ORGANIZE.search(t) else all_files(path)
    kinds = {}
    for e in scanned:
        kinds[kind_of(e.name)] = kinds.get(kind_of(e.name), 0) + 1
    trace(session, "scan", f"open {name} ({len(scanned)} files)", folder=path, count=len(scanned),
          kinds=kinds, sample=[e.name for e in scanned[:12]])
    if ORGANIZE.search(t) and SMART.search(t):
        from ishti.skills.folders.smart import smart_plan
        moves, summary = smart_plan(path, trace=lambda n, label, **kw: trace(session, n, label, **kw))
        if not moves:
            return {"reply": f"{name} is already organized by content - nothing to move.", "sources": []}
        session["pending"] = {"skill": "folders", "moves": moves, "name": name}
        trace(session, "plan", f"plan {len(moves)} moves into {len(summary)} topic folders", status="waiting", groups=summary)
        top = ", ".join(f"{k} {v}" for k, v in sorted(summary.items(), key=lambda kv: -kv[1])[:12])
        return {"reply": f"Plan for {name}, by name and content: move {len(moves)} files - {top}. "
                         "Nothing is deleted or overwritten, and it can be undone. Go ahead? (yes/no)", "sources": []}
    if ORGANIZE.search(t):
        moves = plan_moves(path)
        if not moves:
            return {"reply": f"{name} has no loose files to organize.", "sources": []}
        counts = {}
        for _, dst in moves:
            k = os.path.basename(os.path.dirname(dst))
            counts[k] = counts.get(k, 0) + 1
        summary = ", ".join(f"{k} {v}" for k, v in sorted(counts.items(), key=lambda kv: -kv[1]))
        session["pending"] = {"skill": "folders", "moves": moves, "name": name}
        trace(session, "plan", f"plan {len(moves)} moves into {len(counts)} folders", status="waiting", groups=counts)
        return {"reply": f"Plan for {name} ({path}): move {len(moves)} files into subfolders - {summary}. "
                         "Nothing is deleted or overwritten, and it can be undone. Go ahead? (yes/no)", "sources": []}
    files, kind = all_files(path), kind_in(t)  # questions count subfolders too; organizing stays top-level
    if kind:
        files = [e for e in files if kind_of(e.name) == kind]
    label = kind or "files"
    if not files:
        return {"reply": f"No {label} in {name}.", "sources": []}
    total = sum(e.stat().st_size for e in files)
    from ishti.skills import md_table
    if re.search(r"\b(?:by\s+(?:type|kind|category)|types?\s+of\s+files|breakdown)\b", t, I):  # D1: counts per kind
        kinds = {}
        for e in files:
            n, b = kinds.get(kind_of(e.name) or "other", (0, 0))
            kinds[kind_of(e.name) or "other"] = (n + 1, b + e.stat().st_size)
        rows = [(k, n, size(b)) for k, (n, b) in sorted(kinds.items(), key=lambda kv: -kv[1][0])]
        return {"reply": f"{name}: {len(files)} files, {size(total)}.\n\n" + md_table(("Type", "Files", "Size"), rows),
                "sources": []}
    if re.search(r"\b(?:largest|biggest)\b", t, I):
        top, how = sorted(files, key=lambda e: -e.stat().st_size), "largest"
    else:
        top, how = sorted(files, key=lambda e: -e.stat().st_mtime), "newest"
    import datetime as _dt
    listing = md_table(("Name", "Size", "Modified"), [(e.name, size(e.stat().st_size),
                                                       f"{_dt.datetime.fromtimestamp(e.stat().st_mtime):%d %b %Y}") for e in top[:5]])
    return {"reply": f"{name}: {len(files)} {label}, {size(total)}. {how.capitalize()}:\n\n{listing}", "sources": []}


if __name__ == "__main__":
    import tempfile
    root = tempfile.mkdtemp()
    UNDO_DIR = tempfile.mkdtemp()  # module scope: the self-check never touches the real undo logs
    for n in ["a.pdf", "b.PDF", "c.jpg", "setup.exe", "notes.txt", "x.crdownload", "desktop.ini", "weird.xyz"]:
        with open(os.path.join(root, n), "w") as f:
            f.write("x" * 10)
    os.makedirs(os.path.join(root, "PDFs"))
    with open(os.path.join(root, "PDFs", "a.pdf"), "w") as f:
        f.write("existing")                                    # collision: must not be overwritten
    old = time.time() - 3600
    for e in top_files(root):
        os.utime(e.path, (old, old))
    assert folder_in(f"how many pdfs in {root}")[1] == root
    assert folder_in("how many pdfs are in my dwonload folder")[0] == "Downloads"
    assert kind_in("how many pdfs are in my downloads") == "PDFs" and kind_in("list photos in desktop") == "Images"
    assert kind_in("what's in my pictures folder") is None
    s = {}
    assert run(f"how many pdfs are in {root}", s)["reply"].startswith(f"{os.path.basename(root)}: 3 PDFs")  # 2 loose + 1 already in PDFs/ (subfolders count)
    r = run(f"organize the folder {root}", s)["reply"]
    assert "move 6 files" in r and s["pending"], r                 # .crdownload and desktop.ini untouched
    assert run("no", s)["reply"] == "OK, nothing moved." and os.path.exists(os.path.join(root, "a.pdf"))
    run(f"organize the folder {root}", s)
    assert run("tell me a joke", s) is None and not s["pending"], "an unrelated reply must not be swallowed"
    run(f"organize the folder {root}", s)
    assert "Moved 6 files" in run("yes", s)["reply"]
    assert open(os.path.join(root, "PDFs", "a.pdf")).read() == "existing"          # not overwritten
    assert os.path.exists(os.path.join(root, "PDFs", "a (1).pdf")) and os.path.exists(os.path.join(root, "x.crdownload"))
    assert "moved 6 of 6" in run("undo that", s)["reply"]
    assert sorted(os.listdir(root)) == sorted(["a.pdf", "b.PDF", "c.jpg", "setup.exe", "notes.txt", "x.crdownload",
                                               "desktop.ini", "weird.xyz", "PDFs"])
    print("[self-check] folders OK - typo-tolerant folder, count by type, organize preview/confirm, no overwrite, undo")
