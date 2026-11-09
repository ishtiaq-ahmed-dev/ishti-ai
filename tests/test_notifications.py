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


def test_system_skill_reports_what_fired_and_is_honest_when_empty(store):
    """Checklist #195/#196: the dialogue path reads the real store - and says "nothing" when empty."""
    from ishti.skills.system import skill as sys_skill
    assert sys_skill.match("What notifications did I miss in the last hour?")
    assert sys_skill.match("Tell me the alert now")
    assert not sys_skill.wants_notifications("Turn off notifications for an hour")
    assert not sys_skill.wants_notifications("Set an alert for 5pm")
    assert not sys_skill.wants_notifications("Check my Facebook notifications")
    empty = sys_skill.run("What notifications did I miss in the last hour?", {})
    assert "no notifications" in empty["reply"].lower() and "last hour" in empty["reply"].lower()
    notifications.add("reminder", "Reminder: call Ali", body="14:20")
    got = sys_skill.run("What notifications did I miss in the last hour?", {})
    assert "call ali" in got["reply"].lower() and "14:20" in got["reply"]
    assert "1 unread" in got["reply"]


def test_notification_questions_are_not_chat_digest_questions(store):
    """The digest's "what did I miss" branch must stand down for the bell (it used to steal #195)."""
    from ishti.skills.chat_digest import skill as digest
    assert not digest.match("What notifications did I miss in the last hour?")
    assert digest.match("what did I miss in my group chats")


if __name__ == "__main__":
    import tempfile

    import ishti.facts as F
    F._facts = F.Facts(os.path.join(tempfile.mkdtemp(), "t.db"))
    test_add_recent_order_and_read_state(None)
    test_bad_input_never_raises(None)
    test_add_publishes_on_the_event_bus(None)
    test_api_list_and_mark_read(None)
    print("[self-check] notifications tests OK")


def test_scheduled_job_question_waits_in_the_bell_and_is_answered_in_its_own_session(store, monkeypatch):
    from interface.web_ui import server
    seen = []

    def fake_handle(text, session_id, channel):
        seen.append((text, session_id, channel))
        if text == "yes":
            return {"final_answer": "Sent."}
        return {"final_answer": "Shall I send the weekly report to Sara? (yes/no)"}
    monkeypatch.setattr(server, "ishti_handle", fake_handle)
    assert server.run_scheduled("every friday email the weekly report to Sara") is None  # nothing spoken
    note = notifications.recent()[0]
    sid = note["meta"]["session"]
    assert note["meta"]["answerable"] and sid.startswith("scheduled-") and ":" not in sid
    assert server.run_scheduled("summarise my downloads") is None
    assert notifications.recent()[0]["meta"]["session"] != sid  # each job has its own session
    client = TestClient(server.app, base_url="http://127.0.0.1", client=("127.0.0.1", 50000))
    assert client.post(f"/api/notifications/{note['id']}/answer", json={"answer": "maybe"}).status_code == 400
    r = client.post(f"/api/notifications/{note['id']}/answer", json={"answer": "yes"})
    assert r.status_code == 200 and r.json()["reply"] == "Sent." and seen[-1] == ("yes", sid, "scheduled")
    plain = notifications.add("job", "Brief ready")
    assert client.post(f"/api/notifications/{plain['id']}/answer", json={"answer": "yes"}).status_code == 404


def test_scheduled_job_failure_is_reported_not_swallowed(store, monkeypatch):
    from interface.web_ui import server
    monkeypatch.setattr(server, "ishti_handle", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("network down")))
    assert server.run_scheduled("give me my brief") is None
    row = notifications.recent()[0]
    assert row["kind"] == "error" and "give me my brief" in row["title"] and "network down" in row["body"]


def test_quiet_hours_understands_the_saved_string_and_the_old_dict():
    import datetime as dt
    from ishti.channels.policy import in_quiet_hours
    at = lambda h, m=0: dt.datetime(2026, 11, 9, h, m)
    assert in_quiet_hours("22:00-07:00", at(23)) and in_quiet_hours("22:00-07:00", at(6, 59))
    assert not in_quiet_hours("22:00-07:00", at(7)) and not in_quiet_hours("22:00-07:00", at(12))
    assert in_quiet_hours({"start": 22, "end": 7}, at(1)) and not in_quiet_hours({"start": 22, "end": 7}, at(9))
    assert in_quiet_hours("13:00-14:30", at(14, 0)) and not in_quiet_hours("", at(3)) and not in_quiet_hours("junk", at(3))
