"""Discord and Slack as remote channels: the owner's direct messages with their own ISHTI bot, polled over each
platform's plain HTTPS API (no websocket library, nothing extra to install, nothing to expose to the internet).

The same rules as WhatsApp and Telegram: only the owner's account id is obeyed, /kill /lock /unlock /status work,
forbidden actions are refused remotely, stale messages are not run, replies pass the sensitive-output filter, and an
action that needs a yes still asks. Bot tokens live in Windows Credential Manager (service "ishti-<channel>").
Setup is one chat line on the PC: "connect discord bot <token> owner <your user id>" (ishti/skills/extensions)."""
from __future__ import annotations

import datetime as dt
import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import httpx

from ishti import ROOT
from ishti.channels import policy as policy_mod, security
from ishti.channels.gate import Gate, _audit
from ishti.channels.output import chunk, sensitive_filter
from ishti.channels.wa_inbound import remote_command
from ishti.test_mode import scratch_path

KEYRING_SERVICE = "ishti-{}"
POLL_S = 3.0  # Discord and Slack allow far more; 3 s keeps a reply under ~4 s without busy polling


def token(channel: str) -> str:
    import keyring
    return keyring.get_password(KEYRING_SERVICE.format(channel), "bot") or ""


class ChannelError(RuntimeError):
    pass


class DiscordAPI:
    LIMIT = 1900  # Discord's message cap is 2000 characters

    def __init__(self, bot_token: str, http: httpx.Client | None = None):
        self.http = http or httpx.Client(base_url="https://discord.com/api/v10", timeout=20, headers={
            "Authorization": f"Bot {bot_token}", "User-Agent": "DiscordBot (https://github.com/ishti, 0.2)"})

    def _call(self, method, path, **kw):
        r = self.http.request(method, path, **kw)
        if r.status_code == 429:
            time.sleep(min(float((r.json() or {}).get("retry_after", 2)), 30))
            r = self.http.request(method, path, **kw)
        if r.status_code in (401, 403):
            raise ChannelError("Discord refused the bot token")
        r.raise_for_status()
        return r.json()

    def me(self) -> str:
        return str(self._call("GET", "/users/@me").get("username") or "")

    def open_dm(self, owner: str) -> str:
        return str(self._call("POST", "/users/@me/channels", json={"recipient_id": owner})["id"])

    def latest(self, dm: str) -> str:
        rows = self._call("GET", f"/channels/{dm}/messages", params={"limit": 1})
        return str(rows[0]["id"]) if rows else "0"

    def since(self, dm: str, cursor: str) -> list[dict]:
        rows = self._call("GET", f"/channels/{dm}/messages", params={"after": cursor, "limit": 50})
        out = [{"id": str(m["id"]), "author": str((m.get("author") or {}).get("id") or ""),
                "bot": bool((m.get("author") or {}).get("bot")), "text": str(m.get("content") or ""),
                "ts": dt.datetime.fromisoformat(str(m.get("timestamp")).replace("Z", "+00:00")).timestamp()
                if m.get("timestamp") else time.time()} for m in rows]
        return sorted(out, key=lambda m: int(m["id"]))

    def send(self, dm: str, text: str):
        self._call("POST", f"/channels/{dm}/messages", json={"content": text})


class SlackAPI:
    LIMIT = 3500

    def __init__(self, bot_token: str, http: httpx.Client | None = None):
        self.http = http or httpx.Client(base_url="https://slack.com/api/", timeout=20,
                                         headers={"Authorization": f"Bearer {bot_token}"})

    def _call(self, method, **params):
        r = self.http.post(method, data=params)
        if r.status_code == 429:
            time.sleep(min(float(r.headers.get("Retry-After", 2)), 30))
            r = self.http.post(method, data=params)
        r.raise_for_status()
        data = r.json()
        if not data.get("ok"):
            err = str(data.get("error") or "unknown_error")
            raise ChannelError(f"Slack refused the bot token ({err})" if err in ("invalid_auth", "not_authed",
                                                                                 "account_inactive", "token_revoked")
                               else f"Slack error: {err}")
        return data

    def me(self) -> str:
        return str(self._call("auth.test").get("user") or "")

    def open_dm(self, owner: str) -> str:
        return str(self._call("conversations.open", users=owner)["channel"]["id"])

    def latest(self, dm: str) -> str:
        rows = self._call("conversations.history", channel=dm, limit=1).get("messages") or []
        return str(rows[0]["ts"]) if rows else f"{time.time():.6f}"

    def since(self, dm: str, cursor: str) -> list[dict]:
        rows = self._call("conversations.history", channel=dm, oldest=cursor, limit=50).get("messages") or []
        out = [{"id": str(m["ts"]), "author": str(m.get("user") or ""), "bot": bool(m.get("bot_id") or m.get("subtype")),
                "text": str(m.get("text") or ""), "ts": float(m["ts"])} for m in rows]
        return sorted(out, key=lambda m: float(m["id"]))

    def send(self, dm: str, text: str):
        self._call("chat.postMessage", channel=dm, text=text)


APIS = {"discord": DiscordAPI, "slack": SlackAPI}


