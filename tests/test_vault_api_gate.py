import asyncio
import time
from types import SimpleNamespace

from interface.web_ui import server


class Body:
    def __init__(self, value):
        self.value = value
        self.headers = {"content-type": "application/json"}

    async def json(self):
        return self.value


def test_vault_purge_requires_exact_one_use_approval(monkeypatch):
    server._VAULT_APPROVALS.clear()
    calls = []
    vault = SimpleNamespace(purge_all_media=lambda: calls.append("all") or 2,
                            purge_chat_media=lambda target: calls.append(target) or 1)
    monkeypatch.setattr("ishti.security.media_vault.purge_all_media", vault.purge_all_media)
    monkeypatch.setattr("ishti.security.media_vault.purge_chat_media", vault.purge_chat_media)
    denied = asyncio.run(server.vault_purge(Body({})))
    assert denied.status_code == 409 and calls == []
    approval = asyncio.run(server.vault_purge_prepare(Body({})))
    result = asyncio.run(server.vault_purge(Body({"approval_token": approval["approval_token"]})))
    assert result == {"ok": True, "purged": 2} and calls == ["all"]
    replay = asyncio.run(server.vault_purge(Body({"approval_token": approval["approval_token"]})))
    assert replay.status_code == 409 and calls == ["all"]


def test_vault_purge_approval_binds_chat_and_expires(monkeypatch):
    server._VAULT_APPROVALS.clear()
    calls = []
    monkeypatch.setattr("ishti.security.media_vault.purge_chat_media", lambda target: calls.append(target) or 1)
    approval = asyncio.run(server.vault_purge_prepare(Body({"chat_id": "chat-a"})))
    wrong = asyncio.run(server.vault_purge(Body({"chat_id": "chat-b", "approval_token": approval["approval_token"]})))
    assert wrong.status_code == 409 and calls == []
    approval = asyncio.run(server.vault_purge_prepare(Body({"chat_id": "chat-a"})))
    server._VAULT_APPROVALS[approval["approval_token"]]["expires"] = time.monotonic() - 1
    expired = asyncio.run(server.vault_purge(Body({"chat_id": "chat-a", "approval_token": approval["approval_token"]})))
    assert expired.status_code == 409 and calls == []
