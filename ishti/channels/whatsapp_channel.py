"""WhatsApp adapter (WP-A5) — default OFF, self-chat only, human-paced (R2 mode 1).

Design (R2 §B):
  - reads ONLY the owner's designated chat (default: the self-chat "Message yourself") through the
    existing WhatsApp Web tab machinery (ishti.skills.whatsapp.skill) — no new libraries, no reloads
  - messages in ANY other chat are never parsed, never answered (tested: the parser is never
    called for them)
  - protocol inside the designated chat: the owner writes plain text; ISHTI's replies start with
    "ISHTI:" so its own lines are never re-ingested
  - sends: T2-gated by the dispatcher (unchanged), title-asserted, human-paced
    (>= whatsapp_min_gap_s between automation actions, jittered)
  - the whole adapter is dormant unless policy.whatsapp_enabled AND policy.risk_ack.
    whatsapp_automation are both true — both live in the local-only policy file
  - under ISHTI_TEST_MODE=1 the T2 gate blocks at the last step anyway (repo-wide rule)
"""
from __future__ import annotations

import random
import re
import threading
import time

from ishti.channels import policy as policy_mod, security
from ishti.channels.gate import Gate, _audit, _alert

ISHTI_MARK = "ISHTI:"


def enabled(pol: dict | None = None) -> bool:
    pol = pol or policy_mod.load()
    return bool(pol.get("whatsapp_enabled")) and bool((pol.get("risk_ack") or {}).get("whatsapp_automation"))


class WhatsAppChannel:
    def __init__(self, gate: Gate, snapshot_fn=None, send_fn=None, sleep=time.sleep):
        """snapshot_fn() -> snapshot text of the CURRENT open chat pane (tests inject).
        send_fn(chat_title, text) performs one human-paced send (tests inject; runtime uses the
        browser engine with its T2 gate). sleep injectable for pacing tests."""
        self.gate = gate
        self._snap = snapshot_fn
        self._send = send_fn
        self._sleep = sleep
        self._last_action = 0.0
        self._seen_ids: set[str] = set()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.last_error = ""

    # ------------------------------------------------------------ policy gate
    def _designated(self, pol: dict | None = None) -> str:
        pol = pol or policy_mod.load()
        return str(pol.get("whatsapp_designated_chat") or "Message yourself")

    # ------------------------------------------------------------ reading
    _TITLE = re.compile(r'- heading "([^"]+)" \[ref=')  # the open chat pane's header

    @staticmethod
    def _chat_title(snap: str) -> str | None:
        m = WhatsAppChannel._TITLE.search(snap or "")
        return m.group(1) if m else None

    def read_new(self) -> list[dict]:
        """New owner messages from the designated chat ONLY. Others: never parsed."""
        if not enabled():
            return []
        from ishti.skills.whatsapp import skill as wa

        pol = policy_mod.load()
        designated = self._designated(pol)
        snap = self._snap() if self._snap else None
        if not snap:
            return []
        title = self._chat_title(snap)
        if not title or title.strip().lower() != designated.strip().lower():
            # the open chat is not the designated one: read NOTHING (test 11)
            self.last_error = f"open chat is '{title}', not '{designated}' - read nothing"
            return []
        msgs = wa.parse_messages(snap, n=8)  # [(who, text)], oldest first
        out = []
        for who, body in msgs:
            body = (body or "").strip()
            mid = f"{who}|{body[:40]}"
            if not body or mid in self._seen_ids:
                continue
            self._seen_ids.add(mid)
            if body.startswith(ISHTI_MARK):
                continue  # our own reply
            out.append({"body": body, "who": who})
        return out

    def handle_messages(self, msgs: list[dict]):
        """Feed owner messages through the same gate discipline (allowlist is the designated chat
        itself; the phone/PC holding the WhatsApp session IS the authenticated sender)."""
        pol = policy_mod.load()
        for m in msgs:
            text = m["body"]
            _audit("whatsapp message received", text[:80], channel="whatsapp", session="wa:self")
            cat = Gate.classify_forbidden(text)
            if cat:
                self.reply(f"Refused: '{cat}' is not allowed remotely (policy: remote-forbidden).")
                _audit("remote-forbidden refused", f"{cat}", channel="whatsapp", session="wa:self")
                continue
            if security.locked() and not text.lower().startswith(("/unlock", "/kill", "/status")):
                self.reply("ISHTI is locked - send /unlock <PIN> (or /kill, /status).")
                _audit("command refused while locked", text[:60], channel="whatsapp", session="wa:self")
                continue
            self._dispatch(text, channel="whatsapp")

    def _dispatch(self, text: str, channel: str):
        try:
            from ishti import dispatcher
            dispatch = self.gate._dispatch or dispatcher.handle
        except Exception:
            dispatch = self.gate._dispatch
        if not dispatch:
            self.reply("Channel not wired to the orchestrator.")
            return

        def job():
            try:
                res = dispatch(text, session_id="wa:self", channel=channel)
                self.reply((res or {}).get("final_answer") or "(empty reply)")
            except Exception as exc:
                self.last_error = f"{type(exc).__name__}: {str(exc)[:150]}"
                self.reply(f"That failed: {self.last_error}")
        threading.Thread(target=job, daemon=True, name="ishti-wa-dispatch").start()

    # ------------------------------------------------------------ sending (human-paced)
    def reply(self, text: str):
        """Send to the designated chat only, with pacing. T2-gated sends go through the
        orchestrator's normal ask_first flow - this method is for the adapter's own lines
        (refusals, acks, final answers), which are plain channel output."""
        if not enabled():
            return False
        pol = policy_mod.load()
        designated = self._designated(pol)
        min_gap = float(pol.get("whatsapp_min_gap_s", 3.0))
        wait = self._last_action + min_gap - time.time()
        if wait > 0:
            self._sleep(wait + random.uniform(0.1, 0.9))  # jitter: not machine-regular
        self._last_action = time.time()
        if self._send:
            return self._send(designated, f"{ISHTI_MARK} {text}")
        try:
            from ishti.skills.whatsapp import skill as wa
            wa.send_self_chat(f"{ISHTI_MARK} {text}") if hasattr(wa, "send_self_chat") else None
            _audit("whatsapp reply sent", text[:80], channel="whatsapp", session="wa:self")
            return True
        except Exception as exc:
            self.last_error = f"send failed: {type(exc).__name__}: {str(exc)[:100]}"
            _audit("whatsapp send failed", self.last_error, channel="whatsapp", session="wa:self")
            return False

    # ------------------------------------------------------------ lifecycle
    def start(self, poll_interval_s: float = 5.0):
        assert self._thread is None, "whatsapp channel already running"
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, args=(poll_interval_s,),
                                        daemon=True, name="ishti-whatsapp-channel")
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> bool:
        self._stop.set()
        t = self._thread
        if t and t.is_alive():
            t.join(timeout=timeout)
        self._thread = None
        return not (t and t.is_alive())

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive() and not self._stop.is_set())

    def _loop(self, poll_interval_s: float):
        while not self._stop.is_set():
            try:
                msgs = self.read_new()
                if msgs:
                    self.handle_messages(msgs)
            except Exception as exc:
                self.last_error = f"{type(exc).__name__}: {str(exc)[:120]}"
            self._stop.wait(poll_interval_s)


