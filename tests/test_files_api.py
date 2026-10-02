"""POST /api/upload and POST /api/reveal (ishti/files_api.py): the backend contract for the chat attachment picker (N9)
and the exported-report "open in folder" button (CD-13). Offline: a throw-away FastAPI app, no models, Explorer never
launched (ISHTI_TEST_MODE=1 + a patched launcher)."""
import io
import os
import time

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
    again = up(c, "Quarterly report (final).PDF", b"second").json()
    assert again["path"] != j["path"]                      # never overwrites an earlier upload


def test_upload_kinds_drive_the_mention(api):
    c, _ = api
    assert up(c, "pic.png").json()["mention"].startswith("describe the image ")
    assert up(c, "voice.m4a").json()["mention"].startswith("transcribe ")


def test_upload_refuses_executables_empty_and_oversized(api, monkeypatch):
    c, tmp = api
    for bad in ("setup.exe", "run.bat", "x.ps1", "noext"):
        r = up(c, bad)
        assert r.status_code == 400 and "can't take" in r.json()["error"], bad
    assert up(c, "empty.txt", b"").status_code == 400
    monkeypatch.setattr(files_api, "MAX_UPLOAD_BYTES", 1024)
    r = up(c, "big.pdf", b"x" * 5000)
    assert r.status_code == 413 and "bigger than" in r.json()["error"]
    left = os.listdir(tmp / "uploads") if (tmp / "uploads").exists() else []
    assert not any(n.endswith("big.pdf") for n in left), left      # a partial file never stays


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
