"""Bounded child-process logs. Pipe draining keeps a long-running Ollama log rotatable."""
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import threading

MAX_BYTES = 5_000_000
BACKUPS = 3


def rotate_closed(path, limit=MAX_BYTES, backups=BACKUPS):
    """Rotate a closed crash file at startup; preserve diagnostics rather than deleting them."""
    path = Path(path)
    if path.exists() and path.stat().st_size >= limit:
        for number in range(backups, 0, -1):
            previous = path if number == 1 else path.with_name(path.name + f".{number - 1}")
            if previous.exists():
                previous.replace(path.with_name(path.name + f".{number}"))


def drain(stream, path, limit=MAX_BYTES, backups=BACKUPS):
    handler = RotatingFileHandler(path, maxBytes=limit, backupCount=backups, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(message)s"))
    try:
        while data := stream.readline(16_384):
            line = data.decode("utf-8", errors="replace") if isinstance(data, bytes) else data
            handler.handle(logging.LogRecord("ollama", logging.INFO, "", 0, line.rstrip(), (), None))
    finally:
        handler.close()
        stream.close()


def pipe_log(stream, path):
    thread = threading.Thread(target=drain, args=(stream, path), daemon=True, name="ollama-log")
    thread.start()
    return thread
