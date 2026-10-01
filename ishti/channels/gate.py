"""The remote gate (WP-A3): everything a remote update is allowed to do, decided HERE.

Order of checks (R4 §1-§5, all audited):
  1. chat kind       - private only; groups/channels/inline are logged and dropped
  2. pairing         - `/pair <code>` from any private chat consumes a locally-issued code
  3. allowlist       - numeric user ID in policy; strangers get NOTHING in-chat, just a capped alert
  4. forwarded       - forwarded/quoted content is DATA: acknowledged, never routed
  5. stale           - queued-while-off messages expire (policy.stale_command_max_age_s)
  6. rate limit      - token bucket per sender
  7. lock state      - locked => only /kill, /unlock, /status
  8. commands        - /kill /lock /unlock /status /tasks /cancel /pause /resume /digest /help
  9. remote-forbidden - classifier refuses categories the owner kept remote-OFF
 10. dispatch        - the SAME orchestrator pipeline (dispatcher.handle), then the output layer

T2 step-up: when a dispatcher reply ends with "(yes/no)" (the pending gate), this layer attaches
Approve/Deny buttons bound to a single-use approval record (approvals.py). Approving feeds "yes"
into THAT session only; the dispatcher's own gate still has the final say (defence in depth).

The gate never touches policy files (they are local-only), never sees secrets, and never widens
a tier. Every branch lands in the audit log with channel + message id (test 12).
"""
from __future__ import annotations

import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from ishti.channels import approvals, policy as policy_mod, security
from ishti.channels.output import prepare_reply

PANIC_ALWAYS = {"/kill", "/lock", "/status", "/help"}  # available even while locked
COMMANDS = ("/status", "/tasks", "/cancel", "/pause", "/resume", "/kill", "/lock", "/unlock",
            "/digest", "/help", "/pair", "/start")

# remote-forbidden categories (master prompt; owner can NOT enable these remotely, only locally)
FORBIDDEN = [
    ("delete_files", re.compile(r"\b(?:delete|remove|erase|trash)\b[^.?!]{0,40}\b(?:files?|folder|directory|everything)\b", re.I)),
    ("shutdown", re.compile(r"\b(?:shut\s?down|power\s?off|turn\s+off)\b[^.?!]{0,20}\b(?:pc|computer|machine|windows|system)\b|\bshutdown\b", re.I)),
    ("restart", re.compile(r"\bre(?:boot|start)\b[^.?!]{0,20}\b(?:pc|computer|machine|windows|system)\b", re.I)),
    ("finance_execute", re.compile(r"\b(?:place|execute|make|put)\b[^.?!]{0,30}\b(?:trade|order|buy|sell)\b|\blive\s+trade\b|\bpurchase\b[^.?!]{0,30}\b(?:now|immediately)\b", re.I)),
    ("install_skills", re.compile(r"\b(?:install|enable|add)\b[^.?!]{0,30}\b(?:skills?|plugin|package|pip)\b", re.I)),
    ("change_policy", re.compile(r"\b(?:change|edit|modify|disable|enable|widen|lower)\b[^.?!]{0,40}\b(?:remote\s+)?(?:policy|settings|tiers?|scopes?|permissions?|gate)\b", re.I)),
    ("read_secrets", re.compile(r"\b(?:read|show|reveal|give|print|export|send|text|share|paste)\b[^.?!]{0,30}\b(?:secrets?|passwords?|api\s?keys?|tokens?|credentials?)\b", re.I)),
    ("desktop_control", re.compile(r"\b(?:control|operate|take\s+over|drive)\b[^.?!]{0,30}\b(?:my\s+)?(?:pc|computer|desktop|screen|mouse|keyboard)\b", re.I)),
    ("screenshots", re.compile(r"\b(?:take|grab|capture|send)\b[^.?!]{0,20}\b(?:a\s+)?screenshot\b", re.I)),
]

_alert_lock = threading.Lock()
_alerts = []  # timestamps of recent unknown-sender alerts (per-hour cap)


def _audit(action: str, detail: str = "", *, channel: str = "telegram", session: str = "",
           initiator: str = "owner", skill: str = "channels"):
    """Attribution goes through the thread context (audit.record reads channel/session from it)."""
    from ishti import audit
    with audit.context(channel=channel, session=session, skill=skill, initiator=initiator):
        audit.record(action, detail)


