"""Exercise queue cancellation with an in-process MCP peer, never Chrome or a service."""
import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from ishti import mcp_client


class FakeSession:
    def __init__(self):
        self.started = threading.Event()
        self.exited = threading.Event()
        self.release = asyncio.Event()
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        self.exited.set()

    async def initialize(self):
        self.thread = threading.current_thread()

    async def list_tools(self):
        return SimpleNamespace(tools=[])

    async def call_tool(self, tool, args):
        self.calls.append((tool, args))
        if tool == "blocking":
            self.started.set()
            await self.release.wait()
        if tool == "server_timeout":
            raise TimeoutError("server budget exhausted")
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=tool)], isError=False)


@pytest.fixture
def fake_hub(tmp_path, monkeypatch):
    session = FakeSession()

    @asynccontextmanager
    async def stdio(*_args, **_kwargs):
        yield None, None

    monkeypatch.setattr(mcp_client, "TOKENS_PATH", str(tmp_path / "tokens.json"))
    monkeypatch.setattr(mcp_client, "stdio_client", stdio)
    monkeypatch.setattr(mcp_client, "ClientSession", lambda *_args: session)
    hub = mcp_client.MCPHub({"fake": {"command": "unused-fake-command"}})
    hub.tools("fake")
    try:
        yield hub, session
    finally:
        hub.loop.call_soon_threadsafe(session.release.set)
        hub.close("fake")
        assert session.exited.wait(2), "fake MCP session did not close"
        hub.loop.call_soon_threadsafe(hub.loop.stop)
        session.thread.join(timeout=2)
        assert not session.thread.is_alive()
        hub.loop.close()


def test_timed_out_queued_mutation_never_executes_and_queue_remains_healthy(fake_hub):
    hub, session = fake_hub
    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(hub.call, "fake", "blocking", timeout=5)
        try:
            assert session.started.wait(2)
            with pytest.raises(mcp_client.MCPCallTimeout, match="cancelled before execution") as caught:
                hub.call("fake", "send_message", {"text": "synthetic"}, timeout=0.02)
            assert caught.value.started is False
            assert caught.value.outcome_unknown is False
        finally:
            hub.loop.call_soon_threadsafe(session.release.set)
        assert first.result(2) == "blocking"
    assert hub.call("fake", "read_status", timeout=2) == "read_status"
    assert [tool for tool, _ in session.calls] == ["blocking", "read_status"]


def test_started_call_timeout_reports_unknown_outcome_without_retry(fake_hub):
    hub, session = fake_hub
    with pytest.raises(mcp_client.MCPCallTimeout, match="outcome unknown") as caught:
        hub.call("fake", "blocking", timeout=0.05)
    assert session.started.is_set()
    assert caught.value.started is True
    assert caught.value.outcome_unknown is True
    hub.loop.call_soon_threadsafe(session.release.set)
    assert hub.call("fake", "read_status", timeout=2) == "read_status"
    assert [tool for tool, _ in session.calls] == ["blocking", "read_status"]


def test_remote_timeout_error_is_preserved_and_next_call_still_works(fake_hub):
    hub, session = fake_hub
    with pytest.raises(TimeoutError, match="server budget exhausted") as caught:
        hub.call("fake", "server_timeout", timeout=2)
    assert not isinstance(caught.value, mcp_client.MCPCallTimeout)
    assert hub.call("fake", "read_status", timeout=2) == "read_status"
    assert len(session.calls) == 2
