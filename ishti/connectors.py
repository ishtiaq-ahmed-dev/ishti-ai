"""Connector catalog, consent progress, and routing to existing API read capabilities.

The provider registry lives in ishti.oauth. Consent runs on a worker with the connector's
network scope; progress and token changes publish on the existing WebSocket event bus.
Unsupported actions and unavailable grants return to real browser page inspection.
Credentials and tokens never appear in the catalog. New writes use encrypted local storage.
"""
from __future__ import annotations

import json
import threading
import time
import re

# Where to create the app, and what to tick - shown verbatim in the UI so nobody has to guess.
SETUP = {
    "google": {
        "console": "https://console.cloud.google.com/apis/credentials",
        "steps": "Cloud Console -> pick a project -> APIs & Services -> Credentials -> Create credentials "
                 "-> OAuth client ID -> Application type **Desktop app**. Then enable the API you want "
                 "(Gmail API, Drive API, Calendar API, Sheets API, People API) on the same project.",
        "note": "One Client ID/Secret works for gmail, drive, sheets, docs, calendar, people and youtube.",
    },
    "microsoft": {
        "console": "https://portal.azure.com/#view/Microsoft_AAD_RegisteredApps/ApplicationsListBlade",
        "steps": "Azure portal -> App registrations -> New registration -> Supported account types: "
                 "Personal accounts (so a personal Outlook works) -> add a **Mobile and desktop "
                 "application** redirect of http://127.0.0.1 (any port) -> copy the Application (client) ID.",
        "note": "Use a public desktop application with PKCE. A client secret is not required.",
    },
    "github": {
        "console": "https://github.com/settings/developers",
        "steps": "GitHub -> Settings -> Developer settings -> OAuth Apps -> New OAuth App -> "
                 "Authorization callback URL: http://127.0.0.1:8765/.",
        "note": "Works with a fine-grained PAT too, but this section uses the OAuth app.",
    },
    "meta": {
        "console": "https://developers.facebook.com/apps/",
        "steps": "Meta for Developers -> My Apps -> Create App -> add the **Facebook Login** product -> "
                 "Settings -> Valid OAuth redirect URIs: http://127.0.0.1:8765/. Live mode needs an "
                 "App Review before other people can use it.",
        "note": "Instagram uses the same Meta app - add the Instagram product to it.",
    },
    "linkedin": {
        "console": "https://www.linkedin.com/developers/apps",
        "steps": "LinkedIn -> Developers -> My apps -> Create app -> Products -> **Sign In with LinkedIn** "
                 "-> add http://127.0.0.1:8765/ to the redirect URLs.",
        "note": "Enable Sign In with LinkedIn using OpenID Connect and Share on LinkedIn. The API does not expose personal inbox messages or profile viewers; those requests use Chrome.",
    },
    "telegram": {
        "console": "https://t.me/BotFather",
        "steps": "Message **@BotFather** -> /newbot -> copy the bot token it gives you.",
        "note": "A bot token, not an OAuth grant - ISHTI talks to the Bot API as the bot.",
    },
}

# Which console card a provider belongs to.
FAMILY = {**{s: "google" for s in ("gmail", "drive", "sheets", "docs", "calendar", "people", "youtube")},
          "microsoft": "microsoft", "github": "github", "instagram": "meta",
          "facebook": "meta", "linkedin": "linkedin", "telegram": "telegram"}

WEB_URLS = {"gmail": "https://mail.google.com", "drive": "https://drive.google.com",
            "sheets": "https://sheets.google.com", "docs": "https://docs.google.com",
            "calendar": "https://calendar.google.com", "people": "https://contacts.google.com",
            "youtube": "https://www.youtube.com", "microsoft": "https://outlook.live.com/mail/",
            "github": "https://github.com", "instagram": "https://www.instagram.com",
            "facebook": "https://www.facebook.com", "linkedin": "https://www.linkedin.com",
            "telegram": "https://web.telegram.org"}
