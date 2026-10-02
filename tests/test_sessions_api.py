"""Chat management: rename, select/delete with undo, and local share/export (orchestrator/memory/conversation.py +
the /api/sessions* endpoints in interface/web_ui/server.py). Offline: a temp sessions folder, no models, no network."""
import os

import pytest
from fastapi.testclient import TestClient

from ishti import files_api
from orchestrator.memory.conversation import ConversationManager


@pytest.fixture
def api(tmp_path, monkeypatch):
    conv = ConversationManager(str(tmp_path / "sessions"))
    first = conv.new_chat("Old title")
    conv.add_message(first, "user", "hello there")
    conv.add_message(first, "ishti", "hi")
    second = conv.new_chat("Second chat")
    conv.add_message(second, "user", "another one")
    from interface.web_ui import server
    monkeypatch.setattr(server, "get_conversation_manager", lambda: conv)
    monkeypatch.setattr(files_api, "allowed_roots", lambda: [str(tmp_path)])
    client = TestClient(server.app, base_url="http://127.0.0.1", client=("127.0.0.1", 50000))
    return client, conv, first, second, tmp_path


def test_rename_chat(api):
    client, conv, first, _second, _tmp = api
    r = client.post(f"/api/sessions/{first}/rename", json={"title": "  Trip   plan  "})
    assert r.status_code == 200 and r.json()["title"] == "Trip plan"
    assert conv.load_session(first)["title"] == "Trip plan"          # persisted on disk
    assert client.post(f"/api/sessions/{first}/rename", json={"title": "   "}).status_code == 400
    assert client.post("/api/sessions/sess_missing/rename", json={"title": "x"}).status_code == 400


def test_delete_selected_goes_to_trash_and_undo_restores(api):
    client, conv, first, second, _tmp = api
    r = client.post("/api/sessions/delete", json={"ids": [first, second, "../../evil"]})
    assert r.status_code == 200 and r.json()["deleted"] == [first, second]   # unsafe id ignored
    assert {first, second}.isdisjoint({s["session_id"] for s in conv.list_sessions()})
    assert not os.path.exists(os.path.join(conv.sessions_dir, f"{first}.json"))
    token = r.json()["undo_token"]
    r2 = client.post("/api/sessions/undo", json={"undo_token": token})
    assert r2.status_code == 200 and r2.json()["restored"] == 2
    ids = {s["session_id"] for s in conv.list_sessions()}
    assert {first, second} <= ids


def test_export_markdown_writes_the_file_the_owner_chose(api):
    client, _conv, first, _second, tmp = api
    r = client.post("/api/sessions/export", json={"ids": [first], "format": "md", "dir": str(tmp), "name": "my chat.md"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] and body["chats"] == 1 and body["format"] == "md"
    assert os.path.dirname(body["path"]) == str(tmp) and os.path.basename(body["path"]) == "my chat.md"
    text = open(body["path"], encoding="utf-8").read()
    assert "# ISHTI chats" in text and "hello there" in text and "hi" in text and "made on this PC" in text


def test_export_refuses_a_folder_outside_the_allowed_roots(api):
    client, _conv, first, _second, _tmp = api
    outside = os.path.abspath(os.sep)
    r = client.post("/api/sessions/export", json={"ids": [first], "format": "md", "dir": outside})
    assert r.status_code == 403 and "only save" in r.json()["error"]
    assert client.post("/api/sessions/export", json={"ids": [], "format": "md", "dir": str(_tmp)}).status_code == 400


if __name__ == "__main__":
    import sys

    sys.exit(pytest.main([__file__, "-q"]))
