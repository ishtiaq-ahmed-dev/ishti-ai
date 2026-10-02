"""Settings downloads can be cancelled, and cancelling removes what was not finished (Ollama models, voice files, partial blobs)."""
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from ishti import manage


class FakeOllama(BaseHTTPRequestHandler):
    deleted = []
    models = []

    def log_message(self, *a):
        pass

    def do_GET(self):
        body = json.dumps({"models": [{"name": m} for m in FakeOllama.models]}).encode()
        self.send_response(200); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

    def do_DELETE(self):
        n = int(self.headers.get("Content-Length", 0))
        FakeOllama.deleted.append(json.loads(self.rfile.read(n))["model"])
        self.send_response(200); self.send_header("Content-Length", "0"); self.end_headers()

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        self.rfile.read(n)
        self.send_response(200); self.send_header("Content-Type", "application/x-ndjson"); self.end_headers()
        try:
            for i in range(200):  # a slow pull: 200 progress lines, 50 ms apart
                self.wfile.write((json.dumps({"status": "pulling x", "completed": i * 10, "total": 2000}) + "\n").encode())
                self.wfile.flush()
                time.sleep(0.05)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass


@pytest.fixture
def fake(monkeypatch, tmp_path):
    srv = HTTPServer(("127.0.0.1", 0), FakeOllama)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    FakeOllama.deleted, FakeOllama.models = [], ["qwen2.5:3b"]
    monkeypatch.setattr(manage, "OLLAMA", f"http://127.0.0.1:{srv.server_port}")
    monkeypatch.setattr(manage, "_notify", lambda *a, **k: None)
    monkeypatch.setattr(manage, "_audit", lambda *a, **k: None)
    monkeypatch.setenv("OLLAMA_MODELS", str(tmp_path))
    (tmp_path / "blobs").mkdir()
    yield tmp_path
    srv.shutdown()


def _wait(pred, secs=10):
    end = time.time() + secs
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.05)
    return False


def test_cancelled_pull_removes_the_model_and_its_partial_blobs(fake):
    (fake / "blobs" / "sha256-aaa-partial").write_bytes(b"x" * 1000)           # this pull's unfinished layer
    old = fake / "blobs" / "sha256-bbb-partial"
    old.write_bytes(b"y" * 10)
    os.utime(old, (time.time() - 3600, time.time() - 3600))                     # somebody else's, an hour old: never touched
    manage.pull_model("some-new:model")
    assert _wait(lambda: manage.JOBS.get("some-new:model", {}).get("done", 0) > 0)
    manage.cancel_download("some-new:model")
    assert _wait(lambda: manage.JOBS["some-new:model"]["status"] == "cancelled")
    assert "some-new:model" in FakeOllama.deleted and "qwen2.5:3b" not in FakeOllama.deleted     # a model the owner already had is never deleted
    assert not (fake / "blobs" / "sha256-aaa-partial").exists() and old.exists()


def test_cancel_with_nothing_running_is_a_plain_error(fake):
    with pytest.raises(ValueError):
        manage.cancel_download("nothing-here")


def test_cleanup_incomplete_removes_part_files(monkeypatch, tmp_path):
    monkeypatch.setattr(manage, "DATA", str(tmp_path))
    (tmp_path / "piper").mkdir()
    keep, part = tmp_path / "piper" / "voice.onnx", tmp_path / "piper" / "voice.onnx.part"
    keep.write_bytes(b"ok"); part.write_bytes(b"half")
    assert manage.cleanup_incomplete() == [str(part)] and keep.exists() and not part.exists()


def test_cancelled_voice_download_deletes_unfinished_files(monkeypatch, tmp_path):
    monkeypatch.setattr(manage, "_notify", lambda *a, **k: None)
    monkeypatch.setattr(manage, "_audit", lambda *a, **k: None)
    f1, f2 = tmp_path / "a.bin", tmp_path / "b.bin"

    class R:  # a server that never finishes the second file
        headers = {"content-length": "100"}
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def raise_for_status(self): pass
        def iter_content(self, n):
            for _ in range(1000):
                time.sleep(0.01)
                yield b"z" * 10
    monkeypatch.setattr(manage, "engines", lambda: {"eng": {"files": [(str(f1), "http://x/a"), (str(f2), "http://x/b")]}})
    monkeypatch.setattr(manage.requests, "get", lambda *a, **k: R())
    f1.write_bytes(b"")  # pre-existing file is skipped by the loop and must survive
    f1_before = f1.exists()
    manage.download_files("eng")
    assert _wait(lambda: manage.JOBS["eng"]["done"] > 0)
    manage.cancel_download("eng")
    assert _wait(lambda: manage.JOBS["eng"]["status"] == "cancelled")
    assert f1_before and f1.exists() and not f2.exists() and not (tmp_path / "b.bin.part").exists()