READ_CAPABILITIES = {
    "gmail": "Unread inbox, read or search mail. Other actions use Chrome with confirmation.",
    "drive": "List and search your Drive files.", "sheets": "List your Google Sheets files.",
    "docs": "List your Google Docs files.", "calendar": "Read upcoming Google Calendar events.",
    "people": "Read Google contacts.", "youtube": "Read your channel details.",
    "microsoft": "Read Outlook inbox and account details.", "github": "Read repositories, notifications and account details.",
    "linkedin": "Read the connected profile. Personal messages and profile viewers use Chrome.",
    "instagram": "Meta API requires a professional account. Personal account requests use Chrome.",
    "facebook": "Meta APIs cover approved Page operations. Personal notifications use Chrome.",
    "telegram": "Bot access is managed in Remote channels. Personal web account requests use Chrome.",
}
_SERVICE_WORDS = {
    "gmail": r"gmail|google\s+mail|email|e-mail",
    "drive": r"(?:google\s+)?drive|gdrive",
    "sheets": r"(?:google\s+)?sheets",
    "docs": r"google\s+docs",
    "calendar": r"google\s+calendar",
    "people": r"google\s+contacts",
    "microsoft": r"outlook|microsoft|onedrive",
    **{s: re.escape(s) for s in ("youtube", "github", "linkedin", "instagram", "facebook", "telegram")},
}


def service_for_request(text):
    services = [service for service, words in _SERVICE_WORDS.items()
                if re.search(r"\b(?:" + words + r")\b", text, re.I)]
    return services[0] if len(services) == 1 else None


def credential_secrets(text):
    """Redact legacy credential commands before logging; the UI fields remain the preferred path."""
    return re.findall(r"\bclient[\s_-]?secret\s*[:=]?\s*([\w.-]+)", text or "", re.I)


