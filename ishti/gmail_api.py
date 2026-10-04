"""Gmail over the REST API, using the access token from ishti.oauth (no browser, no scraping).

Why it replaces the browser path: the Playwright path opens Chrome, waits for the page, then reads the DOM.
This asks Google directly, so "do I have unread mail" answers in well under a second and works whether or
not the owner's browser profile is signed in. The browser stays as the fallback for the owner's own
explicit "open my inbox" (they asked for the web) and for anything the API does not cover.

Stdlib only (urllib + base64): the google-api-python-client stack is not a dependency of this project.

Scopes come from ishti.oauth.GMAIL_SCOPES: gmail.readonly, gmail.modify, gmail.send, userinfo.email.
"""
from __future__ import annotations

import base64
import json
import urllib.parse
import urllib.request

API = "https://gmail.googleapis.com/gmail/v1/users/me"


def available(service: str = "gmail") -> bool:
    """True when the owner has authorised Gmail AND we can get a live access token."""
    from ishti import oauth
    return bool(oauth.token(service))


def _req(path: str, params: dict = None, body: dict = None, method: str = "") -> dict:
    from ishti import oauth
    tok = oauth.token("gmail")
    if not tok:
        raise RuntimeError("Gmail is not connected yet - say 'connect gmail'.")
    url = f"{API}{path}" + ("?{0}".format(urllib.parse.urlencode(params)) if params else "")
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data, {"Authorization": f"Bearer {tok}",
                                             "Content-Type": "application/json"},
                                 method=method or ("POST" if body is not None else "GET"))
    with urllib.request.urlopen(req, timeout=45) as r:
        return json.loads(r.read().decode() or "{}")


def _hdr(msg: dict, name: str) -> str:
    for h in msg.get("payload", {}).get("headers", []):
        if str(h.get("name", "")).lower() == name.lower():
            return str(h.get("value") or "")
    return ""


def _decode(body: dict) -> str:
    data = body.get("data")
    if not data:
        return ""
    if body.get("attachmentId"):
        return f"[attachment {body['attachmentId']}]"
    pad = "=" * (-len(data) % 4)
    try:
        raw = base64.urlsafe_b64decode(data + pad)
        return raw.decode("utf-8", "replace")
    except Exception:  # noqa: BLE001
        return ""


def _parts(payload: dict, out: list) -> list:
    if payload.get("body", {}).get("data"):
        out.append(payload)
    for p in payload.get("parts", []) or []:
        _parts(p, out)
    return out


def unread(limit: int = 25) -> list:
    """Newest-first [{'id','from','subject','date','snippet','unread'}] - the raw list, never invented."""
    ids = (_req("/messages", {"q": "is:unread", "maxResults": limit}) or {}).get("messages", [])
    out = []
    for m in ids[:limit]:
        msg = (_req(f"/messages/{m['id']}", {"format": "metadata"}) or {})
        out.append({"id": m["id"], "from": _hdr(msg, "From"), "subject": _hdr(msg, "Subject"),
                    "date": _hdr(msg, "Date"), "snippet": (msg.get("snippet") or "").strip(),
                    "unread": "UNREAD" in msg.get("labelIds", [])})
    return out


def search(query: str, limit: int = 10) -> list:
    ids = (_req("/messages", {"q": query, "maxResults": limit}) or {}).get("messages", [])
    out = []
    for m in ids[:limit]:
        msg = (_req(f"/messages/{m['id']}", {"format": "metadata"}) or {})
        out.append({"id": m["id"], "from": _hdr(msg, "From"), "subject": _hdr(msg, "Subject"),
                    "date": _hdr(msg, "Date"), "snippet": (msg.get("snippet") or "").strip()})
    return out


def read(message_id: str) -> dict:
    """The whole message: headers + body text decoded from base64url."""
    msg = _req(f"/messages/{message_id}", {"format": "full"}) or {}
    blobs = _parts(msg.get("payload", {}), [])
    body = next((_decode(b["body"]) for b in blobs if "html" not in b.get("mimeType", "")), "")
    if not body and blobs:
        body = _decode(blobs[0]["body"])
    return {"id": message_id, "from": _hdr(msg, "From"), "to": _hdr(msg, "To"),
            "subject": _hdr(msg, "Subject"), "date": _hdr(msg, "Date"), "body": body.strip(),
            "snippet": (msg.get("snippet") or "").strip()}


def _mime(to: str, subject: str, body: str) -> str:
    msg = f"To: {to}\r\nSubject: {subject}\r\nContent-Type: text/plain; charset=utf-8\r\n\r\n{body}"
    return base64.urlsafe_b64encode(msg.encode()).decode().rstrip("=")


def send(to: str, subject: str, body: str) -> str:
    out = _req("/messages/send", body={"raw": _mime(to, subject, body)})
    return f"Sent to {to} - {subject} (Gmail id {out.get('id', '?')})"


def draft(to: str, subject: str, body: str) -> str:
    out = _req("/drafts", body={"message": {"raw": _mime(to, subject, body)}})
    return f"Draft saved in Gmail for {to} - {subject}"


def mark_read(message_id: str, read: bool = True) -> str:
    _req(f"/messages/{message_id}/modify", body={"removeLabelIds": ["UNREAD"]} if read
         else {"addLabelIds": ["UNREAD"]})
    return "marked read" if read else "marked unread"


def whoami() -> str:
    from ishti import oauth
    return oauth.account("gmail") or "(account on file)"


if __name__ == "__main__":
    from ishti import oauth as _o
    assert _mime("a@b.c", "S", "x") and API.endswith("/users/me")
    try:
        rows = unread(5)
        print(f"[self-check] gmail_api OK - connected as {_o.account('gmail')}, {len(rows)} unread: "
              + "; ".join(r["subject"][:40] for r in rows[:3]))
    except RuntimeError as exc:
        print(f"[self-check] gmail_api OK (not connected yet: {exc})")
    assert not available("ishti_no_such_service")