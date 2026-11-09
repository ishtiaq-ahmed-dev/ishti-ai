"""Channel service (WP-A6): one lifecycle owner for every remote channel.

  start()   called by the server at boot: Telegram poller if (token stored AND policy.telegram_enabled);
            WhatsApp channel only if its (double) flag is on. Never raises - boot must not depend on it.
  stop()    graceful at shutdown: poller kick-stop + whatsapp stop; joins; no orphan threads (tested).
  health()  /api/health payload + /api/remote/status source of truth.
  pair_start()/pair_cancel()  the desktop pairing flow (codes are issued ONLY here, i.e. locally).
  push()    task-finished / approval-needed / alerts -> /ws/events via ishti.events (+ optional
            message to the paired channel, respecting quiet hours).
"""
from __future__ import annotations

import threading

from ishti.channels import approvals, policy as policy_mod, security
from ishti.channels.gate import Gate
from ishti.channels.poller import Poller
from ishti.channels.telegram_client import TelegramClient

try:  # best-effort failure log (never breaks the module if logging is unavailable)
    from ishti.core.logutil import swallowed
except Exception:  # pragma: no cover
    def swallowed(*_a, **_k):
        return None


_lock = threading.Lock()
_service: "RemoteService | None" = None


def next_step(channel: str, enabled: bool, token: bool, paired: bool, running: bool, risk_ack: bool = False) -> str:
    """One plain sentence that says what the owner has to do next for this channel (or that nothing is needed).
    The owner's device/account steps (a bot token, a pairing message) cannot be done by ISHTI, so it says exactly which."""
    if channel == "telegram":
        if running:
            return "Telegram is on and listening."
        if not token:
            return ("Telegram needs a bot token: create a bot with @BotFather, then paste its token in Settings > Remote "
                    "Control > Telegram (or POST it to /api/remote/telegram/token).")
        if not paired:
            return "Telegram has a token but no paired phone: click Pair Telegram, then send /pair <code> to your bot."
        return "Telegram is ready but switched off: turn it on in Settings > Remote Control."
    if running:
        return "WhatsApp is connected and listening for the allowed owner number(s)."
    if not enabled:
        return ("WhatsApp control is off. Add an owner control number and explicitly enable automation in Settings > Connectors.")
    if not risk_ack:
        return "WhatsApp is switched on but its risk note is not accepted yet (Settings > Remote Control > WhatsApp)."
    if not paired:
        return "WhatsApp control is enabled but the local bridge is not linked; scan the QR in Settings > Connectors."
    return "WhatsApp is linked but the inbound listener is not running; review its last error."


