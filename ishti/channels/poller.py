"""The single Telegram long-polling loop (WP-A1; design R1 A, D1).

One poller per bot token — enforced here (start() refuses a second). Outbound-only: it dials
api.telegram.org; nothing listens. Responsibility split:
  - poller: transport + offset bookkeeping + idempotency + stale expiry, then hands each clean,
    authenticated message to the gate's on_message callback.
  - gate:   allowlist, rate limits, commands, approvals (security decisions live there).
Offset + processed update_ids persist in SQLite so a restart neither re-executes nor loses anything:
  - processed-set check happens BEFORE execution (crash between receive and execute = skip, not redo)
  - the offset advances only after the update is handed to the gate.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time

from ishti import CONFIG
from ishti.channels.telegram_client import TelegramClient, BotAPIError

DB_PATH = os.path.join(os.path.dirname(CONFIG["memory"]["db_path"]), "remote_poller.db")

# update kinds we accept; everything else (edited_*, channel_post, ...) never arrives
ALLOWED = ("message", "callback_query")


class Poller:
    def __init__(self, client: TelegramClient, on_message, db_path: str | None = None):
        self.client = client
        self.on_message = on_message          # fn(update: dict) -> None (the gate)
        self.db = db_path or DB_PATH
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._state_lock = threading.Lock()
        self.offset: int | None = None
        self.last_error = ""
        self.last_poll_at = 0.0
        self.started_at = 0.0
        self.updates_seen = 0
        self._init_db()

    # ------------------------------------------------------------- persistence
    def _init_db(self):
        os.makedirs(os.path.dirname(self.db), exist_ok=True)
        with self._conn() as c:
            c.execute("""CREATE TABLE IF NOT EXISTS poll_state(
                         id INTEGER PRIMARY KEY CHECK (id=1),
                         offset INTEGER, updated REAL, meta TEXT)""")
            c.execute("""CREATE TABLE IF NOT EXISTS processed_ids(
                         update_id INTEGER PRIMARY KEY, processed REAL)""")
            row = c.execute("SELECT offset, meta FROM poll_state WHERE id=1").fetchone()
        if row:
            self.offset = row[0]
            try:
                self._meta = json.loads(row[1] or "{}")
            except Exception:
                self._meta = {}
        else:
            self._meta = {}

    def _conn(self):
        c = sqlite3.connect(self.db, timeout=10)
        return c

    def _save_offset(self, offset: int):
        with self._state_lock, self._conn() as c:
            c.execute("""INSERT INTO poll_state(id, offset, updated, meta) VALUES(1,?,?,?)
                         ON CONFLICT(id) DO UPDATE SET offset=excluded.offset, updated=excluded.updated""",
                      (offset, time.time(), json.dumps(self._meta)))

    def _mark_processed(self, update_id: int):
        with self._state_lock, self._conn() as c:
            c.execute("INSERT OR IGNORE INTO processed_ids(update_id, processed) VALUES(?,?)",
                      (update_id, time.time()))
            c.execute("""DELETE FROM processed_ids WHERE update_id <= ?
                         AND update_id < (SELECT COALESCE(MAX(update_id),0) - 500 FROM processed_ids)""", (update_id,))

    def already_processed(self, update_id: int) -> bool:
        with self._conn() as c:
            row = c.execute("SELECT 1 FROM processed_ids WHERE update_id=?", (update_id,)).fetchone()
        return bool(row)

    # ------------------------------------------------------------- lifecycle
    def start(self):
        assert self._thread is None, "poller already running (one poller per token)"
        self._stop.clear()
        self.started_at = time.time()
        self._thread = threading.Thread(target=self._loop, name="ishti-telegram-poller", daemon=True)
        self._thread.start()
        return self

    def stop(self, timeout: float = 8.0) -> bool:
        """Graceful: signal stop, KICK the in-flight long poll with a timeout=0 getUpdates from a
        second connection (the server releases the hanging poll; a 409 on the kicker is fine), then
        join. The thread object is only discarded once it is really dead."""
        self._stop.set()
        t = self._thread
        if t and t.is_alive():
            kicker = threading.Thread(target=self._kick, daemon=True)
            kicker.start()
            t.join(timeout=timeout)
        alive = bool(t and t.is_alive())
        self._thread = None
        return not alive

    def _kick(self):
        """Short-poll from a second request so the long poll returns at once (python-telegram-bot's
        documented stop trick). Any error here is harmless - stop() still joins with a deadline."""
        try:
            self.client.get_updates(self.offset, timeout_s=0, allowed=ALLOWED)
        except Exception:
            pass

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive() and not self._stop.is_set())

    # ------------------------------------------------------------- the loop
    def _loop(self):
        try:
            self.client.delete_webhook()   # leftover webhook would 409 the polling (R1 A)
        except BotAPIError as exc:
            self.last_error = f"auth/webhook: {exc.description}"
            return                          # no token or bad token: stay stopped, health shows why
        backoff = 1.0
        while not self._stop.is_set():
            try:
                updates = self.client.get_updates(self.offset, timeout_s=25, allowed=ALLOWED)
                backoff = 1.0
                self.last_poll_at = time.time()
            except BotAPIError as exc:
                self.last_error = exc.description[:200]
                if exc.retry_after:
                    self._stop.wait(exc.retry_after)
                else:
                    self._stop.wait(backoff)
                    backoff = min(60.0, backoff * 2)
                continue
            for upd in updates:
                if self._stop.is_set():
                    break
                self._handle_update(upd)
        # loop end: nothing left running; the httpx client closes with the service

    def _handle_update(self, upd: dict):
        uid = upd.get("update_id")
        if uid is None:
            return
        self.updates_seen += 1
        if self.already_processed(uid):     # idempotency: crashed after execute, before offset
            self._advance(uid)
            return
        try:
            self.on_message(upd)            # the gate decides what this update is allowed to do
        except Exception as exc:            # the poller must survive anything the gate throws
            self.last_error = f"gate error: {type(exc).__name__}: {str(exc)[:150]}"
            try:
                from ishti import audit
                with audit.context(skill="channels", initiator="unknown", channel="telegram"):
                    audit.record("remote gate error", f"update {uid}: {type(exc).__name__}: {str(exc)[:120]}")
            except Exception:
                pass
        finally:
            self._mark_processed(uid)
            self._advance(uid)

    def _advance(self, update_id: int):
        nxt = (self.offset if self.offset is not None else update_id - 1) + 1
        nxt = max(nxt, update_id + 1)
        self.offset = nxt
        self._save_offset(nxt)

    # ------------------------------------------------------------- stale commands
    def flush_stale(self, max_age_s: float) -> int:
        """Called at startup BEFORE the loop starts: updates queued while the PC was off are
        confirmed without execution (they expire, the owner is asked to resend - R4 §5).
        Returns how many were dropped."""
        try:
            updates = self.client.get_updates(self.offset, timeout_s=0, allowed=ALLOWED)
        except BotAPIError as exc:
            self.last_error = exc.description[:200]
            return 0
        now = time.time()
        dropped = 0
        newest = None
        for upd in updates:
            uid = upd.get("update_id")
            newest = uid if newest is None else max(newest, uid)
            if self.already_processed(uid):
                continue
            if now - (upd.get("message", {}).get("date") or now) > max_age_s:
                dropped += 1
                try:
                    from ishti import audit
                    m = upd.get("message") or {}
                    with audit.context(skill="channels", initiator="owner", channel="telegram",
                                       session=f"tg:{(m.get('chat') or {}).get('id')}"):
                        audit.record("remote command expired (stale)",
                                     f"update {uid}, sent {now - (m.get('date') or now):.0f}s ago")
                except Exception:
                    pass
            else:
                # fresh enough to handle normally (gate errors never kill the flush)
                try:
                    self.on_message(upd)
                except Exception as exc:
                    self.last_error = f"gate error: {type(exc).__name__}: {str(exc)[:150]}"
            self._mark_processed(uid)
        if newest is not None:
            self.offset = max(self.offset or 0, newest + 1)
            self._save_offset(self.offset)
        return dropped

    def health(self) -> dict:
        return {"running": self.running, "offset": self.offset, "updates_seen": self.updates_seen,
                "last_error": self.last_error, "last_poll_age_s": round(time.time() - self.last_poll_at, 1)
                if self.last_poll_at else None, "uptime_s": round(time.time() - self.started_at, 1)
                if self.started_at else 0}


if __name__ == "__main__":
    # offline self-check: idempotency across restart, stale flush, graceful stop (mock transport)
    import tempfile
    import httpx

    db = os.path.join(tempfile.mkdtemp(), "p.db")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/deleteWebhook"):
            return httpx.Response(200, json={"ok": True, "result": True})
        if request.url.path.endswith("/getUpdates"):
            body = json.loads(request.read() or b"{}")
            if body.get("offset") in (None, 100):
                return httpx.Response(200, json={"ok": True, "result": [
                    {"update_id": 100, "message": {"message_id": 1, "date": time.time(), "chat": {"id": 5},
                                                   "from": {"id": 5}, "text": "hi"}}]})
            return httpx.Response(200, json={"ok": True, "result": []})
        return httpx.Response(200, json={"ok": True, "result": {}})

    client = TelegramClient("t", transport=httpx.MockTransport(handler))
    seen = []
    p = Poller(client, seen.append, db_path=db)
    dropped = p.flush_stale(max_age_s=300)          # the one fresh update goes through
    assert dropped == 0 and seen and seen[0]["update_id"] == 100
    assert p.offset == 101, p.offset
    # restart: same db, update 100 must NOT be executed again
    seen2 = []
    p2 = Poller(client, seen2.append, db_path=db)
    assert p2.offset == 101 and not p2.already_processed(101)
    dropped2 = p2.flush_stale(max_age_s=300)
    assert seen2 == [] and dropped2 == 0, (seen2, dropped2)
    # stale updates are dropped, not executed
    def stale_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/getUpdates"):
            return httpx.Response(200, json={"ok": True, "result": [
                {"update_id": 200, "message": {"message_id": 2, "date": time.time() - 3600,
                                               "chat": {"id": 5}, "from": {"id": 5}, "text": "old"}}]})
        return httpx.Response(200, json={"ok": True, "result": {}})
    p3 = Poller(TelegramClient("t", transport=httpx.MockTransport(stale_handler)), seen.append,
                db_path=os.path.join(tempfile.mkdtemp(), "q.db"))
    assert p3.flush_stale(max_age_s=300) == 1 and len(seen) == 1
    # graceful stop leaves nothing running (emulates Telegram: a second getUpdates releases the poll)
    release = threading.Event()

    def endless(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/getUpdates"):
            if json.loads(request.read() or b"{}").get("timeout"):
                release.wait(30)  # the poll hangs until the server releases it
                return httpx.Response(200, json={"ok": True, "result": []})
            release.set()         # the timeout=0 kicker IS the release
            return httpx.Response(200, json={"ok": True, "result": []})
        return httpx.Response(200, json={"ok": True, "result": {}})

    p4 = Poller(TelegramClient("t", transport=httpx.MockTransport(endless)), lambda u: None,
                db_path=os.path.join(tempfile.mkdtemp(), "r.db"))
    p4.start()
    time.sleep(0.3)
    assert p4.running
    assert p4.stop(timeout=10) is True, "graceful stop failed"
    assert not p4.running
    h = p4.health()
    assert h["running"] is False and h["updates_seen"] >= 0
    print("[self-check] channels.poller OK - offset persists, no re-execution after restart, stale flushed, graceful stop")
