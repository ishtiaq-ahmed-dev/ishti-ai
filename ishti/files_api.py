"""File endpoints for the chat UI: attach a file to a message, and show an exported report in Explorer.

    POST /api/upload   multipart form, field "file"  ->  {ok, path, name, size, kind, mention}
    POST /api/reveal   JSON {"path": "<file or folder>"}  ->  {ok, revealed, launched}

Contract the UI relies on (N9 chat attachments, CD-13 "open in folder" button):
  * upload saves into data/uploads/ under a safe unique name and returns the ABSOLUTE path. The UI puts `mention`
    (e.g. "summarize C:\\...\\data\\uploads\\20261002-101500-report.pdf") into the message, so every document, image and
    OCR skill that already takes a path works unchanged. 50 MB cap; executable/script types are refused; files older
    than 30 days are pruned on the next upload.
  * reveal opens Explorer with the file selected (a folder is just opened). Only paths inside ISHTI's reports/ and
    data/ folders, or the owner's Documents/ISHTI, Downloads and Desktop, are allowed: it must never become a way to
    browse or launch arbitrary things. It is a launch action, so it is refused for another website's request and
    for tunnelled/remote callers, and it only records an audit row (no launch) under ISHTI_TEST_MODE=1.
Errors are JSON {"error": "<plain words>"} with 400 (bad input), 403 (not allowed), 404 (no such file), 413 (too big).

attach(app) is called once from interface/web_ui/server.py:   from ishti import files_api; files_api.attach(app)
"""
import os
import re
import zipfile
import subprocess
import sys
import time
import asyncio
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import APIRouter, Request, UploadFile
from fastapi.responses import JSONResponse

from ishti import ROOT, audit, test_mode
from ishti.core.logutil import swallowed