class DMChannel:
    """Poll one owner's DM with the bot; run their requests through the dispatcher like any remote channel."""

    def __init__(self, name: str, api, owner_id: str, *, dispatch=None, background=True, clock=time.time,
                 allow_message=None, cursor_path: str | None = None):
        self.name, self.api, self.owner = name, api, str(owner_id)
        self.dispatch, self.clock = dispatch, clock
        self.allow_message = allow_message or (lambda sender: security.limiter_for(sender).allow())
        self.cursor_path = cursor_path or scratch_path(os.path.join(ROOT, "data", f"{name}_cursor.json"))
        self.dm, self.cursor = self._load()
        self._pending: set[str] = set()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix=f"ishti-{name}-run") if background else None
        self._lock = threading.Lock()  # one request at a time, in order
        self.last_error = ""
        self.last_inbound_ts = 0.0

    def _load(self):
        """(dm channel, cursor) saved for this owner; a missing or foreign file means a fresh start."""
        try:
            with open(self.cursor_path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return "", ""
        if data.get("owner") != self.owner:
            return "", ""
        return data.get("dm") or "", data.get("cursor") or ""

    def _save(self):
        os.makedirs(os.path.dirname(self.cursor_path), exist_ok=True)
        with open(self.cursor_path, "w", encoding="utf-8") as f:
            json.dump({"owner": self.owner, "dm": self.dm, "cursor": self.cursor}, f)

    def send(self, text: str):
        filtered, _ = sensitive_filter(str(text or ""))
        for part in chunk(filtered, self.api.LIMIT) or ["(empty reply)"]:
            self.api.send(self.dm, part)

    def poll_once(self) -> int:
        if not self.dm:
            self.dm = self.api.open_dm(self.owner)
        if not self.cursor:  # first start: messages from before ISHTI listened are history, not commands
            self.cursor = self.api.latest(self.dm)
            self._save()
            return 0
        handled = 0
        for row in self.api.since(self.dm, self.cursor):
            self.cursor = row["id"]
            self._save()
            if row["bot"] or row["author"] != self.owner:
                continue
            try:
                self._handle(row)
                handled += 1
            except Exception as exc:
                self.last_error = f"{type(exc).__name__}: {str(exc)[:160]}"
                _audit(f"{self.name} inbound failed", self.last_error, channel=self.name)
        return handled

    def _handle(self, row: dict):
        text, session = row["text"].strip(), f"{self.name}:{self.owner}"
        if not text:
            return
        self.last_inbound_ts = self.clock()
        _audit(f"{self.name} message received", f"message={row['id'][-12:]}", channel=self.name, session=session)
        pol = policy_mod.load()
        if self.clock() - row["ts"] > float(pol.get("stale_command_max_age_s", 300)):
            return self.send("That message is stale, so I did not run it. Please resend if it still applies.")
        if not self.allow_message(f"{self.name}:{self.owner}"):
            return self.send("Rate limit reached. Retry in a few seconds.")
        if remote_command(text, self.send, self.name.title()):
            return
        if cat := Gate.classify_forbidden(text):
            return self.send(f"Refused: '{cat}' is not allowed remotely. Ask on the PC, where it is gated normally.")
        answer_text = text
        if session in self._pending and text.casefold() in {"yes", "y", "no", "n"}:
            answer_text = "yes" if text.casefold() in {"yes", "y"} else "no"
            self._pending.discard(session)

        def run():
            dispatch = self.dispatch
            if dispatch is None:
                from ishti import dispatcher
                dispatch = dispatcher.handle
            with self._lock:
                try:
                    result = dispatch(answer_text, session_id=session, channel=self.name)
                    answer = (result or {}).get("final_answer") if isinstance(result, dict) else str(result or "")
                    answer = answer or "(empty reply)"
                    if answer.rstrip().endswith("(yes/no)"):
                        self._pending.add(session)
                except Exception as exc:
                    self.last_error = f"{type(exc).__name__}: {str(exc)[:160]}"
                    _audit(f"{self.name} dispatch failed", self.last_error, channel=self.name, session=session)
                    answer = f"That failed: {self.last_error}"
                self.send(answer)

        self._pool.submit(run) if self._pool else run()

    def start(self):
        if self.running:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name=f"ishti-{self.name}-inbound")
        self._thread.start()

    def _loop(self):
        backoff = POLL_S
        while not self._stop.is_set():
            try:
                self.poll_once()
                backoff, self.last_error = POLL_S, ""
            except ChannelError as exc:  # a revoked token: stop asking the platform every 3 s
                self.last_error, backoff = str(exc), 300.0
            except Exception as exc:     # network hiccup: back off up to a minute
                self.last_error, backoff = f"{type(exc).__name__}: {str(exc)[:160]}", min(backoff * 2, 60.0)
            self._stop.wait(backoff)

    def stop(self, timeout: float = 5.0) -> bool:
        self._stop.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout)
        self._thread = None
        if self._pool:
            self._pool.shutdown(wait=False, cancel_futures=True)
        return not (thread and thread.is_alive())

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive() and not self._stop.is_set())

    def health(self) -> dict:
        return {"running": self.running, "last_error": self.last_error, "last_inbound_ts": self.last_inbound_ts}


def wanted(name: str, pol: dict | None = None) -> bool:
    cfg = (pol or policy_mod.load()).get(name) or {}
    return bool(isinstance(cfg, dict) and cfg.get("enabled") and cfg.get("owner_id") and token(name))


def make(name: str, pol: dict | None = None) -> DMChannel:
    cfg = (pol or policy_mod.load()).get(name) or {}
    return DMChannel(name, APIS[name](token(name)), str(cfg["owner_id"]))
