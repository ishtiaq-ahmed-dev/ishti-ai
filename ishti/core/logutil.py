"""Structured JSON logging with secret/PII redaction (E1).

Every log line is one JSON object: {ts, level, logger, msg, ...fields}. Secrets and PII
are redacted by settings.privacy.redact_patterns (emails/phones/cards by default) plus
explicit secret values registered at runtime (`register_secret` — a key typed in chat, an
API key in a config). Read-only by design: modules log via `jlog(...)` and never worry
about formatting or redaction themselves.
"""
from __future__ import annotations

import json
import os
import re
import sys
import threading
import time

_lock = threading.Lock()
_extra_secrets: list[str] = []
_configured = False
_also_stderr = True
_log_path: str = ""
_patterns: list[re.Pattern] = []


def _default_patterns() -> list[re.Pattern]:    return [re.compile(p)
            for p in (r"[\w.+-]+@[\w-]+\.[\w.]+",          # emails
                      r"\b(?:\+92|0)\d{2,3}[- ]?\d{7,8}\b",  # PK phone numbers (0300-1234567 too)
                      r"\b(?:\d[ -]?){13,19}\b",            # card numbers
                      r"(?:sk|pk|api)[- _]?[A-Za-z0-9]{16,}")]  # API-key shapes


def configure(log_path: str | None = "", patterns: list[str] | None = None,
              enabled: bool = True, also_stderr: bool = True):
    """Called once at startup (web server entry). Safe to call again."""
    global _configured, _log_path, _patterns, _also_stderr
    with _lock:
        if log_path:
            os.makedirs(os.path.dirname(log_path) or ".", exist_ok=True)
            _log_path = log_path
        else:
            _log_path = ""
        _patterns = [re.compile(p) for p in (patterns or [])] + _default_patterns()
        _configured = enabled
        _also_stderr = also_stderr


def register_secret(value: str):
    """A literal value that must never appear in logs (a typed password, a session key)."""
    if value and len(value) >= 4:
        with _lock:
            if value not in _extra_secrets:
                _extra_secrets.append(value)


def redact(text: str) -> str:
    """Mask every registered secret and PII pattern in `text`."""
    t = text or ""
    for s in list(_extra_secrets):
        if s in t:
            t = t.replace(s, "\u2022" * 8)
    for rx in _patterns:
        t = rx.sub(lambda m: "\u2022" * max(6, min(len(m.group(0)), 12)), t)
    return t


def jlog(level: str, logger: str, msg: str, **fields):
    """One JSON line to the log file (and stderr when no file is configured)."""
    rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "level": level.upper(), "logger": logger,
           "msg": redact(str(msg))}
    for k, v in fields.items():
        rec[k] = redact(str(v)) if isinstance(v, str) else v
    line = json.dumps(rec, ensure_ascii=False, default=str)
    with _lock:
        try:
            if _log_path:
                with open(_log_path, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
            elif _also_stderr:
                sys.stderr.write(line + "\n")
        except OSError:
            pass


def info(logger: str, msg: str, **fields): jlog("INFO", logger, msg, **fields)
def warn(logger: str, msg: str, **fields): jlog("WARNING", logger, msg, **fields)
def error(logger: str, msg: str, **fields): jlog("ERROR", logger, msg, **fields)


if __name__ == "__main__":
    import tempfile
    path = os.path.join(tempfile.mkdtemp(prefix="ishti_log_"), "t.jsonl")
    configure(log_path=path, enabled=True)
    register_secret("SuperSecretValue123")
    info("test", "user jane@doe.com typed SuperSecretValue123 and paid 4111-1111-1111-1111",
         request="call 0300-1234567 back")
    with open(path, encoding="utf-8") as f:
        out = f.read().strip().splitlines()[-1]
    d = json.loads(out)
    assert "jane@doe.com" not in d["msg"] and "SuperSecretValue123" not in d["msg"], d
    assert "4111" not in d["msg"] and "0300" not in d["msg"] and "0300" not in d["request"], d
    assert "call" in d["request"] and "\u2022" in d["request"], d  # redacted, not dropped
    print("[self-check] core.logutil OK — JSON lines, secrets+PII redacted, fields kept")
