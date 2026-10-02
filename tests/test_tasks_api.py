"""G: the Tasks page API (ishti/dispatcher.py): complete/open, edit, snooze, delete and create-with-undo.
The whole point of the rewrite: completing a task must never make it vanish - it stays in the list as Done.
Offline: a temp facts DB, no models, no network."""
import os
import time

import pytest
from fastapi.testclient import TestClient

from ishti import facts


@pytest.fixture
def api(tmp_path, monkeypatch):
    f = facts.Facts(os.path.join(str(tmp_path), "t.db"))
    monkeypatch.setattr(facts, "_facts", f)
    from interface.web_ui import server
    return TestClient(server.app, base_url="http://127.0.0.1", client=("127.0.0.1", 50000)), f


def _mk(client, text, due):
    r = client.post("/api/reminders", json={"text": text, "due": due})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_completing_a_task_keeps_it_in_the_list(api):
    client, _f = api
    rid = _mk(client, "call Ali", time.time() + 3600)
    assert [r["id"] for r in client.get("/api/reminders").json()["reminders"]] == [rid]
    assert client.post(f"/api/reminders/{rid}/done", json={}).json()["updated"] == 1
    assert client.get("/api/reminders").json()["reminders"] == []              # the open list ignores it...
    rows = client.get("/api/reminders?include_done=1").json()["reminders"]     # ...but it is still there
    assert rows and rows[0]["id"] == rid and rows[0]["done"] is True
    assert client.post(f"/api/reminders/{rid}/open", json={}).json()["updated"] == 1
    assert client.get("/api/reminders?include_done=1").json()["reminders"][0]["done"] is False


def test_edit_snooze_and_delete(api):
    client, _f = api
    rid = _mk(client, "pay rent", time.time() + 3600)
    assert client.post(f"/api/reminders/{rid}", json={"text": "pay the rent", "due": "2030-01-02T09:00"}).json()["updated"] == 1
    row = client.get("/api/reminders?include_done=1").json()["reminders"][0]
    assert row["text"] == "pay the rent" and row["due"] > time.time()
    before = row["due"]
    client.post(f"/api/reminders/{rid}/done", json={})
    assert client.post(f"/api/reminders/{rid}/snooze", json={"minutes": 10}).json()["snoozed_minutes"] == 10
    row = client.get("/api/reminders?include_done=1").json()["reminders"][0]
    assert row["done"] is False and row["due"] >= before + 600                 # snoozing re-opens it, later
    assert client.delete(f"/api/reminders/{rid}").json()["deleted"] == 1
    assert client.get("/api/reminders?include_done=1").json()["reminders"] == []


def test_bad_input_is_refused_and_restoring_works(api):
    client, _f = api
    assert client.post("/api/reminders", json={"text": "x"}).status_code == 400
    assert client.post("/api/reminders", json={"text": "x", "due": "not a time"}).status_code == 400
    assert client.post("/api/reminders/999", json={}).status_code == 400        # nothing to change
    assert client.post("/api/reminders/999/snooze", json={"minutes": 5}).status_code == 404
    assert client.delete("/api/reminders/999").json()["deleted"] == 0
    # Undo of a delete = create the same task again through the same endpoint
    due = time.time() + 600
    rid = _mk(client, "deleted by mistake", due)
    client.delete(f"/api/reminders/{rid}")
    back = client.post("/api/reminders", json={"text": "deleted by mistake", "due": due}).json()
    rows = client.get("/api/reminders?include_done=1").json()["reminders"]
    assert [r["id"] for r in rows] == [back["id"]] and rows[0]["text"] == "deleted by mistake"