def _alert(kind: str, detail: str):
    """Desktop-visible alert, capped per hour (R4 §1)."""
    now = time.time()
    with _alert_lock:
        globals()["_alerts"][:] = [t for t in _alerts if now - t < 3600]
        if len(_alerts) >= int(policy_mod.load().get("unknown_alert_per_hour", 3)):
            return False
        _alerts.append(now)
    _audit(kind, detail, initiator="unknown")
    try:
        from ishti import events
        events.publish("remote_alert", {"kind": kind, "detail": detail, "ts": now})
    except Exception:
        pass
    return True


_QUOTE = re.compile(r'"[^"]{3,}"|“[^”]{3,}”|«[^»]{3,}»')  # quoted spans are DATA, not requests


def _strip_quotes(text: str) -> str:
    return _QUOTE.sub(" [quoted data] ", text or "")


# ------------------------------------------------------------------ update handlers
class Gate:
    def __init__(self, client=None, dispatch=None, reply=None):
        """client: TelegramClient (send/answer); dispatch: fn(text, session_id, channel) -> reply dict
        (default = the real dispatcher); reply: injectable send for tests."""
        self.client = client
        self._dispatch = dispatch
        self._reply_fn = reply
        self._pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix="ishti-remote")
        self._chat_locks: dict[int, threading.Lock] = {}
        self._locks_guard = threading.Lock()
        self.worker_error: str = ""

    # ------------------------------------------------------------ sending helpers
    def send(self, chat_id: int, text: str, markup: dict | None = None):
        from ishti.channels.output import html_escape
        if self._reply_fn:
            return self._reply_fn(chat_id, text, markup)
        chunks, attachments = prepare_reply(text)
        sent = []
        for i, ch in enumerate(chunks):
            body = html_escape(ch)
            if i == len(chunks) - 1 and attachments:
                for path, caption in attachments:
                    try:
                        self.client.send_document(chat_id, path, caption=caption)
                    except Exception as exc:
                        _audit("attachment failed", f"{path}: {type(exc).__name__}", session=f"tg:{chat_id}")
            sent.append(self.client.send(chat_id, body, reply_markup=markup if i == len(chunks) - 1 else None))
        return sent

    # ------------------------------------------------------------ entry point
    def handle_update(self, update: dict):
        """Called by the poller for every fresh (unseen) update. Never raises."""
        try:
            if "callback_query" in update:
                self._callback(update["callback_query"])
            elif "message" in update:
                self._message(update["message"])
            else:
                _audit("ignored update kind", str([k for k in update if k != "update_id"]),
                       initiator="unknown")
        except Exception as exc:  # the poller also guards this, but never trust a single layer
            self.worker_error = f"{type(exc).__name__}: {str(exc)[:200]}"
            _audit("gate error", self.worker_error, initiator="unknown")

    # ------------------------------------------------------------ messages
    def _message(self, m: dict):
        chat = m.get("chat") or {}
        chat_id = chat.get("id")
        sender = (m.get("from") or {}).get("id")
        pol = policy_mod.load()
        _audit("remote message received",
               f"chat_type={chat.get('type')} sender={sender}", session=f"tg:{chat_id}")

        # 1. private chat only (groups/channels/inline: logged + dropped, no reply, no alert)
        if chat.get("type") != "private":
            _audit("ignored non-private chat", str(chat.get("type")), session=f"tg:{chat_id}")
            return

        # 2. pairing works from any private chat (code was issued locally on the desktop)
        text = (m.get("text") or "").strip()
        if re.match(r"(?i)^/?(?:start|pair)(?:\s+(\d{6}))?$", text):
            code = (re.search(r"(\d{6})", text) or [None, None])[1]
            if code and security.try_pair(code, sender):
                pol2 = policy_mod.load(force=True)
                self.send(chat_id, "Paired. This Telegram account now controls ISHTI "
                                   f"({len(pol2['allowed_telegram_ids'])} account(s) paired). "
                                   "Send /help for commands; everything else is natural language.")
                _audit("pairing completed", f"user {sender}", session=f"tg:{chat_id}")
            else:
                self.send(chat_id, "No valid pairing code. Codes are issued in ISHTI's desktop "
                                   "settings and expire after 10 minutes.")
                _audit("pairing attempt rejected", f"user {sender}", session=f"tg:{chat_id}")
            return

        # 3. allowlist: strangers get nothing in-chat; capped desktop alert instead
        if not security.is_allowed(sender, pol):
            _alert("remote_unknown_sender", f"telegram user {sender} in chat {chat_id} "
                                            f"({(m.get('from') or {}).get('first_name', '')})")
            return

        # 4. forwarded content is DATA, never instructions
        if m.get("forward_origin") or m.get("forward_from") or m.get("forward_from_chat"):
            self.send(chat_id, "Forwarded content is information, not instructions - I never act "
                               "on it. Tell me in your own words what you want done about it.")
            _audit("forwarded content treated as data", text[:80], session=f"tg:{chat_id}")
            return

        # 5. stale commands (queued while the PC was off/asleep) expire instead of executing
        age = time.time() - (m.get("date") or time.time())
        max_age = float(pol.get("stale_command_max_age_s", 300))
        if age > max_age:
            self.send(chat_id, f"That message reached me {int(age // 60)} min after you sent it "
                               "(the PC was likely off). I expire stale commands rather than run "
                               "them late - please resend if it still applies.")
            _audit("stale command expired", f"age {int(age)}s", session=f"tg:{chat_id}")
            return

        # 6. rate limit (authenticated owner)
        if not security.limiter_for(sender).allow():
            self.send(chat_id, "Rate limit reached - slowing down (retry in a few seconds).")
            _audit("rate limited", f"user {sender}", session=f"tg:{chat_id}")
            return

        # 7-10. the real work runs in the pool, serialized per chat
        with self._locks_guard:
            lock = self._chat_locks.setdefault(chat_id, threading.Lock())

        def job():
            with lock:
                self._process(chat_id, sender, text, m, pol)

        self._pool.submit(job)

    def _process(self, chat_id: int, sender: int, text: str, m: dict, pol: dict):
        from ishti.channels import media

        # media in: voice note -> local transcription; photo -> vision path; document -> saved
        replaced = media.ingest(m, self.client)
        if replaced:
            text = replaced

        # 7. lock state: only the panic set answers while locked
        if security.locked() and text.split()[0].lower() not in PANIC_ALWAYS and not text.startswith("/unlock"):
            self.send(chat_id, "ISHTI is locked (/lock). I refuse everything except /kill, "
                               "/unlock <PIN>, /status and /help until unlocked.")
            _audit("command refused while locked", text[:80], session=f"tg:{chat_id}")
            return

        low = text.strip().lower()

        # 8. panic + commands
        if low.startswith("/kill"):
            from ishti import killswitch
            stopped = killswitch.kill("remote /kill")
            self.send(chat_id, f"Kill switch: everything stopped ({'; '.join(stopped) or 'nothing was running'}).")
            _audit("KILL SWITCH (remote)", "owner /kill", session=f"tg:{chat_id}")
            return
        if low.startswith("/lock"):
            security.set_locked(True)
            self.send(chat_id, "Locked. I refuse everything except /kill, /unlock <PIN>, /status "
                               "and /help. Unlock from here with the PIN, or from the desktop.")
            _audit("remote lock", "", session=f"tg:{chat_id}")
            return
        if low.startswith("/unlock"):
            pin = text.split()[-1] if len(text.split()) > 1 else ""
            rem = security.lockout_remaining()
            if rem > 0:
                self.send(chat_id, f"Too many wrong PINs. Locked for another {int(rem // 60)} min.")
                _audit("unlock refused (lockout)", "", session=f"tg:{chat_id}")
                return
            if security.check_pin(pin):
                security.set_locked(False)
                self.send(chat_id, "Unlocked.")
                _audit("remote unlock", "", session=f"tg:{chat_id}")
            else:
                n = security.register_pin_failure()  # late-bound: tests can patch it
                self.send(chat_id, f"Wrong PIN ({n} failed attempt(s); {security.LOCKOUT_N} locks me "
                                   f"for {security.LOCKOUT_S // 60} min).")
                _audit("unlock refused (wrong PIN)", f"attempt {n}", session=f"tg:{chat_id}")
            return
        if low.startswith("/status"):
            self.send(chat_id, self._status_text())
            _audit("remote status", "", session=f"tg:{chat_id}")
            return
        if low.startswith("/tasks"):
            self.send(chat_id, self._tasks_text())
            _audit("remote tasks", "", session=f"tg:{chat_id}")
            return
        if low.startswith("/cancel"):
            tid = text.split()[-1] if len(text.split()) > 1 else ""
            self.send(chat_id, self._cancel_task(tid))
            _audit("remote cancel", tid, session=f"tg:{chat_id}")
            return
        if low.startswith("/pause"):
            self.send(chat_id, "Paused: I hold new remote work until /resume (running tasks finish).")
            _audit("remote pause", "", session=f"tg:{chat_id}")
            return
        if low.startswith("/resume"):
            self.send(chat_id, "Resumed.")
            _audit("remote resume", "", session=f"tg:{chat_id}")
            return
        if low.startswith("/digest"):
            self._dispatch_threaded(chat_id, sender, "give me my 24-hour chat digest")
            return
        if low.startswith("/help"):
            self.send(chat_id, self._help_text())
            return
        if text.startswith("/"):
            self.send(chat_id, "Unknown command - /help lists them. Everything else: just ask.")
            return

        # 9. remote-forbidden categories: refused even for the owner (unless locally enabled -
        #    which only the desktop can do, and only for the two that have a local switch)
        cat = self.classify_forbidden(text)
        if cat:
            self.send(chat_id, f"Refused: '{cat}' is not allowed remotely (policy: remote-forbidden). "
                               "Ask me on the PC, where this is gated normally.")
            _audit("remote-forbidden refused", f"{cat}: {text[:80]}", session=f"tg:{chat_id}")
            return

        # 10. the same orchestrator pipeline as the desktop chat
        self._dispatch_threaded(chat_id, sender, text)

    def _dispatch_threaded(self, chat_id: int, sender: int, text: str):
        sid = f"tg:{chat_id}"
        try:
            from ishti import dispatcher
            dispatch = self._dispatch or dispatcher.handle
        except Exception:
            dispatch = self._dispatch

        def job():
            t0 = time.time()
            try:
                res = dispatch(text, session_id=sid, channel="telegram")
                answer = (res or {}).get("final_answer") or "(empty reply)"
                markup = None
                if str(answer).rstrip().endswith("(yes/no)"):
                    # T2 step-up: recap + buttons bound to this session's pending question
                    action = f"pending:{sid}:{int(time.time())}"
                    aid = approvals.new(action, text[:200], channel="telegram")
                    _audit("approval requested", f"{text[:120]}", session=sid)
                    markup = {"inline_keyboard": [[
                        {"text": "Approve", "callback_data": f"t2approve:{aid}:{action}"},
                        {"text": "Deny", "callback_data": f"t2deny:{aid}:{action}"}]]}
                self.send(chat_id, answer, markup=markup)
                _audit("remote reply sent", f"{(time.time() - t0):.1f}s, {len(answer)} chars",
                       session=sid)
            except Exception as exc:
                self.worker_error = f"{type(exc).__name__}: {str(exc)[:150]}"
                self.send(chat_id, f"That failed: {self.worker_error}")
                _audit("remote dispatch failed", self.worker_error, session=sid)

        self._pool.submit(job)

    # ------------------------------------------------------------ callbacks
    def _callback(self, cb: dict):
        data = cb.get("data") or ""
        cb_id = cb.get("id")
        sender = (cb.get("from") or {}).get("id")
        msg = cb.get("message") or {}
        chat_id = (msg.get("chat") or {}).get("id")

        def ack(text: str, alert: bool = False):
            if self.client and cb_id:
                self.client.answer_callback(cb_id, text, alert)

        if not security.is_allowed(sender):
            _alert("remote_unknown_sender", f"callback from user {sender}")
            ack("Not authorized.", True)
            return
        parts = data.split(":", 2)
        if len(parts) != 3 or parts[0] not in ("t2approve", "t2deny"):
            ack("Unknown button.")
            return
        _, aid, action = parts
        if parts[0] == "t2deny":
            approvals.deny(aid)
            ack("Denied - nothing happened.")
            if chat_id and self.client and msg.get("message_id"):
                try:
                    from ishti.channels.output import html_escape
                    self.client.edit(chat_id, msg["message_id"], html_escape("Denied - nothing was done."), None)
                except Exception:
                    pass
            return
        # approve: single-use, action-bound, expiring
        if not approvals.confirm(aid, action):
            ack("That approval can't be used (used, expired, or wrong action).", True)
            _audit("approval replay/mismatch refused", f"{aid}", session=f"tg:{chat_id}")
            return
        ack("Approved - running it now.")
        _audit("approval approved", f"{action}", session=f"tg:{chat_id}")
        # optional second factor
        if policy_mod.load().get("local_pin_required") and security.has_pin():
            self.send(chat_id, "Approved. Now confirm with /unlock-style PIN: send "
                               f"confirm {action.rsplit(':', 1)[-1]} <PIN>")
            return
        # feed "yes" into THAT session: the dispatcher's own gate has the final say
        def job():
            try:
                from ishti import dispatcher
                dispatch = self._dispatch or dispatcher.handle
                res = dispatch("yes", session_id=f"tg:{chat_id}", channel="telegram")
                self.send(chat_id, (res or {}).get("final_answer") or "(empty reply)")
            except Exception as exc:
                self.send(chat_id, f"The approved action failed: {type(exc).__name__}: {str(exc)[:120]}")
                _audit("approved action failed", f"{type(exc).__name__}", session=f"tg:{chat_id}")
        self._pool.submit(job)

    # ------------------------------------------------------------ helpers
    @staticmethod
    def classify_forbidden(text: str) -> str | None:
        clean = _strip_quotes(text)  # quoted content (pages, chats, quotes) is data - never a request
        for cat, rx in FORBIDDEN:
            if rx.search(clean):
                return cat
        return None

    @staticmethod
    def _status_text() -> str:
        lines = ["ISHTI remote status:"]
        try:
            from ishti.vram import snapshot
            snap = snapshot() or {}
            lines.append(f"GPU: {snap.get('used_mb', '?')} / {snap.get('total_mb', '?')} MB "
                         f"({snap.get('percent', '?')}%)")
        except Exception:
            lines.append("GPU: read failed (model server likely down)")
        try:
            from ishti import taskmgr
            tasks = taskmgr.list_tasks() or []
            running = [t for t in tasks if t.get("status") == "running"]
            lines.append(f"Tasks: {len(running)} running, {len(tasks)} known")
        except Exception:
            lines.append("Tasks: read failed")
        lines.append(f"Locked: {'yes' if security.locked() else 'no'}; "
                     f"approvals pending: {approvals.pending()}")
        return "\n".join(lines)

    @staticmethod
    def _tasks_text() -> str:
        try:
            from ishti import taskmgr
            tasks = taskmgr.list_tasks() or []
            if not tasks:
                return "No background tasks."
            rows = [f"- {t.get('id')}: {t.get('goal') or t.get('topic') or '?'} [{t.get('status')}]"
                    for t in tasks[:10]]
            return "Background tasks:\n" + "\n".join(rows)
        except Exception as exc:
            return f"Tasks unavailable: {type(exc).__name__}"

    @staticmethod
    def _cancel_task(tid: str) -> str:
        if not tid:
            return "Usage: /cancel <task id> (see /tasks)."
        try:
            from ishti import taskmgr
            return (f"Cancelled {tid}." if taskmgr.cancel(tid) else f"No such task: {tid}")
        except Exception as exc:
            return f"Cancel failed: {type(exc).__name__}: {str(exc)[:100]}"

    @staticmethod
    def _help_text() -> str:
        return ("Remote control (same ISHTI, same rules as on the PC):\n"
                "/status - VRAM, loaded models, running tasks\n"
                "/tasks - background tasks\n/cancel <id> - cancel one\n"
                "/pause, /resume - hold/resume remote work\n"
                "/kill - stop everything now\n/lock - refuse all but panic commands\n"
                "/unlock <PIN> - unlock\n/digest - 24-hour chat digest\n"
                "/help - this text\n\nAnything else is natural language. Some actions are "
                "refused remotely by policy; T2 actions need an Approve/Deny confirmation here "
                "with an exact recap.")


if __name__ == "__main__":
    # offline self-check of the pure-decision parts (classifier, help/status guards)
    import ishti.channels.policy as P
    import tempfile, os
    P.PATH = os.path.join(tempfile.mkdtemp(), "p.yaml")
    P.reset_cache()
    g = Gate()
    assert g.classify_forbidden("please delete my files in Downloads") == "delete_files"
    assert g.classify_forbidden("shut down the PC now") == "shutdown"
    assert g.classify_forbidden("place a live trade on my broker account") == "finance_execute"
    assert g.classify_forbidden("send me the api keys") == "read_secrets"
    assert g.classify_forbidden("take a screenshot of the screen") == "screenshots"
    assert g.classify_forbidden("enable the trading skill") == "install_skills"
    assert g.classify_forbidden("summarize my research notes on recycling paper") is None, \
        "a benign request must not trip the classifier"
    assert g.classify_forbidden("what's the weather in Peshawar tomorrow") is None
    assert g.classify_forbidden("restart my computer") == "restart"
    h = g._help_text()
    assert "/kill" in h and "/unlock" in h
    print("[self-check] channels.gate OK - forbidden classifier (incl. negative), help text")
