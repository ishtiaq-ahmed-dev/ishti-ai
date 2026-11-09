"""Recall regressions (LongMemEval-style, tiny): knowledge updates supersede, recall is dated and recency-weighted,
unrelated questions pull nothing, and the recall index stays bounded. Scratch stores only."""
import time

import pytest

from ishti.memstore import memory, retrieval


@pytest.fixture
def mem(tmp_path, monkeypatch):
    monkeypatch.setattr(memory, "db_path", lambda: str(tmp_path / "arch2.db"))
    monkeypatch.setattr(memory, "_conn", None)
    from ishti.learning import store
    store.set_db_path(str(tmp_path / "learn.db"))
    monkeypatch.setattr(retrieval, "graph_hits", lambda q: [])
    monkeypatch.setattr(retrieval, "vector_hits", lambda q, **k: [])
    yield memory
    if memory._conn is not None:
        memory._conn.close()


def _block(q):
    from ishti.core.assemble import arch2_block
    return arch2_block(q)


def test_changed_fact_supersedes_the_old_memory(mem):
    from ishti.facts import get_facts
    mem.semantic("The owner lives in Peshawar with family", source="explicit_user")
    get_facts().set("owner", "city", "Peshawar")
    get_facts().set("owner", "city", "Lahore")
    out = _block("where does the owner live with family")
    assert "Lahore" in out and "Peshawar" not in out
    assert mem.search(status="superseded")[0]["valid_until"]  # kept as "was true until", not deleted


def test_remember_that_reconciles_instead_of_duplicating(mem, monkeypatch):
    from ishti.memstore import consolidate, jsonio
    old = mem.semantic("I work at the bank", source="explicit_user")
    monkeypatch.setattr(jsonio, "ask_json", lambda *a, **k: {"action": "UPDATE", "target_id": old["id"],
                                                             "merged_content": "I work at a startup"})
    consolidate.handle_command("remember that I work at a startup")
    active = [r["content"] for r in mem.search(mtype="semantic")]
    assert active == ["I work at a startup"]


def test_recall_lines_are_dated_and_recent_wins_ties(mem):
    old = mem.semantic("Favourite coffee is dark roast", source="explicit_user")
    new = mem.semantic("Favourite coffee is flat white", source="explicit_user")
    year_ago = time.time() - 400 * 86400
    mem.conn().execute("UPDATE memories SET created_at=?, last_confirmed_at=? WHERE id=?", (year_ago, year_ago, old["id"]))
    mem.conn().commit()
    hits = [h["row"]["content"] for h in retrieval.memory_hits("favourite coffee")]
    assert hits[0] == "Favourite coffee is flat white"
    out = _block("favourite coffee")
    assert time.strftime("[%Y-%m-%d] Favourite coffee is flat white") in out and "newer line overrides" in out


def test_unrelated_question_pulls_nothing(mem):
    mem.semantic("My sister Sara's birthday is on 3 March", source="explicit_user")
    assert "RELEVANT MEMORIES" not in _block("quantum chromodynamics lattice gauge theory")


def test_recall_index_is_dated_and_bounded(tmp_path, monkeypatch):
    from ishti import lexical_store
    monkeypatch.setattr(lexical_store, "MAX_ROWS", 3)
    s = lexical_store.LexicalStore(str(tmp_path / "lex"))
    for i in range(5):
        s.add(f"User: my sister's birthday note number {i}")
    assert s.count() == 3
    hit = s.recall("when is my sister's birthday")
    assert hit and hit[0].startswith(time.strftime("[%Y-%m-%d] User:"))
