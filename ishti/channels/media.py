"""Inbound media for remote channels (WP-A4): voice notes, photos, documents.

Rules:
  - downloads go through the client's getFile (Bot API limit 20 MB - enforced here too, R1 A)
  - voice notes -> LOCAL transcription (faster-whisper through the existing ishti.voice pipeline,
    which the VRAM governor already manages); no cloud STT anywhere
  - photos -> saved and handed to the dispatcher as a vision request (existing tiers, governed)
  - documents -> saved under data/uploads/remote/ and confirmed; the owner decides what happens next
  - every failure is honest ("could not transcribe: ..."), never a fake success
  - functions accept the raw Telegram message dict; returns replacement request text or None
"""
from __future__ import annotations

import os
import time

from ishti import CONFIG
from ishti.channels import policy as policy_mod

UPLOAD_DIR = os.path.join(os.path.dirname(CONFIG["memory"]["db_path"]), "uploads", "remote")
MAX_FILE_MB = 20


def _save_dir():
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    return UPLOAD_DIR


def ingest(m: dict, client) -> str | None:
    """If the message carries media, process it and return the text that should be dispatched
    instead of the (possibly empty) caption. All errors return honest replacement text."""
    if not client:
        return None
    voice = m.get("voice") or m.get("audio")
    if voice:
        return _voice(voice, client)
    photo = m.get("photo")
    if photo:
        return _photo(photo, client, (m.get("caption") or "describe this image"))
    doc = m.get("document")
    if doc:
        return _document(doc, client)
    return None


# ------------------------------------------------------------------ voice notes
def _voice(v: dict, client) -> str:
    size_mb = (v.get("file_size") or 0) / 1e6
    if size_mb > MAX_FILE_MB:
        return f"[voice note too large to fetch ({size_mb:.1f} MB > {MAX_FILE_MB} MB)]"
    stamp = time.strftime("%Y%m%d_%H%M%S")
    path = os.path.join(_save_dir(), f"voice_{stamp}.ogg")
    try:
        client.download(v["file_id"], path)
    except Exception as exc:
        return f"[could not download the voice note: {type(exc).__name__}]"
    try:
        from ishti.voice import transcribe
        text = (transcribe(path) or "").strip()
    except Exception as exc:
        return f"[could not transcribe the voice note locally: {type(exc).__name__}: {str(exc)[:80]}]"
    if not text:
        return "[the voice note transcribed to nothing]"
    try:
        from ishti import audit
        audit.record("remote voice note transcribed", f"{len(text)} chars", skill="channels")
    except Exception:
        pass
    return text


# ------------------------------------------------------------------ photos
def _photo(photos: list, client, caption: str) -> str:
    """Telegram sends an array of sizes; take the largest under the cap."""
    best = None
    for p in photos or []:
        if (p.get("file_size") or 0) <= MAX_FILE_MB * 1e6 and (best is None or
                                                               (p.get("file_size") or 0) > (best.get("file_size") or 0)):
            best = p
    if not best:
        return "[image too large to fetch remotely]"
    stamp = time.strftime("%Y%m%d_%H%M%S")
    path = os.path.join(_save_dir(), f"photo_{stamp}.jpg")
    try:
        client.download(best["file_id"], path)
    except Exception as exc:
        return f"[could not download the image: {type(exc).__name__}]"
    return f"{caption} (the image is saved at {path})"


# ------------------------------------------------------------------ documents
def _document(d: dict, client) -> str:
    name = os.path.basename(d.get("file_name") or "file.bin")
    safe = "".join(c for c in name if c.isalnum() or c in "._- ")[:80] or "file.bin"
    size_mb = (d.get("file_size") or 0) / 1e6
    if size_mb > MAX_FILE_MB:
        return f"[document '{safe}' too large to fetch ({size_mb:.1f} MB)]"
    path = os.path.join(_save_dir(), f"{time.strftime('%Y%m%d_%H%M%S')}_{safe}")
    try:
        client.download(d["file_id"], path)
    except Exception as exc:
        return f"[could not download '{safe}': {type(exc).__name__}]"
    return (f"I saved '{safe}' ({size_mb:.1f} MB) at {path}. Tell me what to do with it - "
            f"reading/summarizing it is fine remotely.")


# ------------------------------------------------------------------ outbound attachment dir
def export_file(name_hint: str, content: str) -> str:
    """Where output.py's big-table CSVs go. Local file under data/ (scope: allowed)."""
    _save_dir()
    safe = "".join(c for c in name_hint if c.isalnum() or c in "._-")[:60] or "table.csv"
    path = os.path.join(UPLOAD_DIR, f"{time.strftime('%Y%m%d_%H%M%S')}_{safe}")
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path


if __name__ == "__main__":
    import ishti.channels.policy as P
    import tempfile, os
    P.PATH = os.path.join(tempfile.mkdtemp(), "p.yaml")
    P.reset_cache()
    UPLOAD_DIR = os.path.join(tempfile.mkdtemp(), "up")

    class FakeClient:
        def __init__(self, fail=False):
            self.saved, self.fail = [], fail
        def download(self, file_id, dest):
            if self.fail:
                raise RuntimeError("boom")
            with open(dest, "wb") as f:
                f.write(b"oggdata")
            self.saved.append(dest)

    # voice path: too large -> honest refusal, no download
    c = FakeClient()
    r = _voice({"file_id": "f", "file_size": 30 * 1_000_000}, c)
    assert "too large" in r and not c.saved
    # download failure is honest
    r = _voice({"file_id": "f", "file_size": 1000}, FakeClient(fail=True))
    assert "could not download" in r
    # transcription: injected (no model here) - patch ishti.voice.transcribe
    import ishti.voice as V
    real = V.transcribe
    V.transcribe = lambda p, language=None: "weather tomorrow in Peshawar"
    try:
        r = _voice({"file_id": "f", "file_size": 1000}, FakeClient())
        assert r == "weather tomorrow in Peshawar", r
    finally:
        V.transcribe = real
    # transcribe failure is honest, never faked
    V.transcribe = lambda p, language=None: (_ for _ in ()).throw(ValueError("no model"))
    try:
        r = _voice({"file_id": "f", "file_size": 1000}, FakeClient())
        assert "could not transcribe" in r, r
    finally:
        V.transcribe = real
    # photo: largest under cap wins; caption preserved
    fc = FakeClient()
    r = _photo([{"file_id": "s", "file_size": 100}, {"file_id": "b", "file_size": 900},
                {"file_id": "x", "file_size": 99 * 1_000_000}], fc, "what is this")
    assert "what is this" in r and "photo_" in r and fc.saved and fc.saved[0].endswith(".jpg")
    # document: safe name + honest size refusal
    assert "too large" in _document({"file_name": "../evil.pdf", "file_size": 99 * 1_000_000}, FakeClient())
    fc = FakeClient()
    r = _document({"file_id": "f", "file_name": "report.pdf", "file_size": 1234}, fc)
    assert "report.pdf" in r and "saved" in r and fc.saved[0].endswith("report.pdf")
    # export_file: csv lands in the upload dir
    p = export_file("table_1.csv", "a,b\n1,2")
    assert os.path.exists(p) and p.endswith("table_1.csv")
    print("[self-check] channels.media OK - size caps, honest failures, voice/photo/document paths, csv export")
