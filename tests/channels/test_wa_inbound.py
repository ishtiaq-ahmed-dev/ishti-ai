from __future__ import annotations

import time

import yaml

from ishti.channels import policy
from ishti.channels import wa_inbound as mod


CHAT = "923001234567@s.whatsapp.net"
POLICY = {
    **policy.DEFAULTS,
    "whatsapp_enabled": True,
    "whatsapp_control_numbers": ["+923001234567"],
    "risk_ack": {"telegram_cloud": False, "whatsapp_automation": True},
}


class FakeBridge:
    def __init__(self, rows=None, me="923001234567:7@s.whatsapp.net"):
        self.rows = list(rows or [])
        self.me = me
        self.sent = []

    def status(self):
        return {"connected": True, "me": self.me}

    def inbox_since(self, cursor):
        return [row for row in self.rows if row["observedAt"] > cursor]

    def send(self, chat, text, *, action_id):
        result = {"id": f"OUT{len(self.sent) + 1}", "state": "submitted"}
        self.sent.append({"chat": chat, "text": text, "action_id": action_id, **result})
        return result


def row(mid, text="hello", *, sender=CHAT, chat=CHAT, from_me=False, observed=1000,
        ts=None, kind="text", **extra):
    return {"id": mid, "text": text, "senderJid": sender, "chat": chat, "fromMe": from_me,
            "observedAt": observed, "ts": ts if ts is not None else int(time.time() * 1000),
            "kind": kind, **extra}


def inbound(tmp_path, bridge, dispatch=None, **kwargs):
    return mod.WhatsAppInbound(bridge=bridge, dispatch=dispatch or (lambda text, **_: {"final_answer": f"answer:{text}"}),
                               policy_loader=lambda: POLICY, cursor_path=tmp_path / "cursor.json",
                               initial_cursor_ms=0, alert=kwargs.pop("alert", lambda *_: None),
                               allow_message=kwargs.pop("allow_message", lambda _sender: True), **kwargs)


def test_allowed_number_dispatches_and_stranger_is_silently_ignored(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "_audit", lambda *_a, **_k: None)
    calls, alerts = [], []
    bridge = FakeBridge([row("OK", sender="923001234567@s.whatsapp.net"),
                         # same 10 national digits, another country: the old suffix match let this through
                         row("SPOOF", sender="443001234567@s.whatsapp.net", chat="443001234567@s.whatsapp.net", observed=1001),
                         row("NO", sender="15550001111@s.whatsapp.net", observed=1002)])
    poller = inbound(tmp_path, bridge, lambda text, **kw: calls.append((text, kw)) or {"final_answer": "done"},
                     alert=lambda *args: alerts.append(args))
    poller.poll_once()
    assert calls == [("hello", {"session_id": "wa:923001234567", "channel": "whatsapp"})]
    assert len(bridge.sent) == 1 and bridge.sent[0]["chat"] == CHAT
    assert [a[1] for a in alerts] == ["WhatsApp sender ending 567", "WhatsApp sender ending 111"]  # spoof + stranger


def test_own_reply_is_not_reingested_but_phone_typed_self_chat_works(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "_audit", lambda *_a, **_k: None)
    calls = []
    bridge = FakeBridge([row("MARK", "ISHTI: old reply", from_me=True),
                         row("SELF", "status please", from_me=True, observed=1001)])
    poller = inbound(tmp_path, bridge, lambda text, **kw: calls.append((text, kw)) or {"final_answer": "ok"})
    poller.poll_once()
    assert calls == [("status please", {"session_id": "wa:923001234567", "channel": "whatsapp"})]
    assert len(bridge.sent) == 1


def test_lid_uses_phone_mapping_for_allowlist(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "_audit", lambda *_a, **_k: None)
    calls = []
    bridge = FakeBridge([row("LID", sender="923001234567@s.whatsapp.net",
                             chat="123456789012345@lid", senderLid="123456789012345@lid")])
    inbound(tmp_path, bridge, lambda text, **kw: calls.append(kw) or {"final_answer": "ok"}).poll_once()
    assert calls == [{"session_id": "wa:923001234567", "channel": "whatsapp"}]
    assert bridge.sent[0]["chat"].endswith("@lid")