router = APIRouter()
_SCAN_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="upload-scan")
UPLOAD_DIR = test_mode.scratch_path(os.path.join(ROOT, "data", "uploads"))
MAX_UPLOAD_BYTES = 50 * 1024 * 1024
KEEP_DAYS = 30
KINDS = {
    "document": {".pdf", ".docx", ".doc", ".xlsx", ".xls", ".csv", ".pptx", ".txt", ".md", ".json", ".rtf", ".odt"},
    "image": {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff", ".heic"},
    "code": {".html", ".htm", ".css", ".js", ".jsx", ".ts", ".tsx", ".py", ".xml", ".yaml", ".yml", ".toml", ".ini", ".log",
             ".sql", ".sh", ".java", ".c", ".cpp", ".h", ".go", ".rs", ".php", ".rb", ".vue", ".scss"},  # text only; never executed. apk/exe/jar stay unlisted = rejected
    "audio": {".mp3", ".wav", ".m4a", ".ogg", ".flac", ".opus", ".aac"},
    "video": {".mp4", ".mkv", ".mov", ".avi", ".webm"},
}
ALLOWED_EXT = set().union(*KINDS.values())
LOOPBACK = {"127.0.0.1", "localhost", "::1"}
from ishti.web_security import PROXY_HEADERS as _PROXY_HEADERS
PROXY_HEADERS = tuple(h.encode() for h in _PROXY_HEADERS)


def _err(msg, code):
    return JSONResponse({"error": msg}, status_code=code)


def kind_of(name):
    ext = os.path.splitext(name)[1].lower()
    return next((k for k, exts in KINDS.items() if ext in exts), "")


def safe_name(raw):
    """A file name that cannot escape the folder: base name only, plain characters, bounded length, original extension."""
    base = os.path.basename(str(raw or "").replace("\\", "/")).strip()
    stem, ext = os.path.splitext(base)
    stem = re.sub(r"[^\w .()-]", "_", stem, flags=re.UNICODE).strip(" .") or "file"
    return stem[:80] + ext.lower()[:10]


def _valid_upload_content(ext, sample, path):
    """Cheap signature checks before a file is handed to a parser/model. Never extract archives."""
    signatures = {
        ".pdf": (b"%PDF-",), ".png": (b"\x89PNG\r\n\x1a\n",),
        ".jpg": (b"\xff\xd8\xff",), ".jpeg": (b"\xff\xd8\xff",),
        ".gif": (b"GIF87a", b"GIF89a"), ".webp": (b"RIFF",), ".bmp": (b"BM",),
        ".tif": (b"II*\x00", b"MM\x00*"), ".tiff": (b"II*\x00", b"MM\x00*"),
        ".mp3": (b"ID3", b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"),
        ".wav": (b"RIFF",), ".ogg": (b"OggS",), ".opus": (b"OggS",),
        ".flac": (b"fLaC",), ".aac": (b"\xff\xf1", b"\xff\xf9"),
        ".mkv": (b"\x1a\x45\xdf\xa3",), ".webm": (b"\x1a\x45\xdf\xa3",),
        ".avi": (b"RIFF",), ".doc": (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1",),
        ".xls": (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1",),
    }
    if ext in (".docx", ".xlsx", ".pptx", ".odt"):
        try:
            with zipfile.ZipFile(path) as archive:
                members = archive.infolist()
                if len(members) > 2000 or sum(x.file_size for x in members) > 200 * 1024 * 1024:
                    return False
                for item in members:
                    if item.flag_bits & 1 or item.file_size > 100 * 1024 * 1024:
                        return False
                    if item.file_size and (not item.compress_size or item.file_size / item.compress_size > 1000):
                        return False
                names = {item.filename for item in members}
                if len(names) != len(members):
                    return False
                required = {".docx": "word/document.xml", ".xlsx": "xl/workbook.xml",
                            ".pptx": "ppt/presentation.xml", ".odt": "mimetype"}[ext]
                safe_members = all(not n.startswith(("/", "\\")) and ".." not in n.replace("\\", "/").split("/")
                                   for n in names)
                no_macros = not any(n.lower().endswith("vbaproject.bin") for n in names)
                return required in names and safe_members and no_macros
        except (OSError, zipfile.BadZipFile, ValueError):
            return False
    if ext in {".heic", ".m4a", ".mp4", ".mov"}:
        # ISO-BMFF files use a `ftyp` box at offset 4. A generic zero prefix is
        # not enough: it accepts arbitrary data (including corrupt/hostile files).
        if len(sample) < 16 or sample[4:8] != b"ftyp":
            return False
        major = sample[8:12]
        compatible = {sample[i:i + 4] for i in range(16, min(len(sample) - 3, 64), 4)}
        brands = {
            ".heic": {b"heic", b"heix", b"hevc", b"hevx", b"mif1", b"msf1"},
            ".m4a": {b"M4A ", b"M4B ", b"M4P ", b"isom", b"iso2", b"mp42"},
            ".mp4": {b"isom", b"iso2", b"iso3", b"mp41", b"mp42", b"avc1", b"M4V ", b"dash", b"MSNV"},
            ".mov": {b"qt  "},
        }[ext]
        return major in brands or bool(compatible & brands)
    allowed = signatures.get(ext)
    if allowed:
        if not any(sample.startswith(sig) for sig in allowed):
            return False
        if ext == ".webp" and sample[8:12] != b"WEBP":
            return False
        if ext == ".wav" and sample[8:12] != b"WAVE":
            return False
        if ext == ".avi" and sample[8:12] != b"AVI ":
            return False
        return True
    if ext == ".rtf":
        return sample.lstrip().startswith(b"{\\rtf")
    if ext in {".txt", ".md", ".csv", ".json"} | KINDS["code"]:
        if b"\x00" in sample:
            return False
        try:
            sample.decode("utf-8-sig")
            return True
        except UnicodeDecodeError:
            return False
    return False


def prune(days=KEEP_DAYS, folder=None):
    """Delete uploads older than `days`; returns how many were removed. Never raises."""
    folder, gone, cutoff = folder or UPLOAD_DIR, 0, time.time() - days * 86400
    try:
        for n in os.listdir(folder):
            p = os.path.join(folder, n)
            if os.path.isfile(p) and os.path.getmtime(p) < cutoff:
                os.remove(p)
                gone += 1
    except OSError as exc:
        swallowed("ishti/files_api.py:prune", exc)
    return gone


_RETENTION = {"last": 0.0}


def maybe_retention(interval_s: float = 86400) -> dict:
    """Daily clean-up of what is temporary by design (prune() and purge_trash() existed but nothing ever called them):
    uploads older than KEEP_DAYS, channel voice-note leftovers older than 7 days, deleted chats past their undo window.
    Real history (chats, memories, notes, ledger) is never touched here."""
    if time.time() - _RETENTION["last"] < interval_s:
        return {}
    _RETENTION["last"] = time.time()
    out = {"uploads": prune(KEEP_DAYS)}
    try:
        from ishti.channels import media
        if os.path.isdir(media.UPLOAD_DIR):
            out["remote_uploads"] = prune(7, media.UPLOAD_DIR)
    except Exception as exc:
        swallowed("ishti/files_api.py:maybe_retention.remote", exc)
    try:
        from orchestrator.memory.conversation import get_conversation_manager
        out["chat_trash"] = get_conversation_manager().purge_trash(max_age_s=86400)
    except Exception as exc:
        swallowed("ishti/files_api.py:maybe_retention.trash", exc)
    return out


async def save_upload(file: UploadFile, folder=None):
    """Stream `file` into the uploads folder -> (info, None) or (None, (message, status)). Partial files never stay."""
    folder = folder or UPLOAD_DIR
    name = safe_name(file.filename)
    ext = os.path.splitext(name)[1]
    if ext not in ALLOWED_EXT:
        return None, (f"I can't take a {ext or 'extensionless'} file. Documents, code, images, audio and video are fine.", 400)
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex}-{name}")
    size = 0
    try:
        with open(path, "xb") as out:
            while True:
                chunk = await file.read(1 << 20)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise OverflowError
                out.write(chunk)
    except asyncio.CancelledError:
        Path(path).unlink(missing_ok=True)
        raise
    except OverflowError:
        os.remove(path)
        return None, (f"That file is bigger than {MAX_UPLOAD_BYTES // 2**20} MB, which is the most I take.", 413)
    except OSError as exc:
        if os.path.exists(path):
            os.remove(path)
        return None, (f"I couldn't save that file ({exc.strerror or exc}).", 500)
    except Exception:
        swallowed("ishti/files_api.py:read_upload_failed")
        Path(path).unlink(missing_ok=True)
        return None, ("The upload could not finish. The staged file was removed.", 503)
    if size == 0:
        os.remove(path)
        return None, ("That file is empty.", 400)
    try:
        with open(path, "rb") as uploaded:
            sample = uploaded.read(8192)
        if not _valid_upload_content(ext, sample, path):
            os.remove(path)
            return None, (f"That file's contents don't match its .{ext.lstrip('.')} extension, or the file isn't a supported format.", 400)
    except OSError as exc:
        if os.path.exists(path):
            os.remove(path)
        return None, (f"I couldn't validate that file ({exc.strerror or exc}).", 500)
    try:
        from ishti.security.upload_scanner import scan_upload
        scan_res = await asyncio.get_running_loop().run_in_executor(_SCAN_EXECUTOR, scan_upload, path)
        if not scan_res.get("safe", False):
            if os.path.exists(path):
                os.remove(path)
            return None, (f"Security scan rejected file: {scan_res.get('reason', 'Malware or unsafe file structure detected')}", 400)
    except asyncio.CancelledError:
        Path(path).unlink(missing_ok=True)
        raise
    except Exception:
        swallowed("ishti/files_api.py:scan_upload_failed")
        Path(path).unlink(missing_ok=True)
        return None, ("The security scan could not complete. The staged file was rejected.", 503)
    kind = kind_of(name)
    verb = {"document": "summarize", "image": "describe the image", "code": "read and review",
            "audio": "transcribe", "video": "describe"}[kind]
    action = {
        "document": "summarize the document",
        "image": "describe the image",
        "code": "read and review the code as text (never execute it)",
        "audio": "transcribe the audio",
        "video": "describe the video",
    }[kind]
    return {"ok": True, "path": path, "name": os.path.basename(path), "size": size, "kind": kind,
            "action": action, "mention": f"{verb} {path}"}, None


@router.post("/api/upload")
async def upload(req: Request, file: UploadFile):
    if _not_direct(req.scope):
        return _err("Uploads are only accepted from this PC's own window.", 403)
    await asyncio.to_thread(prune)
    info, problem = await save_upload(file)
    if problem:
        return _err(*problem)
    audit.record("file uploaded", f"{info['name']} ({info['size']} bytes)", initiator="owner", skill="files_api")
    return info


# ---------------------------------------------------------------- reveal
def allowed_roots():
    home = os.path.expanduser("~")
    return [os.path.join(ROOT, "reports"), os.path.join(ROOT, "data"), os.path.join(home, "Documents", "ISHTI"),
            os.path.join(home, "Downloads"), os.path.join(home, "Desktop")]


def resolve_allowed(path, roots=None):
    """The real path when it exists and sits inside an allowed folder; else None. Symlinks/.. are resolved first."""
    if not isinstance(path, str) or not path.strip() or "\x00" in path or path.startswith(("\\\\", "//")):
        return None  # empty, NUL, or a network (UNC) path
    real = os.path.normcase(os.path.realpath(path.strip().strip('"')))
    if not os.path.exists(real):
        return None
    for root in roots or allowed_roots():
        r = os.path.normcase(os.path.realpath(root))
        try:
            if os.path.commonpath([real, r]) == r:
                return os.path.realpath(path.strip().strip('"'))
        except ValueError:  # different drives
            continue
    return None


def _not_direct(scope):
    """True for a request that is NOT a direct call from this PC's own window: another website (Origin/Host), a
    non-loopback client, or a tunnelled/proxied one. Launch-type actions only serve the owner at this keyboard."""
    from ishti.lifecycle_api import foreign_web_page
    client = (scope.get("client") or ("", 0))[0]
    headers = dict(scope.get("headers") or [])
    return client not in LOOPBACK or any(h in headers for h in PROXY_HEADERS) or foreign_web_page(scope)


def _launch(target):
    """Explorer with the file selected (a folder is opened). argv list, no shell."""
    if sys.platform == "win32":
        arg = target if os.path.isdir(target) else f"/select,{target}"
        subprocess.Popen(["explorer.exe", arg])  # explorer exits by itself; it is the owner's window, not our child
    elif sys.platform == "darwin":
        subprocess.Popen(["open", target] if os.path.isdir(target) else ["open", "-R", target])
    else:
        subprocess.Popen(["xdg-open", target if os.path.isdir(target) else os.path.dirname(target)])


@router.post("/api/reveal")
async def reveal(req: Request):
    if _not_direct(req.scope):
        return _err("Showing a file in Explorer is only possible from this PC's own window.", 403)
    try:
        body = await req.json()
    except ValueError:
        return _err("Send JSON like {\"path\": \"...\"}.", 400)
    path = body.get("path") if isinstance(body, dict) else None
    if not isinstance(path, str) or not path.strip():
        return _err("Which file? Send {\"path\": \"...\"}.", 400)
    real = resolve_allowed(path)
    if real is None:
        # same words for "not there" and "not allowed": the answer must not map the rest of the disk
        return _err("I can only show files in ISHTI's reports and data folders or your Documents/ISHTI, Downloads "
                    "and Desktop - and that file isn't there.", 404)
    audit.record("reveal in folder", real, initiator="owner", skill="files_api")
    if test_mode.enabled():
        return {"ok": True, "revealed": real, "launched": False}
    try:
        _launch(real)
    except OSError as exc:
        return _err(f"Explorer could not be opened ({exc.strerror or exc}).", 500)
    return {"ok": True, "revealed": real, "launched": True}


def attach(app):
    app.include_router(router)
