"""WAHA transport (ishti/wa_bridge.py) against a local fake WAHA server: no Docker, no WhatsApp, no network beyond
loopback. Covers auth, mapping, exact-only chat resolution, idempotent sends and media integrity."""
import base64
import hashlib
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, parse_qs, unquote

import pytest

from ishti import wa_bridge as bridge


class FakeWaha:
    """Programmable WAHA: routes[(method, path)] -> (status, json body or bytes)."""

    def __init__(self):
        self.routes, self.seen = {}, []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def _serve(self, method):
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"null") if length else None
                parts = urlsplit(self.path)
                outer.seen.append({"method": method, "path": unquote(parts.path), "query": parse_qs(parts.query),
                                   "key": self.headers.get("X-Api-Key"), "body": body})
                status, payload = outer.routes.get((method, unquote(parts.path)), (404, {"error": "not found"}))
                raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self):
                self._serve("GET")

            def do_POST(self):
                self._serve("POST")

            def log_message(self, *_a):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def calls(self, method, path):
        return [s for s in self.seen if s["method"] == method and s["path"] == path]


@pytest.fixture
def waha(monkeypatch, tmp_path):
    fake = FakeWaha()
    monkeypatch.delenv("ISHTI_TEST_MODE", raising=False)
    monkeypatch.setattr(bridge, "BASE", fake.url)
    monkeypatch.setattr(bridge, "DATA", str(tmp_path))
    monkeypatch.setattr(bridge, "LEDGER", tmp_path / "actions.json")
    monkeypatch.setattr(bridge, "TEST_DESTINATION_FILE", tmp_path / "test_destination")
    monkeypatch.setattr(bridge, "_api_key", lambda create=False: "synthetic-waha-key")
    monkeypatch.setattr(bridge, "_lids", {})
    yield fake
    fake.server.shutdown()


def test_live_operations_disabled_in_test_mode(monkeypatch):
    monkeypatch.setenv("ISHTI_TEST_MODE", "1")
    for call in (lambda: bridge.send("x@c.us", "hi", action_id="a"), bridge.start, lambda: bridge.mark_read("x@c.us"),
                 lambda: bridge.authorize_self_test("923001234567")):
        with pytest.raises(bridge.BridgeError, match="test mode"):
            call()
    assert bridge.status() == {"connected": False, "running": False}


def test_closed_port_is_not_running_and_costs_no_http(monkeypatch):
    monkeypatch.delenv("ISHTI_TEST_MODE", raising=False)
    monkeypatch.setattr(bridge, "BASE", "http://127.0.0.1:1")
    assert bridge.status() == {"connected": False, "running": False}


def test_status_maps_session_and_sends_the_api_key(waha):
    waha.routes[("GET", "/api/sessions/default")] = (200, {"status": "WORKING", "me": {"id": "923007654321@c.us"}})
    st = bridge.status()
    assert st["connected"] and st["me"] == "923007654321@c.us" and st["pairingState"] == "WORKING"
    assert waha.seen[-1]["key"] == "synthetic-waha-key"
    waha.routes[("GET", "/api/sessions/default")] = (200, {"status": "SCAN_QR_CODE"})
    waha.routes[("GET", "/api/default/auth/qr")] = (200, {"mimetype": "image/png", "data": "iVBORw0KGgo="})
    qr = bridge.get_qr_data()
    assert qr["qr_image"] == "data:image/png;base64,iVBORw0KGgo=" and not qr["connected"] and qr["raw_qr"] is None


def test_unauthorized_key_is_a_clear_error(waha):
    waha.routes[("GET", "/api/default/chats/overview")] = (401, {"error": "bad key"})
    with pytest.raises(bridge.BridgeError, match="refused ISHTI's API key"):
        bridge.chats()


def test_chats_and_messages_are_mapped_oldest_first_with_lid_resolution(waha):
    waha.routes[("GET", "/api/default/chats/overview")] = (200, [
        {"id": "923001111111@c.us", "name": "Ali", "lastMessage": {"body": "hi", "timestamp": 200, "fromMe": False}},
        {"id": "555@lid", "name": "Sara", "lastMessage": {"body": "ok", "timestamp": 100, "fromMe": True}}])
    waha.routes[("GET", "/api/default/lids/555@lid")] = (200, {"lid": "555@lid", "pn": "923002222222@c.us"})
    chats = bridge.chats()
    assert [c["id"] for c in chats] == ["923001111111@c.us", "923002222222@c.us"]
    waha.routes[("GET", "/api/default/chats/923001111111@c.us/messages")] = (200, [
        {"id": "B", "body": "second", "timestamp": 20, "fromMe": True, "source": "api", "ackName": "DEVICE"},
        {"id": {"_serialized": "A"}, "body": "first", "timestamp": 10, "fromMe": False, "from": "923001111111@c.us"},
        {"id": "C", "body": "", "timestamp": 30, "hasMedia": True, "media": {"mimetype": "application/pdf"},
         "_data": {"isViewOnce": True}}])
    rows = bridge.messages("923001111111@s.whatsapp.net", 10, since_ms=5000)
    assert [r["id"] for r in rows] == ["A", "B", "C"]
    assert rows[0]["senderJid"] == "923001111111@c.us" and rows[1]["viaApi"] and rows[1]["delivery"] == "delivered"
    assert rows[2]["kind"] == "document" and rows[2]["restricted"] is True
    q = waha.calls("GET", "/api/default/chats/923001111111@c.us/messages")[-1]["query"]
    assert q["filter.timestamp.gte"] == ["5"] and q["downloadMedia"] == ["false"]


