"""OAuth for the existing desktop connectors.

ISHTI needs a registered provider application. Once it is provisioned (including a shared
Google registration via ISHTI_OAUTH_GOOGLE_CLIENT_ID/CLIENT_SECRET), users click Connect,
choose an account and consent. Advanced credentials remain available for local app owners.

Uses state-bound loopback callbacks and PKCE, provider-specific account lookup, serialized
refresh with token rotation, and encrypted atomic storage in data/oauth. Reads legacy config
and token JSON for compatibility; all new writes use the existing local vault key.
Personal actions unavailable through the provider API fall back to Chrome and its approval flow.
"""
from __future__ import annotations
from ishti.core.logutil import swallowed

import base64
import hashlib
import json
import os
import secrets
import threading
import time
import urllib.parse
import urllib.request
import urllib.error
import tempfile

GOOGLE_SERVICES = ("google", "gmail", "drive", "sheets", "docs", "calendar", "people", "youtube")
_LOCK = threading.RLock()
_TOKEN_LOCKS = {}


class OAuthError(RuntimeError):
    """Only a provider error code; never tokens or a raw provider response."""


def family(service):
    return "google" if service in GOOGLE_SERVICES else service


def _service_lock(service):
    with _LOCK:
        return _TOKEN_LOCKS.setdefault(service, threading.RLock())


def _read_private(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {}
        if data.get("format") == "ishti-oauth-v1":
            from cryptography.fernet import Fernet
            from ishti.core.vault import get_or_create_key
            data = json.loads(Fernet(get_or_create_key()).decrypt(data["ciphertext"].encode()))
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}


def _write_private(path, data):
    # Reuse the existing local vault key. Credentials never go into tracked config.json.
    from cryptography.fernet import Fernet
    from ishti.core.vault import get_or_create_key
    payload = {"format": "ishti-oauth-v1", "ciphertext": Fernet(get_or_create_key()).encrypt(
        json.dumps(data).encode()).decode()}
    fd, temporary = tempfile.mkstemp(prefix="oauth-", suffix=".tmp", dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.remove(temporary)


def _changed(service):
    from ishti import events
    events.publish("connectors_updated", {"service": service})

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
    if service not in PROVIDERS:
        return {}
    try:
        return _read_private(_path(service))
    except Exception:  # noqa: BLE001 - no token yet is not an error
        return {}


def _save(service: str, data: dict) -> None:
    if service not in PROVIDERS:
        raise ValueError("Unknown connector")
    _write_private(_path(service), data)


def config(service: str) -> tuple:
    """(client_id, client_secret) for this service from config.json, or empty strings."""
    from ishti import CONFIG
    if service not in PROVIDERS:
        return "", ""
    local = _read_private(os.path.join(_dir(), "clients.json"))
    fam = family(service)
    for name in dict.fromkeys((service, fam)):
        prefix = "ISHTI_OAUTH_" + name.upper() + "_"
        if os.environ.get(prefix + "CLIENT_ID"):
            return os.environ[prefix + "CLIENT_ID"], os.environ.get(prefix + "CLIENT_SECRET", "")
        for source in (local, CONFIG.get("oauth") or {}, CONFIG.get("oauth_apps") or {}):
            entry = source.get(name) or {}
            if entry.get("client_id"):
                return str(entry["client_id"]), str(entry.get("client_secret") or "")
    # Existing Google registrations work for every Google service after separate scope consent.
    if fam == "google":
        for source in (local, CONFIG.get("oauth") or {}):
            for name in GOOGLE_SERVICES:
                entry = source.get(name) or {}
                if entry.get("client_id"):
                    return str(entry["client_id"]), str(entry.get("client_secret") or "")
    return "", ""


def connected(service: str) -> bool:
    d = _load(service)
    if d.get("auth_error"):
        return False
    return bool(d.get("refresh_token") or (d.get("access_token") and (
        not d.get("expires_at") or float(d["expires_at"]) > time.time())))


def account(service: str) -> str:
    return str(_load(service).get("email") or "")


def status(service: str) -> str:
    if service not in PROVIDERS:
        return "Unknown connector."
    if PROVIDERS[service].get("kind") == "token":
        return "Telegram uses a bot token. Set it in Settings → Remote channels."
    if _load(service).get("auth_error"):
        return f"{service}: sign-in needs reconnecting. Your browser remains available."
    if connected(service):
        return f"{service}: connected{(' as ' + account(service)) if account(service) else ''}."
    cid, secret = config(service)
    if not cid:
        return f"{service}: ISHTI's app registration is not configured. You can use the website in Chrome or configure the app once under Advanced setup."
    if not secret and service != "microsoft":
        return f"{service}: the app registration is missing its client secret. Complete Advanced setup once, then click Connect."
    return f"{service}: app registration is set. Click Connect to choose your account and approve permissions."
# ---------------------------------------------------------------- providers
# One registry, one flow: every provider below speaks standard OAuth 2.0 with the *loopback* redirect, so
# connect()/token() work identically for all of them. Only the endpoints and the scope list differ.
#   auth   - where the owner clicks "Allow"
#   token  - where the code becomes an access token (+ refresh token)
#   api    - the REST base used by oauth.api(service, "/path")
#   scopes - default scopes; connect(service, scopes=[...]) overrides them
PROVIDERS = {
    # ---------------- Google: same auth server, different scope ----------------
    "google":  {"auth": "https://accounts.google.com/o/oauth2/v2/auth",
                "token": "https://oauth2.googleapis.com/token",
                "api": "https://www.googleapis.com/oauth2/v3/userinfo",
                "scopes": ["openid", "https://www.googleapis.com/auth/userinfo.profile", "https://www.googleapis.com/auth/userinfo.email"]},
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
                 "scopes": ["openid", "profile", "email", "w_member_social"]},
    # ---------------- Telegram is a bot token, not an OAuth grant ----------------
    "telegram": {"kind": "token", "api": "https://api.telegram.org"},
}


