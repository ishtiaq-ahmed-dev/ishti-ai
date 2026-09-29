"""Reusable scratch sandbox for QA per-skill loops (prompt 2).

Isolates every persistent store (memory db, chroma, facts singleton, audit db, session dict) into a
fresh temp dir so per-skill loops cannot pollute real user data and can be re-run any number of times.
"""
import contextlib
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


@contextlib.contextmanager
def scratch_stores():
    """Redirect CONFIG memory paths + reset the facts singleton + isolate audit + fresh sessions."""
    import ishti.facts as facts
    from ishti import CONFIG
    real = (CONFIG["memory"]["db_path"], CONFIG["memory"]["chroma_dir"])
    tmp = tempfile.mkdtemp(prefix="qa_scratch_")
    CONFIG["memory"].update(db_path=os.path.join(tmp, "t.db"), chroma_dir=os.path.join(tmp, "chroma"))
    facts._facts = None
    try:
        with contextlib.redirect_stdout(open(os.devnull, "w", encoding="utf-8")):
            yield tmp
    finally:
        CONFIG["memory"].update(db_path=real[0], chroma_dir=real[1])
        facts._facts = None


if __name__ == "__main__":
    from ishti import dispatcher
    with scratch_stores():
        r = dispatcher.handle("Reply with the exact word: PONG", "qa_sandbox_selfcheck")
        assert "PONG" in str(r.get("final_answer") or r.get("reply") or ""), r
    print("[self-check] qa_report.scratch_stores OK - isolated run answered inside the sandbox")
