"""Notifications (ishti/notifications.py + the bell API in interface/web_ui/server.py), offline: a temp facts DB,
no models, no sockets. The store backs the bell popover and the Notifications tab."""
import os

import pytest
from fastapi.testclient import TestClient

from ishti import events, facts, notifications


@pytest.fixture
def store(tmp_path, monkeypatch):
    f = facts.Facts(os.path.join(str(tmp_path), "t.db"))
    monkeypatch.setattr(facts, "_facts", f)
    return f


def test_add_recent_order_and_read_state(store):
    a = notifications.add("reminder", "Reminder: call Ali", body="14:20")
    b = notifications.add("download", "Model downloaded", body="ishti-qwen2.5:14b")
    assert a and b and b["id"] > a["id"] and a["read"] is False
    rows = notifications.recent()
    assert [r["id"] for r in rows] == [b["id"], a["id"]], rows          # newest first
    assert notifications.unread() == 2
    assert notifications.get(a["id"])["title"] == "Reminder: call Ali"
    assert notifications.mark_read(a["id"]) == 1
    assert notifications.unread() == 1
    assert notifications.mark_read() == 0                               # mark all
    assert all(r["read"] for r in notifications.recent())


def test_bad_input_never_raises(store):
    assert notifications.add("nonsense", "x")["kind"] == "alert"        # unknown kind falls back
    assert notifications.add("alert", "   ") is None                    # empty title ignored
    assert notifications.get(99999) is None
    assert len(notifications.recent(limit=-3)) <= 1                     # clamped, never raises
    assert notifications.mark_read("junk") == notifications.unread()


def test_add_publishes_on_the_event_bus(store):
    got = []

    def listen(m):
        got.append(m)

    events.subscribe(listen)
    try:
        row = notifications.add("job", "Background job finished")
    finally:
        events.unsubscribe(listen)
    assert [m["type"] for m in got] == ["notification"]
    assert got[0]["data"]["id"] == row["id"] and got[0]["data"]["title"] == "Background job finished"


def test_api_list_and_mark_read(store):
    from interface.web_ui import server
    client = TestClient(server.app, base_url="http://127.0.0.1", client=("127.0.0.1", 50000))
    a = notifications.add("alert", "ISHTI update ready")
    notifications.add("error", "Request failed: demo")
    r = client.get("/api/notifications")
    assert r.status_code == 200
    body = r.json()
    assert body["unread"] == 2 and len(body["notifications"]) == 2
    assert body["notifications"][0]["title"] == "Request failed: demo"  # newest first
    r = client.post("/api/notifications/read", json={"id": a["id"]})
    assert r.status_code == 200 and r.json()["unread"] == 1
    r = client.post("/api/notifications/read", json={})
    assert r.status_code == 200 and r.json()["unread"] == 0 and r.json()["marked"] == "all"
    assert client.get("/api/notifications").json()["unread"] == 0


if __name__ == "__main__":
    import tempfile

    import ishti.facts as F
    F._facts = F.Facts(os.path.join(tempfile.mkdtemp(), "t.db"))
    test_add_recent_order_and_read_state(None)
    test_bad_input_never_raises(None)
    test_add_publishes_on_the_event_bus(None)
    test_api_list_and_mark_read(None)
    print("[self-check] notifications tests OK")
