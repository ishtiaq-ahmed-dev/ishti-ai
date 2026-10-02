"""WP-ECC acceptance: local plan review page (matrix row ECC-08).

T-ECC-08:
  - render() escapes plan markup (a plan file can never inject into the page) and shows the
    Approve / Request-changes controls; a missing doc is a note, not an error
  - decisions: approve|request-changes with a doc are appended to data/plan_decisions.json
    (bounded history); anything else is None
  - serve() refuses any non-loopback host before opening a socket; on 127.0.0.1 it answers
    GET / (the page) and GET /decisions (history), POST /decision validates and appends,
    bad bodies get 400 and oversized bodies 413
  - the module's own __main__ self-check stays green

Run: venv\\Scripts\\python -m pytest tests/test_plan_view.py -q
"""
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from orchestrator import plan_view as pv  # noqa: E402

PLAN = "# The plan\n\n- step one\n- step two\n\n<script>alert(1)</script>\n<img src=x onerror=alert(2)>\n"


def _work(tmp_path, plan=PLAN):
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "IMPLEMENTATION_PLAN.md").write_text(plan, encoding="utf-8")
    return tmp_path


def _serve(tmp_path):
    dec = str(tmp_path / "data" / "plan_decisions.json")
    h = pv.serve(str(tmp_path), plans=("IMPLEMENTATION_PLAN.md",), decisions_file=dec)
    assert h["ok"] and h["url"].startswith("http://127.0.0.1:"), h
    return h, dec


def test_render_escapes_plan_markup(tmp_path):
    page = pv.render(str(_work(tmp_path)), plans=("IMPLEMENTATION_PLAN.md",))
    assert "Approve" in page and "Request changes" in page and "step one" in page
    assert "<script>alert(1)</script>" not in page and "<img" not in page
    assert "&lt;script&gt;" in page and "&lt;img" in page, "raw markup is escaped"
    page2 = pv.render(str(tmp_path), plans=("DOES_NOT_EXIST.md",))
    assert "not found" in page2 and "Approve" in page2


def test_decisions_are_validated_and_bounded(tmp_path):
    dec = str(tmp_path / "data" / "plan_decisions.json")
    assert pv.record_decision({"doc": "IMPLEMENTATION_PLAN.md", "decision": "approve"}, dec)
    assert pv.record_decision({"doc": "x", "decision": "request-changes", "note": "fix scope"}, dec)
    assert pv.record_decision({"doc": "x", "decision": "maybe"}, dec) is None
    assert pv.record_decision({"decision": "approve"}, dec) is None
    assert pv.record_decision("junk", dec) is None and pv.record_decision(None, dec) is None
    rows = pv.load_decisions(dec)
    assert len(rows) == 2 and rows[1]["note"] == "fix scope" and rows[0]["doc"].endswith(".md")
    assert pv.load_decisions(str(tmp_path / "nope.json")) == []


def test_history_is_capped(tmp_path, monkeypatch):
    monkeypatch.setattr(pv, "MAX_DECISIONS", 3)
    dec = str(tmp_path / "d.json")
    for i in range(5):
        assert pv.record_decision({"doc": f"d{i}", "decision": "approve"}, dec)
    rows = pv.load_decisions(dec)
    assert len(rows) == 3 and [r["doc"] for r in rows] == ["d2", "d3", "d4"]


def test_serve_refuses_non_loopback():
    for host in ("0.0.0.0", "192.168.1.10", "example.com", ""):
        h = pv.serve(host=host)
        assert h["ok"] is False and "loopback" in h["error"], host


def test_http_surface_end_to_end(tmp_path):
    h, dec = _serve(_work(tmp_path))
    base = h["url"].rstrip("/")
    try:
        with urllib.request.urlopen(base + "/", timeout=5) as r:
            body = r.read().decode("utf-8")
        assert r.status == 200 and "Approve" in body and "plan review" in body.lower()
        with urllib.request.urlopen(base + "/decisions", timeout=5) as r:
            assert json.loads(r.read()) == []
        req = urllib.request.Request(base + "/decision",
                                     data=json.dumps({"doc": "IMPLEMENTATION_PLAN.md",
                                                      "decision": "approve"}).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as r:
            row = json.loads(r.read())
        assert r.status == 200 and row["decision"] == "approve" and row["doc"] == "IMPLEMENTATION_PLAN.md"
        assert len(pv.load_decisions(dec)) == 1
        # invalid decision -> 400; unknown path -> 404; oversized -> 413
        bad = urllib.request.Request(base + "/decision", data=b'{"doc":"x","decision":"nope"}',
                                     headers={"Content-Type": "application/json"})
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(bad, timeout=5)
        assert e.value.code == 400 and len(pv.load_decisions(dec)) == 1
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(base + "/nope", timeout=5)
        assert e.value.code == 404
        big = urllib.request.Request(base + "/decision", data=b"x" * (pv.MAX_BODY + 1),
                                     headers={"Content-Type": "application/json"})
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(big, timeout=5)
        assert e.value.code == 413
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(urllib.request.Request(base + "/decision", data=b"not json"), timeout=5)
        assert e.value.code == 400
    finally:
        assert pv.stop(h)


def test_selfcheck_still_passes():
    mod = os.path.join(ROOT, "orchestrator", "plan_view.py")
    env = {**os.environ,
           "PYTHONPATH": ROOT + (os.pathsep + os.environ["PYTHONPATH"] if os.environ.get("PYTHONPATH") else "")}
    r = subprocess.run([sys.executable, mod], capture_output=True, text=True, timeout=120, cwd=ROOT, env=env)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "[self-check] plan_view OK" in r.stdout