def try_request(text, session):
    """Use an existing read capability first. Return None for Chrome fallback, including unsupported actions.

    Never substitute an account/profile check for the user's request to read messages or send something.
    Mutation requests continue through the existing browser approval gate.
    """
    from ishti import oauth
    from ishti.skills import trace
    service = service_for_request(text)
    if not service or (session.get("pending") or {}).get("skill"):
        return None
    if not oauth.connected(service):
        trace(session, "connector", f"{service} is not connected; using Chrome", status="skip")
        return None
    if re.search(r"\b(?:send|reply|forward|delete|remove|post|comment|publish|create|update|upload|edit|pay|transfer|book|connect|disconnect)\b", text, re.I):
        trace(session, "connector", f"{service}: requested action needs the website approval flow", status="skip")
        return None
    if not re.search(r"\b(?:open|check|show|read|list|search|find|view|any|who|what|unread|inbox|notifications?)\b", text, re.I):
        return None
    try:
        def website(reason="this request needs page inspection"):
            trace(session, "connector", f"{service}: {reason}; using Chrome", status="skip")
            return None

        trace(session, "connector", f"checking {service} through its connected API", status="start")
        if service == "gmail":
            from ishti.skills.email import skill as email
            result = email._oauth_path("check my gmail" if re.fullmatch(r"\s*(?:open|visit)\s+(?:my\s+)?gmail[.!]?\s*", text, re.I) else text, session)
            if result is not None:
                trace(session, "connector", "Gmail returned the requested data", status="done")
                return {**result, "connector": service, "verified": True, "no_escalate": True}
            return website()
        if service in ("instagram", "facebook", "telegram"):
            return website("this personal web action is outside the connector's API coverage")
        if service == "linkedin":
            if re.search(r"\b(?:messages?|dms?|inbox|viewed|viewers|notifications?|feed|posts?)\b", text, re.I):
                return website()
            if not re.fullmatch(r"\s*(?:open|check|show|view|read)\s+(?:my\s+)?linkedin(?:\s+(?:account|profile))?[.!?]?\s*", text, re.I):
                return website()
            data = oauth.api(service, "/userinfo")
            reply = "Connected LinkedIn profile: " + str(data.get("name") or "name not returned")
        elif service == "github":
            if re.search(r"\bnotifications?\b", text, re.I):
                if not re.fullmatch(r"\s*(?:check|show|list|read|any)\s+(?:my\s+|new\s+|unread\s+)*(?:github\s+notifications?|notifications?\s+on\s+github)[.!?]?\s*", text, re.I):
                    return website()
                rows = oauth.api(service, "/notifications", {"per_page": 20})
                reply = "GitHub notifications:\n" + ("\n".join("- " + str(r.get("subject", {}).get("title") or "Untitled") for r in rows) or "No unread notifications.")
            elif re.search(r"\brepos?(?:itories)?\b", text, re.I):
                if not re.fullmatch(r"\s*(?:check|show|list|read|view)\s+(?:my\s+)?github\s+repos?(?:itories)?[.!?]?\s*", text, re.I):
                    return website()
                rows = oauth.api(service, "/user/repos", {"per_page": 20, "sort": "updated"})
                reply = "Recently updated GitHub repositories:\n" + ("\n".join("- " + str(r.get("full_name")) for r in rows) or "No repositories returned.")
            elif re.fullmatch(r"\s*(?:open|check|show|view)\s+(?:my\s+)?github(?:\s+(?:account|profile))?[.!?]?\s*", text, re.I):
                data = oauth.api(service, "/user")
                reply = "Connected GitHub account: " + str(data.get("login") or "name not returned")
            else:
                return website()
        elif service in ("drive", "docs", "sheets"):
            words = _SERVICE_WORDS[service]
            listing = re.fullmatch(r"\s*(?:open|check|show|list|view|read)\s+(?:my\s+)?(?:" + words + r")(?:\s+files)?[.!?]?\s*", text, re.I)
            search = re.fullmatch(r"\s*(?:search|find)\s+(?:my\s+)?(?:" + words + r")\s+for\s+(.+?)\s*", text, re.I)
            if not listing and not search:
                return website()
            query = "trashed = false"
            if service in ("docs", "sheets"):
                query += " and mimeType = 'application/vnd.google-apps." + ("document" if service == "docs" else "spreadsheet") + "'"
            if search:
                term = search[1].strip(" .!?").replace("\\", "\\\\").replace("'", "\\'")
                query += f" and name contains '{term}'"
            rows = oauth.api(service, "/files", {"pageSize": 20, "q": query,
                "fields": "files(id,name,webViewLink)", "orderBy": "modifiedTime desc"}, resource="drive").get("files") or []
            reply = f"{service.title()} files (up to 20):\n" + ("\n".join("- " + str(r.get("name") or "Untitled") for r in rows) or "No matching files.")
        elif service == "calendar":
            if not re.fullmatch(r"\s*(?:open|check|show|list|view|read)\s+(?:my\s+)?(?:upcoming\s+)?google\s+calendar(?:\s+(?:events|appointments|schedule))?[.!?]?\s*", text, re.I):
                return website()
            import datetime
            rows = oauth.api(service, "/calendars/primary/events", {"timeMin": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "singleEvents": "true", "orderBy": "startTime", "maxResults": 20}).get("items") or []
            reply = "Upcoming Google Calendar events:\n" + ("\n".join("- " + str(r.get("summary") or "Untitled") + " · " + str(r.get("start", {}).get("dateTime") or r.get("start", {}).get("date") or "") for r in rows) or "No upcoming events.")
        elif service == "people":
            if not re.fullmatch(r"\s*(?:open|check|show|list|view|read)\s+(?:my\s+)?google\s+contacts[.!?]?\s*", text, re.I):
                return website()
            rows = oauth.api(service, "/people/me/connections", {"personFields": "names,emailAddresses", "pageSize": 20}).get("connections") or []
            reply = "Google contacts (up to 20):\n" + ("\n".join("- " + str((r.get("names") or [{}])[0].get("displayName") or "Unnamed contact") for r in rows) or "No contacts returned.")
        elif service == "microsoft":
            if "onedrive" in text.lower():
                return website()
            if re.search(r"\b(?:mail|inbox|messages?|unread)\b", text, re.I):
                if not re.fullmatch(r"\s*(?:open|check|show|list|read|any)\s+(?:my\s+|new\s+|unread\s+)*(?:outlook|microsoft)\s+(?:inbox|mail|emails?|messages?)[.!?]?\s*", text, re.I):
                    return website()
                params = {"$top": 20, "$select": "subject,from,isRead,receivedDateTime", "$orderby": "receivedDateTime desc"}
                if re.search(r"\bunread\b", text, re.I):
                    params["$filter"] = "isRead eq false"
                rows = oauth.api(service, "/me/messages", params).get("value") or []
                reply = "Recent Outlook messages:\n" + ("\n".join("- " + str(r.get("subject") or "No subject") for r in rows) or "No messages returned.")
            elif re.fullmatch(r"\s*(?:open|check|show|view)\s+(?:my\s+)?(?:outlook|microsoft)(?:\s+(?:account|profile))?[.!?]?\s*", text, re.I):
                data = oauth.api(service, "/me")
                reply = "Connected Microsoft account: " + str(data.get("displayName") or data.get("userPrincipalName") or "name not returned")
            else:
                return website()
        elif service == "youtube":
            if not re.fullmatch(r"\s*(?:open|check|show|list|view|read)\s+my\s+youtube\s+channel(?:\s+(?:details|information))?[.!?]?\s*", text, re.I):
                return website()
            rows = oauth.api(service, "/channels", {"part": "snippet", "mine": "true"}).get("items") or []
            reply = "Your YouTube channels:\n" + ("\n".join("- " + str(r.get("snippet", {}).get("title") or "Untitled") for r in rows) or "No channel returned.")
        else:
            return website()
        trace(session, "connector", f"{service} returned the requested data", status="done")
        return {"reply": reply, "sources": [{"title": service.title(), "url": WEB_URLS[service]}],
                "verified": True, "no_escalate": True, "connector": service}
    except Exception as exc:
        trace(session, "connector", f"{service} API unavailable ({type(exc).__name__}); using Chrome", status="error")
        return None

