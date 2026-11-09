"""ISHTI web fetcher: plain HTTP + content extraction, guarded against SSRF.

Every page ISHTI reads for the owner goes through safe_get(): only http(s) URLs to PUBLIC addresses, every redirect hop
re-checked, body size capped. A page that redirects to http://127.0.0.1:8888/api/... (ISHTI's own API, which trusts
local callers) or to a LAN router is refused instead of being read into the model's context.
No headless browser: JavaScript-only pages are reported as unreadable rather than scraped.
"""

import ipaddress
import os
import socket
import sys
from typing import Any, Dict
from urllib.parse import urljoin, urlsplit

import requests

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from tools.web.extract import ContentExtractor  # noqa: E402

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/140.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/pdf;q=0.8,*/*;q=0.5",
    "Accept-Language": "en-US,en;q=0.9",
}
MAX_BYTES = 15_000_000
MAX_REDIRECTS = 5


class BlockedURL(ValueError):
    """The URL points at this PC, the LAN or another non-public address, or is not plain http(s)."""


def check_public(url: str) -> str:
    """-> the host when every address it resolves to is public; raises BlockedURL otherwise.
    ponytail: resolve-then-connect leaves a DNS-rebinding window of milliseconds; closing it needs a custom urllib3
    connection that pins the checked IP. Redirects, literals and ordinary private names are all caught here."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname or parts.username or parts.password:
        raise BlockedURL("only plain http(s) web addresses can be read")
    host = parts.hostname
    try:
        infos = socket.getaddrinfo(host, parts.port or (443 if parts.scheme == "https" else 80), type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError) as exc:
        raise BlockedURL(f"{host} could not be resolved") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%", 1)[0])
        if not ip.is_global or ip.is_multicast:
            raise BlockedURL(f"{host} points to a private or local address; ISHTI only reads public web pages")
    return host


def safe_get(url: str, *, max_bytes: int = MAX_BYTES, stream_body: bool = False, **kw) -> requests.Response:
    """requests.get with SSRF protection: public hosts only, each redirect hop re-checked, body capped.
    stream_body=True returns the open response of the final public hop (for large downloads); the caller reads it."""
    kw.setdefault("headers", DEFAULT_HEADERS)
    kw.setdefault("timeout", 15)
    kw.pop("allow_redirects", None)
    kw.pop("stream", None)
    for _ in range(MAX_REDIRECTS + 1):
        check_public(url)
        r = requests.get(url, allow_redirects=False, stream=True, **kw)
        if r.status_code in (301, 302, 303, 307, 308):
            location = r.headers.get("location")
            r.close()
            if not location:
                raise BlockedURL("the site redirected without a destination")
            url = urljoin(url, location)
            continue
        if stream_body:
            r.url = url
            return r
        body = r.raw.read(max_bytes + 1, decode_content=True) if getattr(r, "raw", None) is not None else r.content
        r.close()
        if len(body) > max_bytes:
            raise BlockedURL(f"the page is larger than {max_bytes // 1_000_000} MB")
        r._content, r._content_consumed = body, True
        r.url = url
        return r
    raise BlockedURL("the site redirected too many times")


class WebFetcher:
    def __init__(self, timeout: int = 10):
        self.timeout = timeout

    def fetch(self, url: str, max_chars: int = 4000) -> Dict[str, Any]:
        """{url, title, content, date, status, method} for a public page; status 'failed' with an error otherwise."""
        error = "the page had no readable text (it may need JavaScript or a login)"
        try:
            res = safe_get(url, timeout=self.timeout)
            if res.status_code == 200 and len(res.text) > 150:
                parsed = ContentExtractor.extract_from_html(res.text, max_chars=max_chars)
                if parsed.get("content") and len(parsed["content"]) > 100:
                    return {"url": url, "title": parsed.get("title") or url, "content": parsed.get("content"),
                            "date": parsed.get("date", ""), "status": "success", "method": "direct_http"}
            elif res.status_code != 200:
                error = f"the site answered HTTP {res.status_code}"
        except BlockedURL as exc:
            error = str(exc)
        except requests.RequestException as exc:
            error = f"the site could not be reached ({type(exc).__name__})"
        return {"url": url, "title": url, "content": "", "date": "", "status": "failed", "error": error}


if __name__ == "__main__":
    for bad in ("http://127.0.0.1:8888/api/facts", "http://localhost/", "http://192.168.1.1/", "http://[::1]/",
                "file:///C:/Windows/win.ini", "http://user:pw@example.com/", "http://169.254.169.254/latest/meta-data"):
        try:
            check_public(bad)
        except BlockedURL:
            continue
        raise AssertionError(f"not blocked: {bad}")
    print("[self-check] fetch OK - loopback, LAN, link-local, file: and credential URLs are refused")
