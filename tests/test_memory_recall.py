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


def test_owner_can_read_correct_confirm_and_forget_every_memory(mem):
    from fastapi.testclient import TestClient
    from interface.web_ui import server
    client = TestClient(server.app, base_url="http://127.0.0.1", client=("127.0.0.1", 50000))
    row = mem.semantic("I drive a Honda Civic", source="explicit_user")
    items = client.get("/api/memory/items").json()["items"]
    assert [i["content"] for i in items] == ["I drive a Honda Civic"]
    assert client.post(f"/api/memory/items/{row['id']}", json={"content": "I drive a Toyota Corolla"}).status_code == 200
    assert [i["content"] for i in client.get("/api/memory/items").json()["items"]] == ["I drive a Toyota Corolla"]
    assert [i["content"] for i in client.get("/api/memory/items?status=superseded").json()["items"]] == ["I drive a Honda Civic"]
    faded = mem.semantic("I go to the gym on Mondays", source="explicit_user")
    mem.conn().execute("UPDATE memories SET status='needs_confirmation' WHERE id=?", (faded["id"],))
    mem.conn().commit()
    assert client.post(f"/api/memory/items/{faded['id']}/confirm").json()["confirmed"] == 1
    new_id = client.get("/api/memory/items?q=Corolla").json()["items"][0]["id"]
    assert client.delete(f"/api/memory/items/{new_id}").json()["forgotten"] == 1
    assert [i["content"] for i in client.get("/api/memory/items").json()["items"]] == ["I go to the gym on Mondays"]
    assert client.get("/api/memory/items?status=forgotten").status_code == 400  # forgotten rows are not browsable


def test_same_subject_new_value_supersedes_without_a_model(mem, monkeypatch):
    from ishti.memstore import jsonio
    monkeypatch.setattr(jsonio, "ask_json", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no model call")))
    mem.reconcile("my favourite colour is blue", source="explicit_user")
    mem.reconcile("my favourite colour is green actually", source="explicit_user")
    assert [r["content"] for r in mem.search(mtype="semantic")] == ["my favourite colour is green"]
    assert mem.reconcile("My favourite colour is green.", source="explicit_user")["action"] == "NOOP"


def test_round6_regressions_lists_days_between_knowledge():
    from ishti import reflex
    from ishti.skills.chat.skill import KNOWLEDGE
    from ishti.skills.checklist import skill as checklist
    from ishti.skills.owner import skill as owner
    assert reflex.answer("how many days between 1 January 2026 and 9 November 2026?")[0].startswith("**312 days**")
    assert reflex.answer("how many days from 2026-03-01 to 2026-03-31")[0].startswith("**30 days**")
    assert KNOWLEDGE.match("who wrote Pride and Prejudice?")
    assert checklist.match("add milk and eggs to my shopping list") and not owner.match("add milk and eggs to my shopping list")
    assert checklist.SHOW.match("what's on my shopping list?").group("name3") == "shopping"
    assert not checklist.match("my spotify playlist")
