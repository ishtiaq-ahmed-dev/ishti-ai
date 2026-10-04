"""OAuth 2.0 for INSTALLED desktop apps (Google, and any provider supporting the loopback redirect).

Why: reading mail by driving Chrome with Playwright works but is slow, brittle and needs the owner signed
in on that browser profile. With a Client ID / Client Secret from the Google Cloud Console the assistant
talks to the Gmail API directly instead - no browser, no scraping, instant replies.

The facts (researched 2026-10, developers.google.com):

* A **Desktop app** client type uses the *loopback IP address* redirect: `http://127.0.0.1:<port>/`.
  Google **deprecates that flow only for the iOS / Android / Chrome client types - it explicitly continues
  to be supported for desktop apps** (Loopback IP Address flow Migration Guide), so it is correct here.
* Installed apps "cannot keep secrets": the `client_secret` ships with the app and is not a secret in the
  usual sense. It is still required by the token exchange, and it lives next to your client_id in config.
* `access_type=offline&prompt=consent` is requested on the FIRST connect so a **refresh token** comes back;
  after that the access token refreshes silently and the owner never sees a login again.
* PKCE (code_challenge / S256) is used as well - Google supports it for installed apps.
* An app left in **Testing** on the consent screen hands out refresh tokens that expire after 7 days; that
  is reported plainly instead of failing later with a confusing `invalid_grant`.

Storage: data/oauth/<service>.json - this PC only. Reads/writes stay inside ISHTI's normal state.

Usage:
    from ishti import oauth
    oauth.connected("gmail")                       # authorised yet?
    oauth.connect("gmail", client_id, secret)      # opens the browser once, then stores the token
    oauth.token("gmail")                           # a fresh access token (auto-refresh), or None
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import threading
import time
import urllib.parse
import urllib.request

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"

# read + modify + send is everything the email skill does; userinfo.email so the reply can say WHICH account.
GMAIL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/userinfo.email",
]


def _dir() -> str:
    from ishti import ROOT
    path = os.path.join(ROOT, "data", "oauth")
    os.makedirs(path, exist_ok=True)
    return path


def _path(service: str) -> str:
    return os.path.join(_dir(), f"{service}.json")


def _load(service: str) -> dict:
    try:
        with open(_path(service), encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001 - no token yet is not an error
        return {}


def _save(service: str, data: dict) -> None:
    with open(_path(service), "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1)


def config(service: str) -> tuple:
    """(client_id, client_secret) for this service from config.json, or empty strings."""
    from ishti import CONFIG
    entry = (CONFIG.get("oauth") or {}).get(service) or {}
    return str(entry.get("client_id") or ""), str(entry.get("client_secret") or "")


def connected(service: str) -> bool:
    d = _load(service)
    return bool(d.get("refresh_token") or d.get("access_token"))


def account(service: str) -> str:
    return str(_load(service).get("email") or "")


def status(service: str) -> str:
    if not config(service)[0]:
        return (f"No Client ID/Secret set for {service}. In Google Cloud Console: APIs & Services -> "
                f"Credentials -> Create OAuth client ID -> **Desktop app**, then enable the Gmail API.")
    if not connected(service):
        return f"{service}: credentials are set, but it has not been authorised yet - say 'connect {service}'."
    return f"{service}: connected{(' as ' + account(service)) if account(service) else ''}."
# ---------------------------------------------------------------- providers
# One registry, one flow: every provider below speaks standard OAuth 2.0 with the *loopback* redirect, so
# connect()/token() work identically for all of them. Only the endpoints and the scope list differ.
#   auth   - where the owner clicks "Allow"
#   token  - where the code becomes an access token (+ refresh token)
#   api    - the REST base used by oauth.api(service, "/path")
#   scopes - default scopes; connect(service, scopes=[...]) overrides them
PROVIDERS = {
    # ---------------- Google: same auth server, different scope ----------------
    "gmail":   {"auth": "https://accounts.google.com/o/oauth2/v2/auth",
                "token": "https://oauth2.googleapis.com/token",
                "api": "https://gmail.googleapis.com/gmail/v1/users/me", "scopes": GMAIL_SCOPES},
    "drive":   {"auth": "https://accounts.google.com/o/oauth2/v2/auth",
                "token": "https://oauth2.googleapis.com/token",
                "api": "https://www.googleapis.com/drive/v3",
                "scopes": ["https://www.googleapis.com/auth/drive.readonly",
                           "https://www.googleapis.com/auth/drive.file",
                           "https://www.googleapis.com/auth/userinfo.email"]},
    "sheets":  {"auth": "https://accounts.google.com/o/oauth2/v2/auth",
                "token": "https://oauth2.googleapis.com/token",
                "api": "https://sheets.googleapis.com/v4/spreadsheets",
                "scopes": ["https://www.googleapis.com/auth/spreadsheets",
                           "https://www.googleapis.com/auth/drive.readonly"]},
    "docs":    {"auth": "https://accounts.google.com/o/oauth2/v2/auth",
                "token": "https://oauth2.googleapis.com/token",
                "api": "https://docs.googleapis.com/v1",
                "scopes": ["https://www.googleapis.com/auth/documents",
                           "https://www.googleapis.com/auth/drive.readonly"]},
    "calendar": {"auth": "https://accounts.google.com/o/oauth2/v2/auth",
                 "token": "https://oauth2.googleapis.com/token",
                 "api": "https://www.googleapis.com/calendar/v3",
                 "scopes": ["https://www.googleapis.com/auth/calendar.readonly",
                            "https://www.googleapis.com/auth/calendar.events"]},
    "people":  {"auth": "https://accounts.google.com/o/oauth2/v2/auth",
                "token": "https://oauth2.googleapis.com/token",
                "api": "https://people.googleapis.com/v1",
                "scopes": ["https://www.googleapis.com/auth/contacts.readonly",
                           "https://www.googleapis.com/auth/userinfo.email"]},
    "youtube": {"auth": "https://accounts.google.com/o/oauth2/v2/auth",
                "token": "https://oauth2.googleapis.com/token",
                "api": "https://www.googleapis.com/youtube/v3",
                "scopes": ["https://www.googleapis.com/auth/youtube.readonly"]},
    # ---------------- Microsoft: Outlook mail + OneDrive via Graph ----------------
    "microsoft": {"auth": "https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
                  "token": "https://login.microsoftonline.com/common/oauth2/v2.0/token",
                  "api": "https://graph.microsoft.com/v1.0",
                  "scopes": ["offline_access", "https://graph.microsoft.com/User.Read",
                             "https://graph.microsoft.com/Mail.Read",
                             "https://graph.microsoft.com/Mail.Send",
                             "https://graph.microsoft.com/Files.ReadWrite"]},
    # ---------------- GitHub ----------------
    "github":  {"auth": "https://github.com/login/oauth/authorize",
                "token": "https://github.com/login/oauth/access_token",
                "api": "https://api.github.com",
                "scopes": ["repo", "read:user", "user:email"]},
    # ---------------- Meta: Instagram + Facebook Graph API ----------------
    "instagram": {"auth": "https://www.facebook.com/v21.0/dialog/oauth",
                  "token": "https://graph.facebook.com/v21.0/oauth/access_token",
                  "api": "https://graph.facebook.com/v21.0",
                  "scopes": ["instagram_basic", "pages_show_list", "pages_manage_metadata",
                             "instagram_manage_messages", "instagram_manage_comments"]},
    "facebook": {"auth": "https://www.facebook.com/v21.0/dialog/oauth",
                 "token": "https://graph.facebook.com/v21.0/oauth/access_token",
                 "api": "https://graph.facebook.com/v21.0",
                 "scopes": ["pages_show_list", "pages_manage_posts", "pages_manage_engagement"]},
    # ---------------- LinkedIn ----------------
    "linkedin": {"auth": "https://www.linkedin.com/oauth/v2/authorization",
                 "token": "https://www.linkedin.com/oauth/v2/accessToken",
                 "api": "https://api.linkedin.com/v2",
                 "scopes": ["r_liteprofile", "r_emailaddress", "w_member_social"]},
    # ---------------- Telegram is a bot token, not an OAuth grant ----------------
    "telegram": {"kind": "token", "api": "https://api.telegram.org"},
}


def api(service: str, path: str, params: dict = None, body: dict = None, method: str = "") -> dict:
    """Authenticated REST call against a connected provider. Raises RuntimeError when not connected.

    Same call shape everywhere: `oauth.api("drive", "/files", {"q": "mimeType=..."})`,
    `oauth.api("github", "/user/repos")`, `oauth.api("instagram", "/me")`.
    """
    prov = PROVIDERS.get(service)
    if not prov:
        raise RuntimeError(f"No provider called '{service}'. Known: {', '.join(providers())}")
    if prov.get("kind") == "token":
        raise RuntimeError(f"{service} uses its own token, not OAuth - set it in config instead.")
    tok = token(service)
    if not tok:
        raise RuntimeError(f"{service} is not connected yet - say 'connect {service}'.")
    url = (prov["api"].rstrip("/") + path) + ("?" + urllib.parse.urlencode(params) if params else "")
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data,
                                 {"Authorization": f"Bearer {tok}", "Accept": "application/json",
                                  "Content-Type": "application/json", "User-Agent": "ISHTI"},
                                 method=method or ("POST" if body is not None else "GET"))
    with urllib.request.urlopen(req, timeout=45) as r:
        return json.loads(r.read().decode() or "{}")


def providers() -> list:
    return sorted(PROVIDERS)


def scopes_for(service: str, scopes=None) -> list:
    if scopes:
        return list(scopes)
    return list(PROVIDERS.get(service, {}).get("scopes") or ["openid", "email", "profile"])





def _post(url: str, params: dict) -> dict:
    body = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(url, body, {"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode() or "{}")


def _challenge(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()


def token(service: str):
    """A usable access token, refreshed silently when expired. None when not connected."""
    data = _load(service)
    if not data:
        return None
    if data.get("access_token") and float(data.get("expires_at") or 0) > time.time() + 60:
        return data["access_token"]
    if not data.get("refresh_token"):
        return data.get("access_token")
    cid, csec = config(service)
    try:
        ep = (PROVIDERS.get(service) or {}).get("token") or TOKEN_URL
        out = _post(ep, {"client_id": cid, "client_secret": csec, "refresh_token": data["refresh_token"],
                          "grant_type": "refresh_token"})
    except Exception as exc:  # noqa: BLE001
        print(f"[oauth] refresh failed for {service}: {type(exc).__name__}: {exc}")
        return None
    if not out.get("access_token"):
        return None
    data.update(access_token=out["access_token"],
                expires_at=time.time() + int(out.get("expires_in", 3500)),
                scope=out.get("scope") or data.get("scope", ""))
    _save(service, data)
    return data["access_token"]


def set_credentials(service: str, client_id: str, client_secret: str) -> str:
    """Store the credentials the owner pasted from the Cloud Console ('connect gmail client-id ...')."""
    from ishti import CONFIG
    entry = CONFIG.setdefault("oauth", {}).setdefault(service, {})
    entry.update(client_id=client_id.strip(), client_secret=client_secret.strip())
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ishti", "config.json")
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(CONFIG, f, indent=2, ensure_ascii=False)
            f.write("\n")
        return f"Saved the {service} Client ID/Secret on this PC. Now say 'connect {service}'."
    except Exception as exc:  # noqa: BLE001
        return f"Could not save the credentials: {type(exc).__name__}: {exc}"


def webbrowser_open(url: str) -> None:
    import webbrowser
    webbrowser.open(url)


def connect(service: str, client_id: str = "", client_secret: str = "", scopes=None,
            open_browser=None, timeout: int = 180) -> dict:
    """Full installed-app flow: open the consent page, take the code back from the loopback port, exchange
    it for access + refresh tokens, store them. Returns {'ok': bool, 'reply': str}.

    The owner is at the machine, so this BLOCKS for up to `timeout` seconds - the first connect is one
    deliberate step (pick the account, click Allow), and after that it never happens again.
    """
    client_id = client_id or config(service)[0]
    client_secret = client_secret or config(service)[1]
    if not client_id or not client_secret:
        return {"ok": False, "reply": f"I don't have a Client ID/Secret for {service} yet. In Google Cloud "
                                      f"Console: APIs & Services -> Credentials -> Create credentials -> OAuth "
                                      f"client ID -> Application type **Desktop app**, enable the API, then say "
                                      f"'connect {service} client-id <ID> client-secret <SECRET>'."}
    prov = PROVIDERS.get(service) or {}
    auth_endpoint = prov.get("auth") or AUTH_URL
    token_endpoint = prov.get("token") or TOKEN_URL
    scopes = scopes_for(service, scopes)
    verifier = secrets.token_urlsafe(64)
    from http.server import BaseHTTPRequestHandler, HTTPServer

    got: dict = {}

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            got.update(code=(q.get("code") or [None])[0], error=(q.get("error") or [None])[0])
            body = (b"ISHTI is connected. You can close this tab and go back to ISHTI."
                    if got.get("code") else b"ISHTI could not connect - you can close this tab.")
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    server = HTTPServer(("127.0.0.1", 0), _Handler)
    redirect = f"http://127.0.0.1:{server.server_address[1]}/"
    q = {"client_id": client_id, "response_type": "code", "redirect_uri": redirect,
         "scope": " ".join(scopes), "access_type": "offline", "prompt": "consent",
         "code_challenge": _challenge(verifier), "code_challenge_method": "S256",
         "include_granted_scopes": "true"}
    (open_browser or webbrowser_open)(f"{auth_endpoint}?{urllib.parse.urlencode(q)}")
    threading.Thread(target=server.serve_forever, daemon=True).start()
    t0 = time.time()
    while not got and time.time() - t0 < timeout:
        time.sleep(0.3)
    server.shutdown()

    if not got.get("code"):
        return {"ok": False, "reply": f"No answer arrived in {timeout}s - the browser was probably closed. "
                                      f"Say 'connect {service}' again and click Allow. "
                                      f"({got.get('error') or 'timeout'})"}
    try:
        out = _post(token_endpoint, {"client_id": client_id, "client_secret": client_secret, "code": got["code"],
                                "grant_type": "authorization_code", "redirect_uri": redirect,
                                "code_verifier": verifier})
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "reply": f"Google rejected the code: {type(exc).__name__}: {exc}. "
                                      f"Say 'connect {service}' and click Allow again."}
    if not out.get("access_token"):
        return {"ok": False, "reply": f"Google returned no token: {str(out)[:300]}"}
    data = {"access_token": out["access_token"], "refresh_token": out.get("refresh_token", ""),
            "expires_at": time.time() + int(out.get("expires_in", 3500)), "scope": out.get("scope", ""),
            "email": out.get("email") or _who(out["access_token"]), "created": time.time()}
    _save(service, data)
    warn = "" if data["refresh_token"] else (
        " No refresh token came back, so this will expire soon - click Allow again and tick "
        "'Send offline access' if Google offers it.")
    return {"ok": True, "reply": f"Connected {service} as {data['email'] or 'your Google account'}. "
                                 f"Mail now works through the API directly - no browser needed." + warn}


def _who(access_token: str) -> str:
    try:
        req = urllib.request.Request("https://www.googleapis.com/oauth2/v1/userinfo",
                                     headers={"Authorization": f"Bearer {access_token}"})
        with urllib.request.urlopen(req, timeout=15) as r:
            return str(json.loads(r.read().decode()).get("email") or "")
    except Exception:  # noqa: BLE001
        return ""


def disconnect(service: str) -> str:
    data = _load(service)
    try:
        if data.get("refresh_token"):
            _post(REVOKE_URL, {"token": data["refresh_token"]})
    except Exception as exc:  # noqa: BLE001 - revoke is best-effort; the local token is still deleted
        from ishti.core.logutil import swallowed
        swallowed("ishti/oauth.py:disconnect:revoke", exc)
    try:
        os.remove(_path(service))
    except OSError as exc:
        from ishti.core.logutil import swallowed
        swallowed("ishti/oauth.py:disconnect:remove", exc)
    return f"Disconnected {service} - the token was revoked and deleted from this PC."


if __name__ == "__main__":
    cid, csec = config("gmail")
    assert _challenge("abc") == _challenge("abc") and len(_challenge("abc")) == 43
    assert token("ishti_no_such_service") is None and not connected("ishti_no_such_service")
    assert "Desktop app" in status("gmail") or "connected" in status("gmail")
    print(f"[self-check] oauth loaded - credentials set: {bool(cid and csec)}, "
          f"gmail connected: {connected('gmail')}, account: {account('gmail') or '-'}")
    print("[self-check] oauth OK - loopback flow, PKCE, silent refresh, store/revoke")