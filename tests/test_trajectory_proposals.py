import os
import tempfile

from ishti import feedback_bridge, runs


def test_third_repeat_opens_exactly_one_proposal():
    runs.set_db_path(os.path.join(tempfile.mkdtemp(), "runs.db"))
    seq = ["web_search", "docs"]
    assert feedback_bridge.trajectory("r1", seq) is None and feedback_bridge.trajectory("r2", seq) is None
    p = feedback_bridge.trajectory("r3", seq)
    assert p and p["origin"] == "trajectory" and "web_search -> docs" in p["title"]
    assert feedback_bridge.trajectory("r4", seq) is None            # only once
    assert feedback_bridge.trajectory("x", ["chat", "docs"]) is None  # chat + single skill is not a workflow
    assert len(runs.list_proposals("open")) == 1
