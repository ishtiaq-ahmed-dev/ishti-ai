"""Raw Telegram Bot API client over httpx (decision D2 in RESEARCH.md R1: zero new dependencies).

Implements exactly what ISHTI needs: getUpdates (long polling), sendMessage (chunking happens in
output.py), answerCallbackQuery, editMessageText, getFile+file download, sendDocument, sendPhoto,
deleteWebhook (so a leftover webhook can never silently kill polling), getMe.

Token handling: the token is fetched from the DPAPI store per call (cached 60 s in memory); it is
never logged, never put in an exception message, never returned by __repr__.
"""
from __future__ import annotations

import threading
import time

import httpx

_api = "https://api.telegram.org"


class BotAPIError(RuntimeError):
    def __init__(self, code: int, description: str, retry_after: float | None = None):
        super().__init__(f"telegram {code}: {description}")  # never contains the token
        self.code = code
        self.description = description
        self.retry_after = retry_after


class TelegramClient:
    def __init__(self, token: str | None = None, base_url: str | None = None,
                 timeout: float = 40.0, transport: httpx.BaseTransport | None = None):
        """transport: injectable for tests (httpx.MockTransport). base_url: mock server URL."""
        self._token = token
        self._token_at = 0.0
        self._lock = threading.Lock()
        self.base = (base_url or _api).rstrip("/")
        self.timeout = timeout
        self._client = httpx.Client(base_url=self.base, timeout=timeout, transport=transport)

    # -- token ---------------------------------------------------------------
    def token(self, force=False) -> str:
        with self._lock:
            if self._token and not force and time.time() - self._token_at < 60:
                return self._token
        if self._token is not None:      # explicit token (tests): never overridden
            return self._token
        from ishti.channels import security
        tok = security.load_token()
        if not tok:
            raise BotAPIError(0, "no bot token stored - pair first in the desktop UI")
        with self._lock:
            self._token, self._token_at = tok, time.time()
        return tok

    def invalidate_token(self):
        with self._lock:
            self._token_at = 0.0

    # -- core call -----------------------------------------------------------
    def call(self, method: str, data: dict | None = None, timeout: float | None = None) -> dict:
        payload = dict(data or {})
        try:
            r = self._client.post(f"/bot{self.token()}/{method}", json=payload,
                                  timeout=timeout or self.timeout)
        except httpx.TimeoutException as exc:
            raise BotAPIError(0, f"timeout: {type(exc).__name__}") from exc
        except httpx.HTTPError as exc:
            raise BotAPIError(0, f"network: {type(exc).__name__}") from exc
        try:
            body = r.json()
        except Exception as exc:
            raise BotAPIError(r.status_code, "non-JSON response") from exc
        if not body.get("ok"):
            desc = str(body.get("description", "unknown"))
            retry = None
            params = body.get("parameters") or {}
            if isinstance(params, dict) and params.get("retry_after"):
                retry = float(params["retry_after"])
            raise BotAPIError(r.status_code, desc, retry)
        return body.get("result")

    # -- convenience ---------------------------------------------------------
    def get_me(self) -> dict:
        return self.call("getMe")

    def delete_webhook(self) -> bool:
        """Polling cannot run while a webhook is set (409) - clear it before polling (R1 A)."""
        try:
            self.call("deleteWebhook", {"drop_pending_updates": False})
            return True
        except BotAPIError as exc:
            if exc.code == 404:  # token invalid; let the poller's auth path surface it
                raise
            return False

    def get_updates(self, offset: int | None, timeout_s: int = 25,
                    allowed=("message", "callback_query")) -> list[dict]:
        data = {"timeout": int(timeout_s), "allowed_updates": list(allowed)}
        if offset is not None:
            data["offset"] = int(offset)
        return self.call("getUpdates", data, timeout=timeout_s + 12.0) or []

    def send(self, chat_id: int, text: str, reply_markup: dict | None = None, **kw) -> dict:
        data = {"chat_id": chat_id, "text": text, "parse_mode": "HTML", **kw}
        if reply_markup:
            data["reply_markup"] = reply_markup
        return self.call("sendMessage", data)

    def edit(self, chat_id: int, message_id: int, text: str, reply_markup: dict | None = None) -> dict:
        data = {"chat_id": chat_id, "message_id": message_id, "text": text, "parse_mode": "HTML"}
        if reply_markup:
            data["reply_markup"] = reply_markup
        return self.call("editMessageText", data)

    def answer_callback(self, callback_query_id: str, text: str = "", alert: bool = False) -> bool:
        try:
            self.call("answerCallbackQuery", {"callback_query_id": callback_query_id,
                                              "text": text[:200], "show_alert": bool(alert)})
            return True
        except BotAPIError as exc:
            if exc.code == 400:  # already answered / too old: the replay guard's friend
                return False
            raise

    def download(self, file_id: str, dest_path: str) -> str:
        """getFile (<=20 MB) + download to dest_path. Returns dest_path."""
        info = self.call("getFile", {"file_id": file_id})
        file_path = info.get("file_path")
        if not file_path:
            raise BotAPIError(0, "getFile returned no file_path")
        url = f"{self.base}/file/bot{self.token()}/{file_path}"
        with self._client.stream("GET", url) as r:
            r.raise_for_status()
            with open(dest_path, "wb") as f:
                for chunk in r.iter_bytes():
                    f.write(chunk)
        return dest_path

    def send_document(self, chat_id: int, path: str, caption: str = "") -> dict:
        with open(path, "rb") as f:
            r = self._client.post(f"/bot{self.token()}/sendDocument",
                                  data={"chat_id": str(chat_id), "caption": caption[:1024]},
                                  files={"document": (os_path_name(path), f)})
        body = r.json()
        if not body.get("ok"):
            raise BotAPIError(r.status_code, str(body.get("description")))
        return body.get("result")

    def send_photo(self, chat_id: int, path: str, caption: str = "") -> dict:
        with open(path, "rb") as f:
            r = self._client.post(f"/bot{self.token()}/sendPhoto",
                                  data={"chat_id": str(chat_id), "caption": caption[:1024]},
                                  files={"photo": (os_path_name(path), f)})
        body = r.json()
        if not body.get("ok"):
            raise BotAPIError(r.status_code, str(body.get("description")))
        return body.get("result")

    def close(self):
        try:
            self._client.close()
        except Exception:
            pass


def os_path_name(path: str) -> str:
    import os
    return os.path.basename(path)


if __name__ == "__main__":
    # offline self-check with a mock transport: the API shape, 429 body, token hygiene
    def handler(request: httpx.Request) -> httpx.Response:
        url = request.url.path
        assert "/bot123:SECRET/" in url or "/file/bot123:SECRET/" in url, url
        if url.endswith("/getMe"):
            return httpx.Response(200, json={"ok": True, "result": {"id": 1, "username": "x"}})
        if url.endswith("/sendMessage"):
            body = request.read().decode()
            assert "123:SECRET" not in body
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 7}})
        if url.endswith("/bad"):
            return httpx.Response(429, json={"ok": False, "description": "Too Many Requests",
                                             "parameters": {"retry_after": 3}})
        return httpx.Response(200, json={"ok": True, "result": {}})

    c = TelegramClient("123:SECRET", transport=httpx.MockTransport(handler))
    assert c.get_me()["username"] == "x"
    assert c.send(5, "hi")["message_id"] == 7
    try:
        c.call("bad")
        raise SystemExit("429 did not raise")
    except BotAPIError as e:
        assert e.retry_after == 3.0 and "SECRET" not in str(e)
    print("[self-check] channels.telegram_client OK - mock round-trip, 429 retry_after, token never leaks")