def test_stale_message_gets_notice_once_and_is_not_dispatched(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "_audit", lambda *_a, **_k: None)
    bridge = FakeBridge([row("OLD", ts=int((time.time() - 600) * 1000))])
    calls = []
    poller = inbound(tmp_path, bridge, lambda *_a, **_k: calls.append(True))
    poller.poll_once(); poller.poll_once()
    assert not calls and len(bridge.sent) == 1 and "stale" in bridge.sent[0]["text"].lower()


def test_rate_limit_and_locked_state_refuse_before_dispatch(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "_audit", lambda *_a, **_k: None)
    limited = FakeBridge([row("RATE")])
    inbound(tmp_path / "rate", limited, allow_message=lambda _sender: False).poll_once()
    assert "Rate limit" in limited.sent[0]["text"]

    monkeypatch.setattr(mod.security, "locked", lambda: True)
    locked = FakeBridge([row("LOCK", observed=2000)])
    inbound(tmp_path / "lock", locked).poll_once()
    assert "locked" in locked.sent[0]["text"].lower()


def test_forbidden_request_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "_audit", lambda *_a, **_k: None)
    bridge = FakeBridge([row("FORBID", "delete my files in Downloads")])
    inbound(tmp_path, bridge).poll_once()
    assert "Refused" in bridge.sent[0]["text"] and "delete_files" in bridge.sent[0]["text"]


def test_t2_yes_no_returns_to_same_dispatch_session(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "_audit", lambda *_a, **_k: None)
    calls = []
    bridge = FakeBridge([row("ASK", "send it"), row("YES", "YES", observed=1001)])

    def dispatch(text, **kw):
        calls.append((text, kw["session_id"]))
        return {"final_answer": "Proceed? (yes/no)" if text == "send it" else "approved"}

    inbound(tmp_path, bridge, dispatch).poll_once()
    assert calls == [("send it", "wa:923001234567"), ("yes", "wa:923001234567")]
    assert len(bridge.sent) == 2


def test_cursor_persists_across_restart(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "_audit", lambda *_a, **_k: None)
    calls = []
    first = FakeBridge([row("ONE")])
    inbound(tmp_path, first, lambda text, **_: calls.append(text) or {"final_answer": "ok"}).poll_once()
    second = FakeBridge([row("ONE"), row("TWO", "second", observed=1001)])
    mod.WhatsAppInbound(bridge=second, dispatch=lambda text, **_: calls.append(text) or {"final_answer": "ok"},
                        policy_loader=lambda: POLICY, cursor_path=tmp_path / "cursor.json",
                        alert=lambda *_: None, allow_message=lambda _sender: True).poll_once()
    assert calls == ["hello", "second"]


def test_policy_migrates_formatted_phone_strings_out_of_telegram_ids(tmp_path, monkeypatch):
    target = tmp_path / "remote_policy.yaml"
    target.write_text(yaml.safe_dump({"allowed_telegram_ids": [12345, "+92 300-1234567"]}), encoding="utf-8")
    monkeypatch.setattr(policy, "PATH", str(target))
    policy.reset_cache()
    try:
        loaded = policy.load(force=True)
        assert loaded["allowed_telegram_ids"] == [12345]
        assert loaded["whatsapp_control_numbers"] == ["+923001234567"]
        assert "+92 300-1234567" not in yaml.safe_load(target.read_text(encoding="utf-8"))["allowed_telegram_ids"]
    finally:
        policy.reset_cache()


def test_long_run_does_not_block_kill_or_status_in_background_mode(tmp_path, monkeypatch):
    """Owner sends a slow request, then /status: the poll loop must answer /status while the run is still busy."""
    import threading
    monkeypatch.setattr(mod, "_audit", lambda *_a, **_k: None)
    release = threading.Event()
    bridge = FakeBridge([row("SLOW", "do something long"), row("ST", "/status", observed=1001)])

    def slow(text, **_):
        release.wait(5)
        return {"final_answer": "finished"}

    poller = inbound(tmp_path, bridge, slow, background=True)
    poller.poll_once()                       # returns although the first request is still running
    deadline = time.time() + 3
    while time.time() < deadline and not any("status" in m["text"].lower() for m in bridge.sent):
        time.sleep(0.05)
    assert any("ISHTI remote status" in m["text"] for m in bridge.sent), bridge.sent
    assert not any("finished" in m["text"] for m in bridge.sent)   # the slow run has not answered yet
    release.set()
    deadline = time.time() + 3
    while time.time() < deadline and not any("finished" in m["text"] for m in bridge.sent):
        time.sleep(0.05)
    assert any("finished" in m["text"] for m in bridge.sent)
    poller.stop()