# Live progress of the consent flow, per service. Small and in-memory: it only has to outlive one page load.
_PROGRESS: dict = {}
_PROGRESS_LOCK = threading.RLock()
_CANCEL: dict = {}


def progress(service: str) -> dict:
    with _PROGRESS_LOCK:
        return dict(_PROGRESS.get(service) or {"state": "idle", "reply": ""})


def _set(service: str, **kw) -> None:
    with _PROGRESS_LOCK:
        _PROGRESS[service] = {**progress(service), "at": time.time(), **kw}
    from ishti import events
    events.publish("connectors_updated", {"service": service})


# ---------------------------------------------------------------- one row per connector
def entry(service: str) -> dict:
    """One connector, with its live state - never a guess."""
    from ishti import oauth
    prov = oauth.PROVIDERS.get(service) or {}
    try:
        cid, _sec = oauth.config(service)
        has = bool(cid and (_sec or service == "microsoft"))
    except Exception:  # noqa: BLE001
        has = False
    try:
        if prov.get("kind") == "token":
            from ishti.channels import security
            conn = bool(security.has_token())
        else:
            conn = oauth.connected(service)
    except Exception:  # noqa: BLE001
        conn = False
    try:
        who = oauth.account(service)
    except Exception:  # noqa: BLE001
        who = ""
    try:
        data = oauth._load(service)  # noqa: SLF001 - expiry is token state only oauth can answer
    except Exception:  # noqa: BLE001
        data = {}
    fam = FAMILY.get(service, "google")
    kind = prov.get("kind") or "oauth"
    return {
        "service": service,
        "name": service.capitalize(),
        "family": fam,
        "kind": kind,                      # "oauth" = consent screen, "token" = paste a secret
        "api": prov.get("api", ""),
        "website": WEB_URLS.get(service, ""),
        "capabilities": READ_CAPABILITIES.get(service, ""),
        "auth": prov.get("auth", ""),
        "scopes": prov.get("scopes") or [],
        "granted": (data.get("scope") or "").split() if isinstance(data.get("scope"), str) else [],
        "has_credentials": has,
        "one_click": has and kind == "oauth",
        "connected": conn,
        "account": who,
        "has_refresh": bool(data.get("refresh_token")),
        "expires_at": data.get("expires_at") or 0,
        "connected_at": data.get("created") or 0,
        "state": "connected" if conn else ("reconnect" if data.get("auth_error") else "ready" if has else "empty"),
        "setup": SETUP.get(fam, {}),
        "status_text": _safe_status(oauth, service),
        "progress": progress(service),
    }


def _safe_status(oauth, service: str) -> str:
    try:
        return oauth.status(service)
    except Exception as exc:  # noqa: BLE001
        return f"Could not read the {service} status ({type(exc).__name__})."


def catalog() -> list:
    """Every OAuth/token connector ISHTI knows, in one list."""
    from ishti import oauth
    return [entry(s) for s in oauth.providers()]


def summary() -> dict:
    rows = catalog()
    return {
        "connectors": rows,
        "connected": sum(1 for r in rows if r["connected"]),
        "ready": sum(1 for r in rows if not r["connected"] and r["has_credentials"]),
        "total": len(rows),
        "families": sorted({r["family"] for r in rows}),
        "note": "Sign in with Connect when ISHTI's app registration is configured. App credentials and tokens are encrypted in local data/oauth storage; legacy config is read for compatibility. Advanced setup is needed once by the app owner. Telegram uses Settings → Remote channels.",
    }


# ---------------------------------------------------------------- actions
def set_credentials(service: str, client_id: str, client_secret: str = "") -> dict:
    """Save the pasted Client ID/Secret. Never echoes the secret back."""
    from ishti import oauth
    if service not in oauth.PROVIDERS:
        return {"ok": False, "reply": f"{service} is not a connector ISHTI knows."}
    if not client_id.strip():
        return {"ok": False, "reply": "Paste the Client ID first."}
    if oauth.PROVIDERS[service].get("kind") == "token":
        return {"ok": False, "reply": "Configure the Telegram bot token in Settings → Remote channels."}
    try:
        msg = oauth.set_credentials(service, client_id, client_secret or "")
    except Exception as exc:
        return {"ok": False, "reply": f"Could not save app registration ({type(exc).__name__})."}
    _set(service, state="ready", reply=msg)
    return {"ok": True, "reply": msg, "connector": entry(service)}


