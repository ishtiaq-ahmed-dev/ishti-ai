"""FIX-8 regression: Windows path regexes must accept forward slashes.

Users type D:/ISHTI/docs/file.md constantly; the old backslash-only patterns made
docs.reply "give me the full path" and dev.run_file decline existing .py files.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def test_docs_path_accepts_forward_slashes():
    from ishti.skills.docs.skill import PATH
    assert PATH.findall("summarize D:/ISHTI/docs/FINAL_REPORT.md") == ["D:/ISHTI/docs/FINAL_REPORT.md"]
    assert PATH.findall(r"summarize D:\ISHTI\docs\FINAL_REPORT.md") == [r"D:\ISHTI\docs\FINAL_REPORT.md"]


def test_dev_run_file_accepts_forward_slashes():
    import ishti.skills.dev.skill as dev
    for name, rx in dev.OPS:
        if name == "run_file":
            assert rx.search("run D:/ISHTI/tools/monitor.py"), "forward-slash .py path must match"
            assert rx.search(r"run D:\ISHTI\tools\monitor.py"), "backslash .py path must still match"
            return
    raise AssertionError("run_file op not found in dev.OPS")
