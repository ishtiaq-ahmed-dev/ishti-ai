"""No source file may contain a backspace byte (0x08). A shell heredoc turns the regex word boundary \\b into 0x08; the
pattern still compiles, so routing silently stops matching (it broke web_search's clause splitter and, earlier, the
WhatsApp and owner skills). This scan makes that failure loud."""
import pathlib
import subprocess

ROOT = pathlib.Path(__file__).resolve().parents[1]


def test_no_backspace_bytes_in_tracked_source():
    files = subprocess.run(["git", "ls-files", "*.py", "*.ts", "*.tsx", "*.json"], capture_output=True, text=True,
                           cwd=ROOT).stdout.split()
    assert files, "git ls-files returned nothing"
    bad = [f for f in files if (ROOT / f).is_file() and b"\x08" in (ROOT / f).read_bytes()]
    assert not bad, f"backspace bytes (a mangled regex \\b) in: {bad}"
