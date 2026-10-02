"""WP-OD acceptance: workspace path confinement (matrix row OD-15).

T-OD-15, against a scratch workspace root:
  - root confinement is realpath-based: a plain path either stays inside the root or is
    refused with a reason ("..", absolute escape, drive-relative "C:x", UNC/device,
    alternate data stream, reserved device name, trailing dot/space, null byte, over-long)
  - a symlink that points outside the root is refused (when the OS allows creating one)
  - size caps: writes and reads above max_bytes are refused; exactly-at-cap passes
  - list()/delete()/ensure_dir() only ever touch the root - the outside decoy is untouched
  - every method is total: junk inputs return None/False/[], never raise
  - the module's own __main__ self-check stays green

Run: venv\\Scripts\\python -m pytest tests/test_workspace_confinement.py -q
"""
import os
import pathlib
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from ishti.workspace import Workspace  # noqa: E402


@pytest.fixture()
def env(tmp_path):
    root = tmp_path / "ws"
    ws = Workspace(str(root), max_bytes=1024)
    outside = tmp_path / "outside.txt"
    outside.write_text("owner file - unreachable through the workspace", encoding="utf-8")
    return {"ws": ws, "root": root, "outside": outside, "base": tmp_path}


def test_plain_paths_resolve_inside_the_root(env):
    ws = env["ws"]
    assert ws.resolve("notes.txt") == os.path.join(ws.root, "notes.txt")
    assert ws.resolve("deep/nested/a.md") == os.path.join(ws.root, "deep", "nested", "a.md")
    assert ws.resolve(pathlib.Path("sub") / "x.txt") == os.path.join(ws.root, "sub", "x.txt")
    assert ws.resolve("notes/../a.txt") == os.path.join(ws.root, "a.txt"), "internal .. normalises"
    assert ws.resolve(".") == ws.root
    assert ws.contains(str(env["root"] / "inside.txt"))
    assert not ws.contains(env["outside"])


@pytest.mark.parametrize("hostile,needle", [
    ("../outside.txt", "escape"),
    ("..\\outside.txt", "escape"),
    ("a/../../outside.txt", "escape"),
    (r"C:outside.txt", "drive-relative"),
    (r"\\server\share\x", "UNC"),
    (r"\\?\C:\Windows", "UNC"),
    (r"\\\\.\\pipe\\x", "UNC"),
    ("notes.txt:secret", "stream"),
    ("NUL", "reserved"),
    ("aux.log", "reserved"),
    ("COM1.txt", "reserved"),
    ("lpt9", "reserved"),
    ("bad.", "trailing"),
    ("bad ", "trailing"),
    ("a\x00b.txt", "null"),
    ("x" * 300, "too long"),
    ("", "empty"),
    (None, "empty"),
])
def test_escapes_are_refused_with_a_reason(env, hostile, needle):
    r = env["ws"].check(hostile)
    assert r["ok"] is False and needle in r["reason"], (hostile, r)
    assert env["ws"].resolve(hostile) is None


def test_absolute_outside_path_is_refused(env):
    r = env["ws"].check(str(env["outside"]))
    assert r["ok"] is False and "escape" in r["reason"]
    assert not env["ws"].write_text(str(env["outside"]), "pwned")


def test_symlink_escape_is_refused(env):
    link = env["root"] / "linkout"
    try:
        os.symlink(str(env["base"]), str(link), target_is_directory=True)
    except (OSError, NotImplementedError):
        # Windows: junctions need no special privilege, so the escape is still exercised
        r = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(env["base"])],
                           capture_output=True, text=True)
        if r.returncode != 0:
            pytest.skip(f"cannot create symlink or junction here: {r.stdout} {r.stderr}")
    assert env["ws"].resolve("linkout/outside.txt") is None
    assert not env["ws"].write_text("linkout/pwned.txt", "x")
    assert not (env["base"] / "pwned.txt").exists()


def test_size_caps_for_read_and_write(env):
    ws = env["ws"]                                       # max_bytes = 1024
    assert ws.write_text("ok.txt", "x" * 1024)           # exactly at cap passes
    assert not ws.write_text("big.txt", "x" * 1025)
    assert ws.read_text("ok.txt") == "x" * 1024
    with open(env["root"] / "over.bin", "wb") as f:
        f.write(b"y" * 2000)
    assert ws.read_bytes("over.bin") is None
    assert ws.read_text("ok.txt", max_bytes=10) is None, "per-call cap"
    assert ws.write_text("append.txt", "1234567890") and ws.write_text(
        "append.txt", "1234567890", append=True) and not ws.write_text(
        "append.txt", "x" * 1010, append=True), "append respects the remaining budget"


def test_list_is_confined_and_bounded(env):
    ws = env["ws"]
    for name in ("a.txt", "b.txt", "c.txt"):
        assert ws.write_text(name, name)
    decoy = env["base"] / "decoy.txt"
    decoy.write_text("not in the workspace", encoding="utf-8")
    rows = ws.list(limit=2)
    assert len(rows) == 2, "bounded"
    names = [r["name"] for r in ws.list()]
    assert set(names) == {"a.txt", "b.txt", "c.txt"}
    assert "decoy.txt" not in names
    assert all(r["path"].startswith(ws.root + os.sep) for r in rows)


def test_delete_and_ensure_dir_never_leave_the_root(env):
    ws = env["ws"]
    assert ws.ensure_dir("one/two") and os.path.isdir(os.path.join(ws.root, "one", "two"))
    assert not ws.ensure_dir("../evil") and not os.path.exists(env["base"] / "evil")
    assert ws.write_text("gone.txt", "bye")
    assert ws.delete("gone.txt") and not os.path.exists(os.path.join(ws.root, "gone.txt"))
    assert not ws.delete(str(env["outside"]))
    assert not ws.delete("../outside.txt")
    assert env["outside"].exists() and env["outside"].read_text(encoding="utf-8").startswith("owner file")
    assert not ws.delete(".") and ws.root is not None


def test_outside_file_is_untouched_after_attacks(env):
    ws = env["ws"]
    before = env["outside"].read_text(encoding="utf-8")
    for attack in ("../outside.txt", str(env["outside"]), "..\\outside.txt"):
        assert ws.write_text(attack, "overwritten") is False
        assert ws.read_text(attack) is None
        assert ws.delete(attack) is False
    assert env["outside"].read_text(encoding="utf-8") == before


def test_every_method_is_total_on_junk(env):
    ws = env["ws"]
    for junk in (42, ["a"], {"p": 1}, object(), b"bytes", None):
        assert ws.resolve(junk) is None, repr(junk)
        assert ws.check(junk)["ok"] is False
        assert ws.read_text(junk) is None and ws.read_bytes(junk) is None
        assert ws.write_text(junk, "x") is False
        assert ws.ensure_dir(junk) is False and ws.delete(junk) is False
    assert isinstance(ws.list(limit="junk"), list)


def test_selfcheck_still_passes():
    mod = os.path.join(ROOT, "ishti", "workspace.py")
    env = {**os.environ,
           "PYTHONPATH": ROOT + (os.pathsep + os.environ["PYTHONPATH"] if os.environ.get("PYTHONPATH") else "")}
    r = subprocess.run([sys.executable, mod], capture_output=True, text=True,
                       timeout=120, cwd=ROOT, env=env)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "[self-check] workspace OK" in r.stdout
