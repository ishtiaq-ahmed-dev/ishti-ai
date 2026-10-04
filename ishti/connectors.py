"""The Connectors section: every outside account ISHTI can plug into, and its real state.

One registry - `ishti/oauth.PROVIDERS` - so adding a service is a dictionary entry, not new code.
This module only *reads* that registry and adds what a UI needs: the setup steps, the console link, the
scopes, the token lifetime, and a non-blocking way to run the consent flow.

Why the connect flow is threaded: `oauth.connect()` BLOCKS for up to 180 s waiting for the loopback
redirect. A web request must never hold that long, so `start()` runs it on a worker thread and the page
polls `progress()` until it finishes.

Secrets never leave this PC: Client ID/Secret go to ishti/config.json, tokens to
data/oauth/<service>.json, both of which are git-ignored.
"""
from __future__ import annotations

import json
import threading
import time

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
        "note": "Client secret is optional for a personal account on the device-code style flow.",
    },
    "github": {
        "console": "https://github.com/settings/developers",
        "steps": "GitHub -> Settings -> Developer settings -> OAuth Apps -> New OAuth App -> "
                 "Authorization callback URL: http://127.0.0.1 (any port is fine).",
        "note": "Works with a fine-grained PAT too, but this section uses the OAuth app.",
    },
    "meta": {
        "console": "https://developers.facebook.com/apps/",
        "steps": "Meta for Developers -> My Apps -> Create App -> add the **Facebook Login** product -> "
                 "Settings -> Valid OAuth redirect URIs: http://127.0.0.1 (any port). Live mode needs an "
                 "App Review before other people can use it.",
        "note": "Instagram uses the same Meta app - add the Instagram product to it.",
    },
    "linkedin": {
        "console": "https://www.linkedin.com/developers/apps",
        "steps": "LinkedIn -> Developers -> My apps -> Create app -> Products -> **Sign In with LinkedIn** "
                 "-> add http://127.0.0.1 to the redirect URLs.",
        "note": "LinkedIn requires 'Sign In with LinkedIn' plus 'Share on LinkedIn' for posting.",
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

# Live progress of the consent flow, per service. Small and in-memory: it only has to outlive one page load.
_PROGRESS: dict = {}


def progress(service: str) -> dict:
    return dict(_PROGRESS.get(service) or {"state": "idle", "reply": ""})


def _set(service: str, **kw) -> None:
    _PROGRESS[service] = {**progress(service), "at": time.time(), **kw}


# ---------------------------------------------------------------- one row per connector
def entry(service: str) -> dict:
    """One connector, with its live state - never a guess."""
    from ishti import oauth
    prov = oauth.PROVIDERS.get(service) or {}
    try:
        cid, _sec = oauth.config(service)
        has = bool(cid)
    except Exception:  # noqa: BLE001
        has = False
    try:
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
        "auth": prov.get("auth", ""),
        "scopes": prov.get("scopes") or [],
        "granted": (data.get("scope") or "").split() if isinstance(data.get("scope"), str) else [],
        "has_credentials": has,
        "connected": conn,
        "account": who,
        "has_refresh": bool(data.get("refresh_token")),
        "expires_at": data.get("expires_at") or 0,
        "connected_at": data.get("created") or 0,
        "state": "connected" if conn else ("ready" if has else "empty"),
        "setup": SETUP.get(fam, {}),
        "status_text": _safe_status(oauth, service),
        "progress": progress(service),
    }


def _safe_status(oauth, service: str) -> str:
    try:
        return oauth.status(service)
    except Exception as exc:  # noqa: BLE001
        return f"Could not read the {service} status: {type(exc).__name__}: {exc}"


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
        "note": "Client ID/Secret are stored in ishti/config.json and tokens in data/oauth/*.json - "
                "both on this PC only, and both git-ignored. A Client ID is not a secret; the secret is.",
    }


# ---------------------------------------------------------------- actions
def set_credentials(service: str, client_id: str, client_secret: str = "") -> dict:
    """Save the pasted Client ID/Secret. Never echoes the secret back."""
    from ishti import oauth
    if service not in oauth.PROVIDERS:
        return {"ok": False, "reply": f"{service} is not a connector ISHTI knows."}
    if not client_id.strip():
        return {"ok": False, "reply": "Paste the Client ID first."}
    msg = oauth.set_credentials(service, client_id, client_secret or "")
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
    if not oauth.config(service)[0]:
        return {"ok": False, "reply": f"Paste the {service} Client ID and secret first, then Connect."}
    if progress(service).get("state") == "waiting":
        return {"ok": True, "reply": "Still waiting for you to click Allow in the browser tab.", "waiting": True}

    _set(service, state="waiting", reply="Waiting for you to click Allow in the browser tab…")

    def _run():
        try:
            res = oauth.connect(service, timeout=180)
            _set(service, state="done" if res.get("ok") else "error", reply=res.get("reply", ""))
        except Exception as exc:  # noqa: BLE001
            _set(service, state="error", reply=f"{type(exc).__name__}: {exc}")

    threading.Thread(target=_run, name=f"connect-{service}", daemon=True).start()
    return {"ok": True, "waiting": True,
            "reply": f"Opened the {service} consent page. Click Allow - ISHTI is waiting."}


def disconnect(service: str) -> dict:
    """Revoke locally (and at the provider when we hold a token), so the tokens stop being usable."""
    from ishti import oauth
    if service not in oauth.PROVIDERS:
        return {"ok": False, "reply": f"{service} is not a connector ISHTI knows."}
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
        assert r["state"] in {"connected", "ready", "empty"}, r
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

