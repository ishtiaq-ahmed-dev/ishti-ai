"""Mock Telegram Bot API for offline acceptance tests (WP-C1; master prompt: "mock Telegram Bot
API server + a real test bot once the owner provides a token").

Implements exactly the methods ISHTI's client uses, over httpx.MockTransport, with:
  - a scripted update inbox (push_update) that getUpdates pops (long-poll returns instantly)
  - recorded outbound messages/sends/callbacks (assert what ISHTI sent, never the real world)
  - answerCallbackQuery semantics: a second answer to the same query id returns ok=False (replay)
  - editable messages (editMessageText) recorded with the new text
Everything runs in-process; no sockets, no network, no real Telegram.
"""
from __future__ import annotations

import json
import threading
import time

import httpx

from ishti.channels.telegram_client import TelegramClient, BotAPIError


class MockTelegram:
    def __init__(self, token="123456:TESTTOKEN000000000000000000000"):
        self.token = token
        self._inbox: list[dict] = []          # updates waiting for getUpdates
        self._lock = threading.Lock()
        self.sent: list[dict] = []            # every sendMessage
        self.edits: list[dict] = []           # every editMessageText
        self.docs: list[dict] = []            # every sendDocument
        self.photos: list[dict] = []
        self.callback_answers: list[dict] = []
        self.answered: set[str] = set()       # callback ids already answered (replay guard)
        self._next_update_id = 1000
        self._next_message_id = 5000
        self.webhook_set = False              # deleteWebhook clears this; getUpdates 409s while set

    # ------------------------------------------------------------- test-side API
    def push_update(self, *, chat_id=111, sender_id=111, text="", chat_type="private",
                    date=None, forward_origin=None, message_id=None) -> int:
        """Queue one incoming update; returns its update_id."""
        with self._lock:
            uid = self._next_update_id
            self._next_update_id += 1
            mid = message_id or self._next_message_id
            self._next_message_id += 1
            msg = {"message_id": mid, "date": int(date if date is not None else time.time()),
                   "chat": {"id": chat_id, "type": chat_type},
                   "from": {"id": sender_id, "first_name": "Test"}}
            if text:
                msg["text"] = text
            if forward_origin:
                msg["forward_origin"] = forward_origin
            self._inbox.append({"update_id": uid, "message": msg})
            return uid

    def push_callback(self, *, data, chat_id=111, sender_id=111, message_id=5000) -> int:
        with self._lock:
            uid = self._next_update_id
            self._next_update_id += 1
            self._inbox.append({"update_id": uid, "callback_query": {
                "id": f"cb{uid}", "from": {"id": sender_id}, "data": data,
                "message": {"message_id": message_id, "chat": {"id": chat_id}}}})
            return uid

    def push_voice(self, *, file_id="voicefile", seconds=2, chat_id=111, sender_id=111) -> int:
        with self._lock:
            uid = self._next_update_id
            self._next_update_id += 1
            self._inbox.append({"update_id": uid, "message": {
                "message_id": self._next_message_id, "date": int(time.time()),
                "chat": {"id": chat_id, "type": "private"}, "from": {"id": sender_id},
                "voice": {"file_id": file_id, "duration": seconds, "file_size": 12000}}})
            self._next_message_id += 1
            return uid

    # ------------------------------------------------------------- assertions helpers
    def sent_texts(self, chat_id=None) -> list[str]:
        return [m["text"] for m in self.sent if chat_id is None or m["chat_id"] == chat_id]

    def clear(self):
        with self._lock:
            self._inbox.clear()

    # ------------------------------------------------------------- the transport
    def client(self) -> TelegramClient:
        return TelegramClient(self.token, transport=httpx.MockTransport(self._handler))

    def _handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/getUpdates"):
            if self.webhook_set:
                return httpx.Response(409, json={"ok": False,
                                                 "description": "Conflict: can't use getUpdates method while webhook is active"})
            body = json.loads(request.content or b"{}")
            with self._lock:
                updates = list(self._inbox)
                self._inbox.clear()
            return httpx.Response(200, json={"ok": True, "result": updates})
        if path.endswith("/deleteWebhook"):
            self.webhook_set = False
            return httpx.Response(200, json={"ok": True, "result": True})
        if path.endswith("/setWebhook"):
            self.webhook_set = True
            return httpx.Response(200, json={"ok": True, "result": True})
        if path.endswith("/getMe"):
            return httpx.Response(200, json={"ok": True, "result": {"id": 42, "username": "ishti_test_bot"}})
        if path.endswith("/sendMessage"):
            body = json.loads(request.content or b"{}")
            with self._lock:
                mid = self._next_message_id
                self._next_message_id += 1
            rec = {"chat_id": body.get("chat_id"), "text": body.get("text"),
                   "reply_markup": body.get("reply_markup"), "message_id": mid}
            self.sent.append(rec)
            return httpx.Response(200, json={"ok": True, "result": {"message_id": mid, "chat": {"id": body.get("chat_id")}}})
        if path.endswith("/editMessageText"):
            body = json.loads(request.content or b"{}")
            self.edits.append(body)
            return httpx.Response(200, json={"ok": True, "result": {"message_id": body.get("message_id")}})
        if path.endswith("/answerCallbackQuery"):
            body = json.loads(request.content or b"{}")
            cid = body.get("callback_query_id")
            if cid in self.answered:
                return httpx.Response(400, json={"ok": False,
                                                 "description": "Bad Request: query is too old and response timeout expired or query id is invalid"})
            self.answered.add(cid)
            self.callback_answers.append(body)
            return httpx.Response(200, json={"ok": True, "result": True})
        if path.endswith("/sendDocument"):
            self.docs.append({"chat_id": request.url.params.get("chat_id"),
                              "file": request.read()[:24]})
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})
        if path.endswith("/sendPhoto"):
            self.photos.append({"chat_id": request.url.params.get("chat_id")})
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})
        if path.endswith("/getFile"):
            return httpx.Response(200, json={"ok": True, "result": {"file_id": "x", "file_path": "voice/file.ogg"}})
        if "/file/bot" in path:
            return httpx.Response(200, content=b"RIFF0000WAVEfmt data")
        if path.endswith("/badToken"):
            return httpx.Response(404, json={"ok": False, "description": "Not Found"})
        return httpx.Response(200, json={"ok": True, "result": {}})


if __name__ == "__main__":
    m = MockTelegram()
    c = m.client()
    uid = m.push_update(text="hello")
    ups = c.get_updates(None, timeout_s=0)
    assert ups[0]["update_id"] == uid
    assert c.send(111, "hi")["message_id"] == m._next_message_id - 1
    m.sent[0]["text"] == "hi"
    # callback replay: second answer gets ok=False -> answer_callback returns False
    assert c.answer_callback("cbX", "first") is True
    assert c.answer_callback("cbX", "second") is False
    # webhook conflict surfaces as 409
    m.webhook_set = True
    try:
        c.get_updates(None, timeout_s=0)
        raise SystemExit("expected 409")
    except BotAPIError as e:
        assert e.code == 409
    m.webhook_set = False
    print("[self-check] tests.mock_telegram OK - inbox, recording, callback replay, 409 webhook")
