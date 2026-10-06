import asyncio

from interface.web_ui import server


class SlowSocket:
    async def send_text(self, _message):
        await asyncio.sleep(1)


def test_websocket_send_has_timeout(monkeypatch):
    monkeypatch.setattr(server, "WS_SEND_TIMEOUT_S", 0.01)
    with_timeout = server._send_websocket(SlowSocket(), "event")
    try:
        asyncio.run(with_timeout)
    except TimeoutError:
        pass
    else:
        raise AssertionError("slow websocket send was not bounded")
