"""FIX-8 regression: Windows path regexes must accept forward slashes.

Users type D:/work/docs/file.md constantly; the old backslash-only patterns made
docs.reply "give me the full path" and dev.run_file decline existing .py files.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def test_docs_path_accepts_forward_slashes():
    from ishti.skills.docs.skill import PATH
    assert PATH.findall("summarize D:/work/docs/report.md") == ["D:/work/docs/report.md"]
    assert PATH.findall(r"summarize D:\work\docs\report.md") == [r"D:\work\docs\report.md"]


def test_dev_run_file_accepts_forward_slashes():
    import ishti.skills.dev.skill as dev
    for name, rx in dev.OPS:
        if name == "run_file":
            assert rx.search("run D:/work/tools/monitor.py"), "forward-slash .py path must match"
            assert rx.search(r"run D:\work\tools\monitor.py"), "backslash .py path must still match"
            return
    raise AssertionError("run_file op not found in dev.OPS")
