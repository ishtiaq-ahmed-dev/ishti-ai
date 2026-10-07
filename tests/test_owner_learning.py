import os
import tempfile

from ishti import behavior_profiles as bp
from ishti import owner_learning as ol
from ishti import runs


def test_stable_signals_become_visible_facts_once(monkeypatch):
    d = tempfile.mkdtemp()
    runs.set_db_path(os.path.join(d, "runs.db"))
    monkeypatch.setattr(bp, "_default_store", bp.BehaviorProfileStore(os.path.join(d, "l.db")))
    seen = []
    for _ in range(5):
        seen += ol.observe_turn("mujhe batao mera project kahan hai", ["files"])
    facts = [f["value"] for f in bp.owner_visible_facts()]
    assert any("Urdu" in f for f in facts) and any("files skill" in f for f in facts)
    assert len(facts) == len(set(facts))                       # never duplicated
    runs.set_learning_mode("off")
    assert ol.observe_turn("hello there friend", ["files"]) == []   # off means off
    got = ol.infer_preferences(llm_fn=lambda p: '{"preferences":[{"type":"tone","value":"prefers short direct answers"}]}')
    assert got == []                                           # too little history: nothing inferred