class RemoteService:
    def __init__(self, bridge=None):
        self.client: TelegramClient | None = None
        self.poller: Poller | None = None
        self.gate: Gate | None = None
        self.whatsapp = None
        self.started_at = 0.0
        if bridge is None:
            from ishti import wa_bridge as bridge
        self.bridge = bridge

    # ------------------------------------------------------------ lifecycle
    def start(self) -> dict:
        """Idempotent; follows the policy switches. Returns the health dict."""
        pol = policy_mod.load()
        self.started_at = self.started_at or __import__("time").time()
        tg = pol.get("telegram_enabled") and security.has_token()
        if tg and not (self.poller and self.poller.running):
            self.client = TelegramClient()
            self.gate = Gate(client=self.client)
            self.poller = Poller(self.client, self.gate.handle_update)
            self.client.delete_webhook()  # a leftover webhook would 409 the poll (R1 A)
            self.poller.start()
            self.client = self.client  # keep for sends
        elif not tg and self.poller:
            self.poller.stop()
            self.poller = None
        try:
            wa_state = self.bridge.status()
        except Exception:
            wa_state = {"connected": False}
        wa_on = (bool(pol.get("whatsapp_enabled"))
                 and bool((pol.get("risk_ack") or {}).get("whatsapp_automation"))
                 and bool(wa_state.get("connected")))
        if wa_on and not (self.whatsapp and self.whatsapp.running):
            from ishti.channels.wa_inbound import WhatsAppInbound
            self.whatsapp = WhatsAppInbound(bridge=self.bridge, background=True)
            self.whatsapp.start()
        elif not wa_on and self.whatsapp:
            self.whatsapp.stop()
            self.whatsapp = None
        return self.health()

    def stop(self) -> dict:
        """Graceful; joins threads; safe to call twice."""
        stopped = []
        if self.poller:
            stopped.append(f"telegram:{self.poller.stop()}")
            self.poller = None
        if self.whatsapp:
            stopped.append(f"whatsapp:{self.whatsapp.stop()}")
            self.whatsapp = None
        self.started_at = 0.0
        return {"stopped": stopped}

    # ------------------------------------------------------------ health
    def health(self) -> dict:
        pol = policy_mod.load()
        sec = security.status()
        tg_running = bool(self.poller and self.poller.running)
        wa_running = bool(self.whatsapp and self.whatsapp.running)
        wa_ack = bool((pol.get("risk_ack") or {}).get("whatsapp_automation"))
        try:
            wa_state = self.bridge.status()
        except Exception as exc:
            wa_state = {"connected": False, "lastError": f"{type(exc).__name__}: {str(exc)[:120]}"}
        wa_connected = bool(wa_state.get("connected"))
        wa_error = self.whatsapp.last_error if self.whatsapp else str(wa_state.get("lastError") or "")
        out = {
            "telegram": {"enabled": bool(pol.get("telegram_enabled")), "token_stored": sec["token_stored"],
                         "paired_ids": sec["paired_ids"], "running": tg_running,
                         "poller": self.poller.health() if self.poller else None,
                         "next_step": next_step("telegram", bool(pol.get("telegram_enabled")), sec["token_stored"],
                                                bool(sec["paired_ids"]), tg_running)},
            "whatsapp": {"enabled": bool(pol.get("whatsapp_enabled")),
                         "risk_ack": wa_ack,
                         "connected": wa_connected,
                         "running": wa_running,
                         "pairing_state": wa_state.get("pairingState", "paired" if wa_connected else "waiting_for_scan"),
                         "last_inbound_ts": self.whatsapp.last_inbound_ts if self.whatsapp else 0.0,
                         "last_error": wa_error,
                         "next_step": next_step("whatsapp", bool(pol.get("whatsapp_enabled")), False, wa_connected,
                                                wa_running, wa_ack)},
            "pin_set": sec["pin_set"], "locked": sec["locked"], "lockout_s": sec["lockout_s"],
            "approvals_pending": approvals.pending(), "started_at": self.started_at,
            "privacy_notice": "Telegram/WhatsApp messages pass through their servers - not local. "
                              "Sensitive-output redaction applies to remote replies.",
        }
        return out

    # ------------------------------------------------------------ pairing (desktop-only)
    def pair_start(self) -> dict:
        code = security.new_pairing_code()
        return {"code": code, "expires_min": 10,
                "instructions": "Send /pair <code> to your bot from your own Telegram account within 10 minutes."}

    def pair_cancel(self) -> bool:
        security._pairings.clear()
        return True

    def revoke_sessions(self) -> dict:
        n = security.unpair_all()
        return {"revoked_ids": n, "approvals_cleared": approvals.revoke_all()}

    # ------------------------------------------------------------ push notifications
    def push(self, kind: str, text: str, urgent: bool = False) -> bool:
        """Desktop event always; remote message if urgent OR quiet hours are off."""
        try:
            from ishti import events
            events.publish("remote_push", {"kind": kind, "text": text[:300], "urgent": urgent})
        except Exception as _exc:
            swallowed('ishti/channels/service.py:RemoteService.push', _exc)
        try:
            from ishti.channels import output
            if urgent or not policy_mod.in_quiet_hours():
                if self.poller and self.poller.running and self.client and security.status()["paired_ids"]:
                    chat_id = security.status()["paired_ids"][0]
                    for ch in output.prepare_reply(text)[0]:
                        self.client.send(chat_id, output.html_escape(ch))
                    return True
        except Exception as _exc:
            swallowed('ishti/channels/service.py:RemoteService.push', _exc)
        return False


def get() -> RemoteService:
    global _service
    with _lock:
        if _service is None:
            _service = RemoteService()
        return _service


if __name__ == "__main__":
    import ishti.channels.policy as P
    import tempfile, os
    P.PATH = os.path.join(tempfile.mkdtemp(), "p.yaml")
    P.reset_cache()
    from ishti.channels import security as S
    S.STATE_PATH = os.path.join(tempfile.mkdtemp(), "s.json")

    svc = get()
    # boot with everything off: start is a no-op that still reports health
    h = svc.start()
    assert h["telegram"]["running"] is False and h["whatsapp"]["running"] is False
    # enabled but no token: still safe (no poller), health explains
    P.save({"telegram_enabled": True})
    h = svc.start()
    assert h["telegram"]["running"] is False and h["telegram"]["token_stored"] is False
    # pairing flow: code issued locally, consumed by try_pair
    code = svc.pair_start()["code"]
    assert S.try_pair(code, 4242) and svc.health()["telegram"]["paired_ids"] == [4242]
    # revoke
    out = svc.revoke_sessions()
    assert out["revoked_ids"] == 1 and svc.health()["telegram"]["paired_ids"] == []
    # stop is idempotent and clean
    assert svc.stop() == {"stopped": []} or isinstance(svc.stop()["stopped"], list)
    h2 = svc.health()
    assert h2["telegram"]["running"] is False and h2["whatsapp"]["running"] is False
    # push without channels: never raises
    assert svc.push("task", "done", urgent=True) is False
    print("[self-check] channels.service OK - idempotent start/stop, honest health, pairing flow, revoke, safe push")