def test_find_chat_is_exact_or_nothing(waha):
    waha.routes[("GET", "/api/default/chats/overview")] = (200, [
        {"id": "1@c.us", "name": "Ali Khan"}, {"id": "2@c.us", "name": "ali khan"}, {"id": "3@c.us", "name": "Zara"}])
    waha.routes[("GET", "/api/contacts/all")] = (200, [])
    assert bridge.find_chat("+92 300 123 4567") == "923001234567@c.us"
    assert bridge.find_chat("Zara") == "3@c.us"
    assert bridge.find_chat("Ali Khan") == ""  # two chats share the name: never a guess
    assert bridge.find_chat("Za") == "" and bridge.find_chat("") == ""


def test_send_is_idempotent_per_action_id_and_never_replays(waha):
    waha.routes[("POST", "/api/sendText")] = (201, {"id": "MSG1", "ackName": "SERVER"})
    first = bridge.send("923001234567@s.whatsapp.net", "hello", action_id="act-1")
    assert first["id"] == "MSG1" and first["delivery"] == "server_ack" and first["chat"] == "923001234567@c.us"
    again = bridge.send("923001234567@c.us", "hello", action_id="act-1")
    assert again["replayed"] and len(waha.calls("POST", "/api/sendText")) == 1
    body = waha.calls("POST", "/api/sendText")[0]["body"]
    assert body == {"session": "default", "chatId": "923001234567@c.us", "text": "hello"}


def test_uncertain_send_is_recorded_and_not_retried(waha):
    waha.routes[("POST", "/api/sendText")] = (500, {"error": "boom"})
    with pytest.raises(bridge.BridgeError):
        bridge.send("923001234567@c.us", "hello", action_id="act-2")
    waha.routes[("POST", "/api/sendText")] = (201, {"id": "LATE"})
    out = bridge.send("923001234567@c.us", "hello", action_id="act-2")
    assert out["replayed"] and out["state"] == "uncertain" and len(waha.calls("POST", "/api/sendText")) == 1


def test_attachment_integrity_and_voice_format(waha, tmp_path):
    photo = tmp_path / "p.png"
    photo.write_bytes(b"\x89PNG fake image bytes")
    with pytest.raises(bridge.BridgeError, match="changed after approval"):
        bridge.send("1@c.us", media=str(photo), expected_hash="0" * 64, action_id="m1")
    wav = tmp_path / "note.wav"
    wav.write_bytes(b"RIFF....WAVE")
    with pytest.raises(bridge.BridgeError, match="Ogg Opus"):
        bridge.send("1@c.us", media=str(wav), media_kind="voice", action_id="m2")
    waha.routes[("POST", "/api/sendImage")] = (201, {"id": "IMG"})
    digest = hashlib.sha256(photo.read_bytes()).hexdigest()
    out = bridge.send("1@c.us", media=str(photo), caption="look", expected_hash=digest, action_id="m3")
    sent = waha.calls("POST", "/api/sendImage")[0]["body"]
    assert out["id"] == "IMG" and sent["caption"] == "look" and base64.b64decode(sent["file"]["data"]) == photo.read_bytes()


def test_media_is_fetched_only_from_this_waha_instance(waha, monkeypatch):
    waha.routes[("GET", "/api/default/chats/1@c.us/messages/M1")] = (200, {
        "id": "M1", "timestamp": 1, "hasMedia": True,
        "media": {"mimetype": "image/png", "url": "http://evil.example/api/files/default/M1.png"}})
    waha.routes[("GET", "/api/files/default/M1.png")] = (200, b"\x89PNG data")
    monkeypatch.setattr(bridge, "_read_worker", lambda kind, data: {"text": f"{kind}:{len(data)}"})
    out = bridge.read_media("1@c.us", "M1")
    assert out["text"] == "image:9" and out["trust"] == "untrusted_message_content"
    assert waha.calls("GET", "/api/files/default/M1.png")  # fetched from OUR base, not evil.example
    waha.routes[("GET", "/api/default/chats/1@c.us/messages/M2")] = (200, {
        "id": "M2", "timestamp": 1, "hasMedia": True, "media": {"mimetype": "image/png", "url": "http://x/etc/passwd"}})
    with pytest.raises(bridge.BridgeError, match="no longer available"):
        bridge.read_media("1@c.us", "M2")


def test_authorize_self_test_requires_the_linked_account(waha):
    waha.routes[("GET", "/api/sessions/default")] = (200, {"status": "WORKING", "me": {"id": "923007654321@c.us"}})
    with pytest.raises(bridge.BridgeError, match="must be the account"):
        bridge.authorize_self_test("+923001234567")
    result = bridge.authorize_self_test("+92 300 765 4321")
    assert result["destination"] == "923007654321" and bridge.account_matches_self_test() is True


def test_deferred_mutations_never_call_network(waha):
    assert bridge.delete("x", "y")["ok"] is False and bridge.edit() == bridge.delete() and bridge.react()["ok"] is False
    assert waha.seen == []