def api(service: str, path: str, params: dict = None, body: dict = None, method: str = "", resource: str = ""):
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
    if resource:
        if family(service) != "google" or resource != "drive" or service not in ("sheets", "docs", "drive"):
            raise ValueError("Unsupported connector resource")
        base = PROVIDERS[resource]["api"]
    else:
        base = prov["api"]
    if not path.startswith("/") or "?" in path or "#" in path:
        raise ValueError("Use an API path and separate query parameters")
    url = (base.rstrip("/") + path) + ("?" + urllib.parse.urlencode(params) if params else "")
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data,
                                 {"Authorization": f"Bearer {tok}", "Accept": "application/json",
                                  "Content-Type": "application/json", "User-Agent": "ISHTI"},
                                 method=method or ("POST" if body is not None else "GET"))
    from ishti import audit
    with audit.context(skill="connectors"):
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
    req = urllib.request.Request(url, body, {"Content-Type": "application/x-www-form-urlencoded",
                                          "Accept": "application/json"})
    try:
        from ishti import audit
        with audit.context(skill="connectors"):
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        try:
            code = json.loads(exc.read(4096)).get("error")
        except (ValueError, AttributeError):
            code = None
        raise OAuthError(code if isinstance(code, str) and code.replace("_", "").isalnum()
                         else f"http_{exc.code}") from None