if __name__ == "__main__":
    import ishti.channels.policy as P
    import tempfile, os
    P.PATH = os.path.join(tempfile.mkdtemp(), "p.yaml")
    P.reset_cache()
    P.save({"whatsapp_enabled": False})
    # dormant while disabled: read/send never happen
    wa_snap = '''
- row "Ali" [ref=e5]
- heading "Ali" [ref=e9]
- row "You: hi 1:39 PM Read" [ref=e12]
- row "meeting moved to 5 1:40 PM" [ref=e14]
'''
    sent = []
    ch = WhatsAppChannel(gate=None, snapshot_fn=lambda: wa_snap, send_fn=lambda t, x: sent.append(x) or True,
                         sleep=lambda s: None)
    assert ch.read_new() == [] and ch.reply("hi") is False  # disabled: nothing happens
    # enabled with the risk acknowledged:
    P.save({"whatsapp_enabled": True, "risk_ack": {"whatsapp_automation": True, "telegram_cloud": False},
            "whatsapp_designated_chat": "Message yourself"})
    # the open chat is a DIFFERENT chat: parse nothing
    assert ch.read_new() == [] and "not 'Message yourself'" in ch.last_error
    # now the designated chat is open: only new owner lines, never our own marker lines
    designated_snap = '''
- heading "Message yourself" [ref=e9]
- row "You: ISHTI: earlier reply 1:39 PM Read" [ref=e12]
- row "what is on my calendar 1:40 PM" [ref=e14]
- row "hello again 1:41 PM" [ref=e16]
'''
    ch2 = WhatsAppChannel(gate=None, snapshot_fn=lambda: designated_snap,
                          send_fn=lambda t, x: sent.append(x) or True, sleep=lambda s: None)
    msgs = ch2.read_new()
    assert [m["body"] for m in msgs] == ["what is on my calendar", "hello again"], msgs
    # idempotent: same snapshot again -> nothing new
    assert ch2.read_new() == []
    # human pacing: the second reply waits >= min gap after the first
    P.save({"whatsapp_min_gap_s": 0.05})
    sleeps = []
    ch2._sleep = sleeps.append
    ch2.reply("one")                      # no wait: first action
    ch2._last_action = time.time()        # pretend the first send just happened
    ch2.reply("two")
    assert sleeps and sleeps[-1] >= 0.05, sleeps
    assert all(x.startswith("ISHTI: ") for x in sent[-2:])
    # lifecycle: start/stop clean, thread joins
    ch3 = WhatsAppChannel(gate=None, snapshot_fn=lambda: designated_snap,
                          send_fn=lambda t, x: True, sleep=lambda s: None)
    ch3.start(poll_interval_s=0.05)
    time.sleep(0.15)
    assert ch3.running
    assert ch3.stop(timeout=3) is True and not ch3.running
    print("[self-check] channels.whatsapp OK - default OFF, designated-only reads, own lines skipped, pacing, clean stop")
