"""POST /api/upload and POST /api/reveal (ishti/files_api.py): the backend contract for the chat attachment picker (N9)
and the exported-report "open in folder" button (CD-13). Offline: a throw-away FastAPI app, no models, Explorer never
launched (ISHTI_TEST_MODE=1 + a patched launcher)."""
import io
import os
import time
import asyncio
import threading

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ishti import files_api


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setattr(files_api, "UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setattr(files_api, "allowed_roots", lambda: [str(tmp_path / "reports")])
    monkeypatch.setenv("ISHTI_TEST_MODE", "1")
    monkeypatch.setattr(files_api.audit, "record", lambda *a, **k: None)
    app = FastAPI()
    files_api.attach(app)
    (tmp_path / "reports").mkdir()
    return TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 50000)), tmp_path


def up(client, name, data=b"hello", ctype="application/octet-stream"):
    return client.post("/api/upload", files={"file": (name, io.BytesIO(data), ctype)})


def test_upload_saves_under_a_safe_unique_name_and_returns_the_path(api):
    c, tmp = api
    r = up(c, "..\\..\\Quarterly report (final).PDF", b"%PDF-1.4 x")
    j = r.json()
    assert r.status_code == 200 and j["ok"] and j["kind"] == "document" and j["size"] == 10
    assert os.path.dirname(j["path"]) == str(tmp / "uploads") and ".." not in j["name"]
    assert open(j["path"], "rb").read() == b"%PDF-1.4 x"
    assert j["mention"] == f"summarize {j['path']}"
    assert j["action"] == "summarize the document"
    again = up(c, "Quarterly report (final).PDF", b"%PDF-1.7 second").json()
    assert again["path"] != j["path"]                      # never overwrites an earlier upload


def test_upload_rejects_scanner_crash_and_removes_staged_file(api, monkeypatch):
    from ishti.security import upload_scanner
    c, tmp = api
    def crash(_path):
        raise RuntimeError("synthetic scanner outage")
    monkeypatch.setattr(upload_scanner, "scan_upload", crash)
    response = up(c, "invoice.pdf", b"%PDF-1.4 synthetic")
    assert response.status_code == 503
    assert "scan could not complete" in response.json()["error"]
    assert not list((tmp / "uploads").iterdir())


def test_upload_kinds_drive_the_mention(api):
    c, _ = api
    assert up(c, "pic.png", b"\x89PNG\r\n\x1a\nrest").json()["mention"].startswith("describe the image ")
    assert up(c, "voice.m4a", b"\x00\x00\x00\x18ftypM4A \x00\x00\x00\x00").json()["mention"].startswith("transcribe ")


def test_slow_scanner_does_not_block_the_event_loop(tmp_path, monkeypatch):
    from fastapi import UploadFile
    from ishti.security import upload_scanner
    entered, release = threading.Event(), threading.Event()
    def scanner(_path):
        entered.set()
        return {"safe": release.wait(3), "reason": "Synthetic timeout"}
    monkeypatch.setattr(upload_scanner, "scan_upload", scanner)
    async def probe():
        job = asyncio.create_task(files_api.save_upload(UploadFile(filename="probe.txt", file=io.BytesIO(b"synthetic")), str(tmp_path)))
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            await asyncio.sleep(0)
            assert not job.done(), "The scanner blocked every coroutine until it finished"
        finally:
            release.set()
        info, problem = await job
        assert info["ok"] and problem is None
    asyncio.run(probe())


def test_cancelled_scan_removes_staged_upload(tmp_path, monkeypatch):
    from fastapi import UploadFile
    from ishti.security import upload_scanner
    entered, release = threading.Event(), threading.Event()
    folder = tmp_path / "uploads"
    def scanner(_path):
        entered.set()
        release.wait(3)
        return {"safe": True}
    monkeypatch.setattr(upload_scanner, "scan_upload", scanner)
    async def probe():
        job = asyncio.create_task(files_api.save_upload(
            UploadFile(filename="cancelled.txt", file=io.BytesIO(b"synthetic")), str(folder)))
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            job.cancel()
            with pytest.raises(asyncio.CancelledError):
                await job
            assert not list(folder.iterdir())
        finally:
            release.set()
    asyncio.run(probe())


def test_failed_upload_read_removes_partial_file(tmp_path):
    folder = tmp_path / "uploads"
    class BrokenUpload:
        filename = "broken.txt"
        reads = 0
        async def read(self, _size):
            self.reads += 1
            if self.reads == 1:
                return b"synthetic"
            raise RuntimeError("Disconnected stream")
    info, problem = asyncio.run(files_api.save_upload(BrokenUpload(), str(folder)))
    assert info is None and problem[1] == 503
    assert not list(folder.iterdir())