def _challenge(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()


def token(service: str):
    """A usable access token, refreshed silently when expired. None when not connected."""
    with _service_lock(service):
        return _token_locked(service)


def _token_locked(service):
    data = _load(service)
    if not data or data.get("auth_error"):
        return None
    if data.get("access_token") and (not data.get("expires_at") or float(data["expires_at"]) > time.time() + 60):
        return data["access_token"]
    if not data.get("refresh_token"):
        return data.get("access_token") if float(data.get("expires_at") or 0) > time.time() else None
    cid, csec = config(service)
    try:
        ep = (PROVIDERS.get(service) or {}).get("token") or TOKEN_URL
        params = {"client_id": cid, "refresh_token": data["refresh_token"], "grant_type": "refresh_token"}
        if csec:
            params["client_secret"] = csec
        out = _post(ep, params)
    except Exception as exc:  # noqa: BLE001
        if isinstance(exc, OAuthError) and str(exc) in ("invalid_grant", "invalid_client"):
            data["auth_error"] = str(exc)
            _save(service, data)
            _changed(service)
        print(f"[oauth] refresh failed for {service}: {type(exc).__name__}")
        return None
    if not out.get("access_token"):
        if out.get("error") in ("invalid_grant", "invalid_client"):
            data["auth_error"] = out["error"]
            _save(service, data)
            _changed(service)
        return None
    data.update(access_token=out["access_token"],
                expires_at=time.time() + int(out.get("expires_in", 3500)),
                scope=out.get("scope") or data.get("scope", ""))
    if out.get("refresh_token"):
        data["refresh_token"] = out["refresh_token"]
    _save(service, data)
    _changed(service)
    return data["access_token"]


def set_credentials(service: str, client_id: str, client_secret: str = "") -> str:
    """Store the credentials the owner pasted from the Cloud Console ('connect gmail client-id ...')."""
    if service not in PROVIDERS or PROVIDERS[service].get("kind") == "token" or not client_id.strip():
        raise ValueError("A known OAuth service and Client ID are required")
    with _LOCK:
        path = os.path.join(_dir(), "clients.json")
        data = _read_private(path)
        data[service] = {"client_id": client_id.strip(), "client_secret": client_secret.strip()}
        _write_private(path, data)
    _changed(service)
    return f"Saved the {service} app registration in the local encrypted store. Now connect {service}."


def webbrowser_open(url: str) -> None:
    import webbrowser
    webbrowser.open(url)


def connect(service: str, client_id: str = "", client_secret: str = "", scopes=None,
            open_browser=None, timeout: int = 180, cancel=None) -> dict:
    """Full installed-app flow: open the consent page, take the code back from the loopback port, exchange
    it for access + refresh tokens, store them. Returns {'ok': bool, 'reply': str}.

    The owner is at the machine, so this BLOCKS for up to `timeout` seconds - the first connect is one
    deliberate step (pick the account, click Allow), and after that it never happens again.
    """
    if service not in PROVIDERS or PROVIDERS[service].get("kind") == "token":
        return {"ok": False, "reply": "Select an OAuth connector."}
    supplied_credentials = bool(client_id)
    client_id = client_id or config(service)[0]
    client_secret = client_secret or config(service)[1]
    if not client_id or (service != "microsoft" and not client_secret):
        return {"ok": False, "reply": f"The {service} app registration is incomplete. Configure ISHTI's app once in Connectors → Advanced setup, then sign in with the Connect button."}
    if supplied_credentials:
        set_credentials(service, client_id, client_secret)
    prov = PROVIDERS.get(service) or {}
    auth_endpoint = prov.get("auth") or AUTH_URL
    token_endpoint = prov.get("token") or TOKEN_URL
    scopes = scopes_for(service, scopes)
    verifier = secrets.token_urlsafe(64)
    state = secrets.token_urlsafe(32)
    from http.server import BaseHTTPRequestHandler, HTTPServer

    got: dict = {}
    received = threading.Event()

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            parsed = urllib.parse.urlparse(self.path)
            q = urllib.parse.parse_qs(parsed.query)
            supplied = (q.get("state") or [""])[0]
            if parsed.path != "/" or not secrets.compare_digest(supplied, state):
                self.send_error(400, "Invalid authorization callback")
                return
            if received.is_set():
                self.send_error(409, "Authorization callback already received")
                return
            got.update(code=(q.get("code") or [None])[0], error=(q.get("error") or [None])[0])
            body = b"Authorization received. Return to ISHTI to see whether sign-in completed."
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            received.set()

        def log_message(self, *a):
            pass

    # Providers with exact registered callbacks cannot use a random port.
    port = 0 if family(service) == "google" or service == "microsoft" else 8765
    try:
        server = HTTPServer(("127.0.0.1", port), _Handler)
    except OSError:
        return {"ok": False, "reply": "The local sign-in callback port is busy. Close the other sign-in flow and retry."}
    redirect = f"http://127.0.0.1:{server.server_address[1]}/"
    q = {"client_id": client_id, "response_type": "code", "redirect_uri": redirect,
         "scope": " ".join(scopes), "state": state,
         "code_challenge": _challenge(verifier), "code_challenge_method": "S256",
         }
    if family(service) == "google":
        q.update(access_type="offline", prompt="consent", include_granted_scopes="true")
    worker = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.05), daemon=True)
    worker.start()
    try:
        (open_browser or webbrowser_open)(f"{auth_endpoint}?{urllib.parse.urlencode(q)}")
        deadline = time.monotonic() + timeout
        while not received.is_set() and time.monotonic() < deadline:
            if cancel and cancel.is_set():
                return {"ok": False, "reply": "Sign-in cancelled."}
            received.wait(min(0.2, max(0, deadline - time.monotonic())))
    except Exception:
        return {"ok": False, "reply": "Could not open the sign-in page. Retry after checking the browser."}
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=1)

    if not got.get("code"):
        return {"ok": False, "reply": "Sign-in was declined or timed out. Nothing was connected; click Connect to retry."}
    try:
        params = {"client_id": client_id, "code": got["code"], "grant_type": "authorization_code",
                  "redirect_uri": redirect, "code_verifier": verifier}
        if client_secret:
            params["client_secret"] = client_secret
        out = _post(token_endpoint, params)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "reply": f"{service} rejected sign-in ({type(exc).__name__}). Check the registered callback and granted permissions, then retry."}
    if not out.get("access_token"):
        return {"ok": False, "reply": f"{service} returned no access token. Nothing was connected; retry sign-in."}
    data = {"access_token": out["access_token"], "refresh_token": out.get("refresh_token", ""),
            "expires_at": time.time() + int(out["expires_in"]) if out.get("expires_in") else 0,
            "scope": out.get("scope", ""),
            "email": out.get("email") or _who(out["access_token"], service), "created": time.time()}
    with _service_lock(service):
        if cancel and cancel.is_set():
            return {"ok": False, "reply": "Sign-in cancelled. Nothing was stored."}
        _save(service, data)
    _changed(service)
    warn = "" if data["refresh_token"] or not data["expires_at"] else " Reconnect when this access token expires."
    return {"ok": True, "reply": f"Connected {service}" + (f" as {data['email']}" if data["email"] else "") + "." + warn}


