"""Owner-only WhatsApp inbound control over the local Baileys bridge."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import threading
import time
import uuid

from ishti.channels import policy as policy_mod, security
from ishti.channels.gate import Gate, _alert, _audit
from ishti.channels.output import chunk, sensitive_filter

try:
    from ishti.test_mode import scratch_path
    _DATA = Path(scratch_path(str(Path(__file__).resolve().parents[2] / "data" / "wa_bridge")))
except Exception:  # pragma: no cover
    _DATA = Path(__file__).resolve().parents[2] / "data" / "wa_bridge"

CURSOR_PATH = _DATA / "inbound_cursor.json"
ISHTI_MARK = "ISHTI:"


def digits(value: str) -> str:
    return re.sub(r"\D", "", str(value or "").split("@", 1)[0].split(":", 1)[0])


def numbers_match(left: str, right: str) -> bool:
    """Match international/local spellings without ever treating a LID as a phone number."""
    a, b = digits(left), digits(right)
    if len(a) < 10 or len(b) < 10:
        return False
    return any(a[-size:] == b[-size:] for size in range(min(12, len(a), len(b)), 9, -1))


def _phone_candidates(row: dict) -> list[str]:
    candidates = []
    for key in ("senderJid", "senderPn", "participantPn", "participant", "chat"):
        value = str(row.get(key) or "")
        if value and "@lid" not in value and "@g.us" not in value:
            candidates.append(value)
    return candidates


class WhatsAppInbound:
    def __init__(self, *, bridge=None, dispatch=None, policy_loader=None, cursor_path=None,
                 clock=time.time, alert=None, allow_message=None, initial_cursor_ms=None):
        if bridge is None:
            from ishti import wa_bridge as bridge
        self.bridge = bridge
        self.dispatch = dispatch
        self.policy_loader = policy_loader or policy_mod.load
        self.cursor_path = Path(cursor_path or CURSOR_PATH)
        self.clock = clock
        self.alert = alert or _alert
        self.allow_message = allow_message or (lambda sender: security.limiter_for(sender).allow())
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._sent_ids: set[str] = set()
        self._pending: set[str] = set()
        self.last_error = ""
        self.last_inbound_ts = 0.0
        self._cursor, self._seen = self._load_cursor(initial_cursor_ms)

    def _load_cursor(self, initial_cursor_ms):
        try:
            raw = json.loads(self.cursor_path.read_text(encoding="utf-8"))
            return int(raw.get("ms", 0)), set(str(x) for x in raw.get("ids", [])[-500:])
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            start = int(self.clock() * 1000) if initial_cursor_ms is None else int(initial_cursor_ms)
            return start, set()

    def _save_cursor(self):
        self.cursor_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.cursor_path.with_suffix(f".{uuid.uuid4().hex}.tmp")
        try:
            tmp.write_text(json.dumps({"ms": self._cursor, "ids": sorted(self._seen)[-500:]}), encoding="utf-8")
            os.replace(tmp, self.cursor_path)
        finally:
            tmp.unlink(missing_ok=True)

    def _advance(self, row: dict):
        self._cursor = max(self._cursor, int(row.get("observedAt") or 0))
        mid = str(row.get("id") or "")
        if mid:
            self._seen.add(mid)
            if len(self._seen) > 500:
                self._seen = set(sorted(self._seen)[-500:])
        self._save_cursor()

    def _allowed_sender(self, row: dict, pol: dict, linked: str) -> str:
        controls = [str(x) for x in pol.get("whatsapp_control_numbers", [])]
        explicit = []
        for key in ("senderJid", "senderPn", "participantPn", "participant"):
            value = str(row.get(key) or "")
            if value and "@lid" not in value and "@g.us" not in value:
                explicit.append(value)
        candidates = explicit or _phone_candidates(row)
        sender = next((digits(c) for c in candidates if any(numbers_match(c, n) for n in controls)), "")
        if not sender:
            return ""
        if row.get("fromMe"):
            # fromMe is accepted only for the linked account's own self-chat. API replies are
            # rejected earlier by message ID/marker, so a phone-typed self-chat command still works.
            if not linked or not numbers_match(sender, linked):
                return ""
            chat_phone = next((c for c in _phone_candidates(row) if numbers_match(c, linked)), "")
            if not chat_phone:
                return ""
        return sender

    def _send(self, chat: str, text: str):
        filtered, _ = sensitive_filter(str(text or ""))
        results = []
        for part in chunk(filtered, 3500) or ["(empty reply)"]:
            result = self.bridge.send(chat, f"{ISHTI_MARK} {part}", action_id=f"wa_inbound_{uuid.uuid4().hex}")
            if isinstance(result, dict) and result.get("id"):
                self._sent_ids.add(str(result["id"]))
                if len(self._sent_ids) > 1000:
                    self._sent_ids = set(sorted(self._sent_ids)[-1000:])
            results.append(result)
        return results

    def _command(self, chat: str, text: str) -> bool:
        low = text.strip().lower()
        if security.locked() and not low.startswith(("/kill", "/unlock", "/status", "/help")):
            self._send(chat, "ISHTI is locked. I refuse everything except /kill, /unlock <PIN>, /status and /help.")
            return True
        if low.startswith("/kill"):
            from ishti import killswitch
            stopped = killswitch.kill("remote WhatsApp /kill")
            self._send(chat, f"Kill switch: everything stopped ({'; '.join(stopped) or 'nothing was running'}).")
        elif low.startswith("/lock"):
            security.set_locked(True)
            self._send(chat, "Locked. Use /unlock <PIN>, /kill, /status or /help.")
        elif low.startswith("/unlock"):
            pin = text.split()[-1] if len(text.split()) > 1 else ""
            if security.lockout_remaining() > 0:
                self._send(chat, "Too many wrong PINs. Try again after the lockout.")
            elif security.check_pin(pin):
                security.set_locked(False)
                self._send(chat, "Unlocked.")
            else:
                security.register_pin_failure()
                self._send(chat, "Wrong PIN.")
        elif low.startswith("/status"):
            self._send(chat, Gate._status_text())
        elif low.startswith("/tasks"):
            self._send(chat, Gate._tasks_text())
        elif low.startswith("/help"):
            self._send(chat, "Commands: /status /tasks /kill /lock /unlock <PIN> /help. Otherwise, ask naturally.")
        elif low.startswith("/"):
            self._send(chat, "Unknown command. Send /help for the supported commands.")
        else:
            return False
        return True

    def _handle(self, row: dict, pol: dict, linked: str):
        chat, mid = str(row.get("chat") or ""), str(row.get("id") or "")
        text = str(row.get("text") or "").strip()
        if not chat or chat.endswith("@g.us") or mid in self._sent_ids or text.startswith(ISHTI_MARK):
            return
        sender = self._allowed_sender(row, pol, linked)
        if not sender:
            if not row.get("fromMe"):
                self.alert("remote_unknown_sender", f"WhatsApp sender ending {digits(_phone_candidates(row)[0])[-3:] if _phone_candidates(row) else '???'}")
            return
        self.last_inbound_ts = self.clock()
        session = f"wa:{sender}"
        _audit("whatsapp message received", f"message={mid[-12:]}", channel="whatsapp", session=session)
        age = self.clock() - float(row.get("ts") or self.clock() * 1000) / 1000
        if age > float(pol.get("stale_command_max_age_s", 300)):
            self._send(chat, "That message is stale, so I did not run it. Please resend if it still applies.")
            return
        if not self.allow_message(sender):
            self._send(chat, "Rate limit reached. Retry in a few seconds.")
            return
        if row.get("kind") != "text":
            self._send(chat, "I can read text only here for now.")
            return
        if not text:
            return
        if self._command(chat, text):
            return
        cat = Gate.classify_forbidden(text)
        if cat:
            self._send(chat, f"Refused: '{cat}' is not allowed remotely. Ask on the PC, where it is gated normally.")
            return
        answer_text = text
        if session in self._pending and text.strip().casefold() in {"yes", "y", "no", "n"}:
            answer_text = "yes" if text.strip().casefold() in {"yes", "y"} else "no"
            self._pending.discard(session)
        dispatch = self.dispatch
        if dispatch is None:
            from ishti import dispatcher
            dispatch = dispatcher.handle
        result = dispatch(answer_text, session_id=session, channel="whatsapp")
        answer = (result or {}).get("final_answer") if isinstance(result, dict) else str(result or "")
        answer = answer or "(empty reply)"
        if answer.rstrip().endswith("(yes/no)"):
            self._pending.add(session)
        self._send(chat, answer)

    def poll_once(self) -> int:
        pol = self.policy_loader()
        state = self.bridge.status()
        linked = digits(state.get("me") or "")
        rows = sorted(self.bridge.inbox_since(max(0, self._cursor - 1)),
                      key=lambda r: (int(r.get("observedAt") or 0), str(r.get("id") or "")))
        handled = 0
        for row in rows:
            if str(row.get("id") or "") in self._seen:
                continue
            try:
                self._handle(row, pol, linked)
                handled += 1
            except Exception as exc:
                self.last_error = f"{type(exc).__name__}: {str(exc)[:160]}"
                _audit("whatsapp inbound failed", self.last_error, channel="whatsapp")
            finally:
                self._advance(row)
        return handled

    def start(self, poll_interval_s: float = 2.0):
        if self.running:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, args=(poll_interval_s,), daemon=True,
                                        name="ishti-wa-inbound")
        self._thread.start()

    def _loop(self, poll_interval_s: float):
        while not self._stop.is_set():
            try:
                self.poll_once()
            except Exception as exc:
                self.last_error = f"{type(exc).__name__}: {str(exc)[:160]}"
            self._stop.wait(poll_interval_s)

    def stop(self, timeout: float = 5.0) -> bool:
        self._stop.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout)
        self._thread = None
        return not (thread and thread.is_alive())

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive() and not self._stop.is_set())
