"""Integration pills (WP-COUCOU, matrix COU-08; coucou Poller.swift / integrations.rs).

OPT-IN and OFF by default - coucou's posture, kept:
  - providers: Stripe, n8n, GitHub, Vercel, Resend, Notion, Cal.com (read-only status)
  - until the owner explicitly opts in: NO network fetch, NO credential read, ever
  - refresh cadence ~5 min (REFRESH_S), PAUSED while the island is idle
  - credentials come from Windows Credential Manager only for enabled providers
    (win_credential) - this module never stores a token itself

Everything is injectable (fetch/cred/now) so tests prove the OFF-by-default contract
without touching the network or the owner's real credential store.
Self-check: python desktop/overlay/integrations.py
"""
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

PROVIDERS = ("stripe", "n8n", "github", "vercel", "resend", "notion", "cal")
REFRESH_S = 300                          # ~5 minutes, like coucou's poller

_opted_in = set()                        # explicit opt-in registry (Settings surface)


def opt_in(provider: str, on: bool = True) -> bool:
    """The owner flips this in Settings; nothing else enables a provider."""
    global _opted_in
    provider = str(provider).lower()
    if provider not in PROVIDERS:
        return False
    if on:
        _opted_in.add(provider)
    else:
        _opted_in.discard(provider)
    return provider in _opted_in


def enabled(provider) -> bool:
    return str(provider).lower() in _opted_in


def due(last_ts: float, now: float | None = None) -> bool:
    """Cadence gate: refresh at most every REFRESH_S seconds."""
    now = time.time() if now is None else now
    return (now - float(last_ts)) >= REFRESH_S


def status(provider, *, idle: bool = False, fetch=None, cred=None, now=None) -> dict:
    """One pill's state. Contract (tested):
         not enabled  -> {"state": "off"}            and fetch/cred are NEVER called
         idle island  -> {"state": "paused"}         and fetch/cred are NEVER called
         no credential-> {"state": "missing-cred"}   and fetch is NEVER called
         otherwise    -> {"state": "ok"|"error", "detail": ...}
       Never raises."""
    try:
        provider = str(provider).lower()
        if provider not in PROVIDERS:
            return {"state": "unknown", "provider": provider}
        if not enabled(provider):
            return {"state": "off", "provider": provider, "reason": "opt-in required"}
        if idle:
            return {"state": "paused", "provider": provider, "reason": "island idle"}
        if cred is None:
            from desktop.overlay import integrations as _self
            cred = _self.win_credential
        if fetch is None:
            from desktop.overlay import integrations as _self
            fetch = _self.http_status
        token = cred(provider)
        if not token:
            return {"state": "missing-cred", "provider": provider,
                    "reason": "no credential in Windows Credential Manager"}
        detail = fetch(provider, token)
        if detail is True:
            return {"state": "ok", "provider": provider}
        if detail is False or detail is None:
            return {"state": "error", "provider": provider, "detail": "unreachable"}
        return {"state": "ok", "provider": provider, "detail": str(detail)[:200]}
    except Exception as exc:                  # a pill never breaks the overlay
        return {"state": "error", "provider": str(provider), "detail": type(exc).__name__}


def win_credential(provider: str):
    """Read the provider token from Windows Credential Manager (TypeGeneric).
    Only ever called for ENABLED providers; returns None when absent. Never raises."""
    try:
        import win32cred
        cred = win32cred.CredRead(f"ishti-integration-{provider}", 1)
        return (cred or {}).get("CredentialBlob", b"").decode("utf-8", "replace") or None
    except Exception:
        return None


def http_status(provider: str, token: str):
    """Read-only status probe. Injected/mocked in tests - the default implementation is
    only reached for opted-in providers with a real credential."""
    import urllib.request
    url = f"https://ishti-status.invalid/{provider}"   # placeholder: providers wire their own
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:   # nosec - owner-opted endpoint
            return resp.status
    except Exception:
        return False


if __name__ == "__main__":
    # self-check: OFF-by-default means NO cred, NO fetch, NO network - ever
    calls = []

    def spy_fetch(*a, **k):
        calls.append(("fetch", a))
        return True

    def spy_cred(*a, **k):
        calls.append(("cred", a))
        return "tok"

    for p in PROVIDERS:
        r = status(p, fetch=spy_fetch, cred=spy_cred)
        assert r["state"] == "off", (p, r)
    assert calls == [], "off-by-default: no credential read, no network, no exceptions"
    assert status("stripe", idle=True, fetch=spy_fetch, cred=spy_cred)["state"] == "off"
    assert not due(0, now=10), "cadence: no refresh inside REFRESH_S"
    assert due(0, now=REFRESH_S + 1), "cadence: refresh after REFRESH_S"
    # opt in -> the pill becomes live; idle pauses it without any call; missing cred never fetches
    assert opt_in("github") is True
    r = status("github", idle=True, fetch=spy_fetch, cred=spy_cred)
    assert r["state"] == "paused" and calls == [], "idle island: no polling"
    assert status("github", fetch=spy_fetch,
                  cred=lambda p: None)["state"] == "missing-cred" and calls == []
    r = status("github", fetch=spy_fetch, cred=spy_cred)
    assert r["state"] == "ok" and calls == [("cred", ("github",)), ("fetch", ("github", "tok"))], (r, calls)
    assert opt_in("github", on=False) is False and not enabled("github")
    assert status("github", fetch=spy_fetch, cred=spy_cred)["state"] == "off"
    assert opt_in("not-a-provider") is False
    assert status("nope")["state"] == "unknown"
    assert opt_in("stripe") is True
    assert status("stripe", fetch=lambda *a: (_ for _ in ()).throw(RuntimeError("net")),
                  cred=lambda p: "t")["state"] == "error", "fetch errors become a pill state"
    opt_in("stripe", on=False)
    print("[self-check] overlay integrations OK - OFF by default (zero cred/network calls), "
          "idle-paused, cadence-gated, missing-cred never fetches")