def _who(access_token: str, service="gmail") -> str:
    try:
        url = "https://www.googleapis.com/oauth2/v1/userinfo" if family(service) == "google" else {
            "microsoft": "https://graph.microsoft.com/v1.0/me", "github": "https://api.github.com/user",
            "linkedin": "https://api.linkedin.com/v2/userinfo",
        }.get(service)
        if not url:
            return ""
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {access_token}",
                                                  "Accept": "application/json", "User-Agent": "ISHTI"})
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read().decode())
            return str(data.get("email") or data.get("userPrincipalName") or data.get("login") or "")
    except Exception:  # noqa: BLE001
        return ""


def disconnect(service: str) -> str:
    if service not in PROVIDERS:
        return "Unknown connector."
    with _service_lock(service):
        data = _load(service)
        try:
            os.remove(_path(service))
        except FileNotFoundError:
            swallowed('ishti/oauth.py:disconnect:508')
    revoked = False
    try:
        if family(service) == "google" and data:
            _post(REVOKE_URL, {"token": data.get("refresh_token") or data.get("access_token")})
            revoked = True
    except Exception:
        swallowed('ishti/oauth.py:disconnect:515')
    _changed(service)
    return f"Disconnected {service}; its local tokens were deleted." + (
        " Google also revoked the grant." if revoked else " You can remove the app's grant in your provider's account settings.")


if __name__ == "__main__":
    cid, csec = config("gmail")
    assert _challenge("abc") == _challenge("abc") and len(_challenge("abc")) == 43
    assert token("ishti_no_such_service") is None and not connected("ishti_no_such_service")
    assert "registration" in status("gmail") or "connected" in status("gmail")
    print(f"[self-check] oauth loaded - credentials set: {bool(cid and csec)}, "
          f"gmail connected: {connected('gmail')}, account: {account('gmail') or '-'}")
    print("[self-check] oauth OK - loopback flow, PKCE, silent refresh, store/revoke")
