"""Markets History: every analysis is saved with its question, write-up and data used; newest first; searchable; deletable."""
import time

from ishti.skills.markets import research as r


def _plan(action="BUY"):
    return {"action": action, "entry": 100.0, "stop": 92.0, "t1": 115.0, "t2": 130.0}


def test_save_list_search_open_delete():
    base = len(r.history_rows())
    r.log_call("NVDA", "NVIDIA", "USD", 101.5, _plan(), question="should I buy nvidia", analysis="**NVIDIA**: buy near 100",
               data_used={"sources": ["Yahoo Finance"], "days": 250, "headlines": 6})
    time.sleep(0.01)
    r.log_call("OGDC", "Oil & Gas Dev", "PKR", 314.0, _plan("WAIT"), question="analyze ogdc", analysis="wait for a dip")
    rows = r.history_rows()
    assert len(rows) == base + 2 and rows[0]["symbol"] == "OGDC", "newest first"
    hit = r.history_rows("nvidia")
    assert [x["symbol"] for x in hit] == ["NVDA"] and hit[0]["data"]["days"] == 250
    assert [x["symbol"] for x in r.history_rows("WAIT")] == ["OGDC"]            # the action is searchable too
    one = r.get_call(hit[0]["id"])
    assert one["analysis"].startswith("**NVIDIA**") and one["t1"] == 115.0
    assert r.delete_call(one["id"]) == 1 and r.get_call(one["id"]) is None
    assert r.delete_call(rows[0]["id"]) == 1 and len(r.history_rows()) == base


def test_limit_cuts_the_oldest_not_the_newest():
    for i in range(5):
        r.log_call(f"S{i}", f"stock {i}", "USD", 1.0, _plan(), question=f"q{i}")
        time.sleep(0.005)
    top = r.history_rows(limit=2)
    assert [x["symbol"] for x in top] == ["S4", "S3"]
    for x in r.history_rows(limit=5):
        r.delete_call(x["id"])