def test_upload_refuses_executables_empty_and_oversized(api, monkeypatch):
    c, tmp = api
    for bad in ("setup.exe", "mobile.apk", "library.jar", "native.dll", "run.bat", "x.ps1", "noext"):
        r = up(c, bad)
        assert r.status_code == 400 and "can't take" in r.json()["error"], bad
    assert up(c, "empty.txt", b"").status_code == 400
    monkeypatch.setattr(files_api, "MAX_UPLOAD_BYTES", 1024)
    r = up(c, "big.pdf", b"x" * 5000)
    assert r.status_code == 413 and "bigger than" in r.json()["error"]
    left = os.listdir(tmp / "uploads") if (tmp / "uploads").exists() else []
    assert not any(n.endswith("big.pdf") for n in left), left      # a partial file never stays


def test_upload_rejects_mismatched_and_unsafe_image_formats(api):
    c, tmp = api
    for name, content in (("fake.pdf", b"not a pdf"), ("fake.png", b"hello"), ("active.svg", b"<svg onload='alert(1)'>")):
        r = up(c, name, content)
        assert r.status_code == 400, (name, r.status_code, r.text)
    assert not (tmp / "uploads").exists() or not os.listdir(tmp / "uploads")


def test_iso_bmff_uploads_check_ftyp_brand_instead_of_accepting_zero_prefix(api):
    c, tmp = api
    # Realistic ftyp headers, with a size, box type, major brand and minor version.
    valid = {
        "photo.heic": b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00mif1",
        "audio.m4a": b"\x00\x00\x00\x18ftypM4A \x00\x00\x00\x00",
        "video.mp4": b"\x00\x00\x00\x18ftypisom\x00\x00\x00\x00",
        "video.mov": b"\x00\x00\x00\x18ftypqt  \x00\x00\x00\x00",
    }
    for name, content in valid.items():
        response = up(c, name, content)
        assert response.status_code == 200, (name, response.status_code, response.text)

    for name in ("fake.heic", "fake.m4a", "fake.mp4", "fake.mov"):
        response = up(c, name, b"\x00\x00\x00\x18arbitrary-data")
        assert response.status_code == 400, (name, response.status_code, response.text)
    assert len(os.listdir(tmp / "uploads")) == len(valid)


def test_old_uploads_are_pruned(api):
    c, tmp = api
    j = up(c, "old.txt").json()
    old = time.time() - 40 * 86400
    os.utime(j["path"], (old, old))
    up(c, "new.txt")
    assert not os.path.exists(j["path"])


def test_reveal_inside_an_allowed_folder(api):
    c, tmp = api
    f = tmp / "reports" / "digest.pdf"
    f.write_bytes(b"x")
    r = c.post("/api/reveal", json={"path": str(f)})
    assert r.status_code == 200 and r.json() == {"ok": True, "revealed": str(f), "launched": False}   # test mode: no Explorer


def test_reveal_launches_explorer_through_the_launcher_outside_test_mode(api, monkeypatch):
    c, tmp = api
    monkeypatch.delenv("ISHTI_TEST_MODE")
    seen = []
    monkeypatch.setattr(files_api, "_launch", lambda target: seen.append(target))
    f = tmp / "reports" / "a b.pdf"
    f.write_bytes(b"x")
    assert c.post("/api/reveal", json={"path": str(f)}).json()["launched"] is True and seen == [str(f)]


@pytest.mark.parametrize("path", ["C:\\Windows\\System32\\cmd.exe", "\\\\server\\share\\x.pdf", "", None, 5, "bad\x00name"])
def test_reveal_refuses_everything_outside_the_allowed_folders(api, path):
    c, _ = api
    r = c.post("/api/reveal", json={"path": path})
    assert r.status_code in (400, 404) and "error" in r.json()


def test_reveal_cannot_escape_with_dotdot_or_a_missing_file(api):
    c, tmp = api
    (tmp / "secret.txt").write_text("x")
    assert c.post("/api/reveal", json={"path": str(tmp / "reports" / ".." / "secret.txt")}).status_code == 404
    assert c.post("/api/reveal", json={"path": str(tmp / "reports" / "nope.pdf")}).status_code == 404


def test_launch_actions_refuse_other_websites_and_remote_callers(api):
    c, tmp = api
    f = tmp / "reports" / "d.pdf"
    f.write_bytes(b"x")
    evil = c.post("/api/reveal", json={"path": str(f)}, headers={"origin": "https://evil.example"})
    assert evil.status_code == 403
    tunnelled = c.post("/api/reveal", json={"path": str(f)}, headers={"x-forwarded-for": "1.2.3.4"})
    assert tunnelled.status_code == 403
    assert up(c, "a.txt").status_code == 200
    assert c.post("/api/upload", files={"file": ("a.txt", io.BytesIO(b"x"))},
                  headers={"origin": "https://evil.example"}).status_code == 403
    remote = TestClient(c.app, base_url="http://127.0.0.1", client=("203.0.113.9", 1234))
    assert remote.post("/api/reveal", json={"path": str(f)}).status_code == 403


def test_reveal_bad_body(api):
    c, _ = api
    assert c.post("/api/reveal", content=b"not json", headers={"content-type": "application/json"}).status_code == 400