def start(service: str) -> dict:
    """Kick off the consent flow on a worker thread and return at once.

    oauth.connect() blocks for up to 180 s on the loopback redirect, so it must never run inside the
    request. The page polls GET /api/connectors/{service}/progress until the state leaves "waiting".
    """
    from ishti import oauth
    if service not in oauth.PROVIDERS:
        return {"ok": False, "reply": f"{service} is not a connector ISHTI knows."}
    prov = oauth.PROVIDERS[service] or {}
    if (prov.get("kind") or "oauth") == "token":
        return {"ok": False, "reply": f"{service} uses a token, not a consent screen - paste the secret below."}
    cid, secret = oauth.config(service)
    if not cid or (service != "microsoft" and not secret):
        return {"ok": False, "reply": oauth.status(service)}
    with _PROGRESS_LOCK:
        if progress(service).get("state") == "waiting":
            return {"ok": True, "reply": "Sign-in is already running. Complete the browser consent screen.", "waiting": True}
        cancel = threading.Event()
        _CANCEL[service] = cancel
        _set(service, state="waiting", reply="Starting sign-in. Complete the consent screen in your browser.")

    def _run():
        try:
            from ishti import audit
            with audit.context(skill="connectors", initiator="owner"):
                res = oauth.connect(service, timeout=180, cancel=cancel)
            if not cancel.is_set():
                _set(service, state="done" if res.get("ok") else "error", reply=res.get("reply", ""))
        except Exception as exc:  # noqa: BLE001
            if not cancel.is_set():
                _set(service, state="error", reply=f"Sign-in failed ({type(exc).__name__}). Retry Connect.")

    threading.Thread(target=_run, name=f"connect-{service}", daemon=True).start()
    return {"ok": True, "waiting": True,
            "reply": f"Starting {service} sign-in. Complete the consent screen in your browser."}


def disconnect(service: str) -> dict:
    """Revoke locally (and at the provider when we hold a token), so the tokens stop being usable."""
    from ishti import oauth
    if service not in oauth.PROVIDERS:
        return {"ok": False, "reply": f"{service} is not a connector ISHTI knows."}
    if oauth.PROVIDERS[service].get("kind") == "token":
        return {"ok": False, "reply": "Disconnect Telegram in Settings → Remote channels."}
    with _PROGRESS_LOCK:
        if service in _CANCEL:
            _CANCEL[service].set()
    from ishti import audit
    with audit.context(skill="connectors", initiator="owner"):
        msg = oauth.disconnect(service)
    _set(service, state="idle", reply=msg)
    return {"ok": True, "reply": msg, "connector": entry(service)}


# ---------------------------------------------------------------- self-check
if __name__ == "__main__":
    s = summary()
    assert s["total"] >= 13, s["total"]
    assert s["connected"] + s["ready"] <= s["total"]
    names = {r["service"] for r in s["connectors"]}
    for need in ("gmail", "drive", "github", "instagram", "linkedin", "telegram", "microsoft"):
        assert need in names, f"missing connector {need}"
    for r in s["connectors"]:
        assert r["state"] in {"connected", "ready", "empty", "reconnect"}, r
        assert r["setup"].get("steps"), f"{r['service']} has no setup steps"
        # No credential *value* may travel back out. (The setup/status text legitimately says the words
        # "Client ID/Secret" as instructions - what must not appear is the secret itself.)
        from ishti import oauth as _o
        _cid, _sec = _o.config(r["service"])
        blob = json.dumps(r, default=str)
        for val, what in ((_sec, "client_secret"), (_cid, "client_id")):
            if val and len(str(val)) >= 8:
                assert str(val) not in blob, f"{r['service']} leaked its {what}"
        assert "client_secret" not in r, r["service"]
    bad = start("telegram")
    assert bad["ok"] is False and "token" in bad["reply"], bad
    # deliberately NOT calling start() on a real service here: it would open a consent page
    assert set_credentials("nope", "x")["ok"] is False
    assert disconnect("nope")["ok"] is False
    print(f"{s['total']} connectors, {s['connected']} connected, {s['ready']} ready; "
          f"families: {', '.join(s['families'])}")
    print("[self-check] connectors OK - one registry, real state, no secret ever returned")

