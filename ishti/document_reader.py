"""Bounded document extraction in a separate process; this is not an OS sandbox."""
import base64
import json
import os
from pathlib import Path
import subprocess
import sys

MAX_FILE_BYTES = 50 * 1024 * 1024
MAX_TEXT_CHARS = 60000
MAX_OUTPUT_BYTES = MAX_TEXT_CHARS * 6 + 4096
TIMEOUT_SECONDS = 45
EXTENSIONS = {".pdf", ".docx", ".doc", ".xlsx", ".xls", ".csv", ".pptx", ".txt", ".md",
              ".html", ".htm", ".css", ".js", ".jsx", ".ts", ".tsx", ".py", ".xml", ".yaml", ".yml", ".toml", ".ini", ".log",
              ".sql", ".sh", ".java", ".c", ".cpp", ".h", ".go", ".rs", ".php", ".rb", ".vue", ".scss"}


class DocumentReadError(RuntimeError):
    """The reader failed without returning a trustworthy document result."""


def read_document(path, limit=MAX_TEXT_CHARS, *, timeout=TIMEOUT_SECONDS):
    limit = min(MAX_TEXT_CHARS, max(0, int(limit)))
    ext = Path(path).suffix.lower()
    if ext not in EXTENSIONS:
        raise DocumentReadError("Unsupported document format")
    with open(path, "rb") as source:
        data = source.read(MAX_FILE_BYTES + 1)
    if len(data) > MAX_FILE_BYTES:
        raise DocumentReadError("Document exceeds the 50 MiB reader limit")
    if limit == 0:
        return ""
    payload = json.dumps({"extension": ext, "limit": limit,
                          "bytes": base64.b64encode(data).decode("ascii")}).encode("utf-8")
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    try:
        result = subprocess.run(
            [sys.executable, "-I", str(Path(__file__).with_name("document_reader_worker.py"))],
            input=payload, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=max(0.01, min(TIMEOUT_SECONDS, float(timeout))), creationflags=flags,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise DocumentReadError("Document extraction timed out; the reader was terminated") from exc
    except OSError as exc:
        raise DocumentReadError("Document reader could not start") from exc
    if len(result.stdout) > MAX_OUTPUT_BYTES:
        raise DocumentReadError("Document reader exceeded its output limit")
    try:
        reply = json.loads(result.stdout)
        text = reply.get("text")
        if result.returncode or not isinstance(text, str) or len(text) > limit:
            raise ValueError("Reader rejected the document")
    except (ValueError, TypeError, AttributeError) as exc:
        raise DocumentReadError("Document extraction failed; the file may be damaged or protected") from exc
    return text
