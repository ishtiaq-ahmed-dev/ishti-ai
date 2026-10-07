"""HTTP side of the remote channels (docs/API_CONTRACT.md §11). attach(app) wires:
  routes    /api/remote/status, /pair/start, /pair/cancel, /revoke, /policy (GET/PUT),
            /telegram/token (POST/DELETE), /enable, /test, /pin (POST/DELETE)
  startup   the service follows the policy switches at boot (start() never raises)
  shutdown  graceful stop before the server dies (no orphan pollers - tested)
Auth: same rule as the whole server - local unproxied callers are trusted, everything else needs
the bearer token. The policy/token endpoints are the ONLY writers of remote_policy.yaml and the
DPAPI store, and both check `authorized(conn)` strictly.

OD-12/23 hardening: every state-changing route additionally passes `_origin_ok()` first -
a browser request from a cross-site context (Sec-Fetch-Site: cross-site/same-site, or an
Origin that does not match Host) is refused 403 regardless of token; token sessions are
hashed + in-memory (ishti/api_sessions.py), so a restart invalidates them.
"""
from __future__ import annotations

import asyncio
import re

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter()
TOKEN_RE = re.compile(r"^\d{6,12}:[A-Za-z0-9_-]{30,}$")


def _origin_ok(request: Request) -> bool:
    """Browser CSRF posture: a cross-site context is refused even with a valid token."""
    from ishti import api_sessions
    return api_sessions.origin_ok(request.headers.get("origin"),
                                  request.headers.get("host"),
                                  request.headers.get("sec-fetch-site"))


def _auth(request: Request) -> bool:
    from interface.web_ui import server  # import late: server.py imports this module
    return server.authorized(request)


def _deny():
    return JSONResponse({"error": "unauthorized"}, status_code=401)


def _forbidden():
    return JSONResponse({"error": "cross-site request refused"}, status_code=403)


@router.get("/api/remote/tunnel")
async def tunnel_status(request: Request):
    if not _auth(request):
        return _deny()
    from ishti import tunnel
    return tunnel.status()


@router.post("/api/remote/tunnel/start")
async def tunnel_start(request: Request):
    if not _origin_ok(request):
        return _forbidden()
    if not _auth(request):
        return _deny()
    from ishti.web_security import direct_loopback
    if not direct_loopback(request):
        return JSONResponse({"error": "Tunnel start is available only from the local desktop"}, status_code=403)
    try:
        body = await request.json()
    except Exception:
        body = {}
    try:
        port = int(body.get("port", 8888))
        from ishti import tunnel
        result = await asyncio.to_thread(tunnel.start, port)
        from ishti.channels import policy
        policy.save({"remote_access_enabled": True})
        return result
    except Exception as exc:
        return JSONResponse({"error": str(exc)[:240]}, status_code=409)


@router.post("/api/remote/tunnel/stop")
async def tunnel_stop(request: Request):
    if not _origin_ok(request):
        return _forbidden()
    if not _auth(request):
        return _deny()
    from ishti import tunnel
    result = await asyncio.to_thread(tunnel.stop)
    from ishti.channels import policy
    policy.save({"remote_access_enabled": False})
    return result


@router.get("/api/remote/status")
async def remote_status(request: Request):
    if not _auth(request):
        return _deny()
    from ishti import audit
    from ishti.channels import service
    h = service.get().health()
    try:
        rows = audit.query(limit=30)
        recent = [{"ts": r[0], "initiator": r[1], "skill": r[2], "action": r[3], "detail": r[4],
                   "channel": r[8]} for r in rows if (r[8] or "").startswith(("telegram", "whatsapp"))][:12]
    except Exception:
        recent = []
    return {**h, "recent": recent}


@router.post("/api/remote/pair/start")
async def pair_start(request: Request):
    if not _origin_ok(request):
        return _forbidden()
    if not _auth(request):
        return _deny()
    from ishti.channels import service
    return service.get().pair_start()


@router.post("/api/remote/pair/cancel")
async def pair_cancel(request: Request):
    if not _origin_ok(request):
        return _forbidden()
    if not _auth(request):
        return _deny()
    from ishti.channels import service
    return {"cancelled": service.get().pair_cancel()}


@router.post("/api/remote/revoke")
async def revoke(request: Request):
    if not _origin_ok(request):
        return _forbidden()
    if not _auth(request):
        return _deny()
    from ishti.channels import service
    return service.get().revoke_sessions()


@router.get("/api/remote/policy")
async def get_policy(request: Request):
    if not _auth(request):
        return _deny()
    from ishti.channels import policy
    return policy.load(force=True)


@router.put("/api/remote/policy")
async def put_policy(request: Request):
    """The ONLY remote-policy writer, and it is local-only (any non-local caller needs the token;
    the channel gate has no path here at all)."""
    if not _origin_ok(request):
        return _forbidden()
    if not _auth(request):
        return _deny()
    from ishti.channels import policy, service
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "body must be JSON"}, status_code=400)
    saved = policy.save(body)
    if body.get("telegram_enabled") is not None or body.get("whatsapp_enabled") is not None:
        service.get().start()  # follow the switch immediately
    return saved


@router.post("/api/remote/telegram/token")
async def set_token(request: Request):
    """Store the @BotFather token in Windows Credential Manager (DPAPI). Never logged."""
    if not _origin_ok(request):
        return _forbidden()
    if not _auth(request):
        return _deny()
    from ishti.channels import security
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "body must be JSON"}, status_code=400)
    token = str(body.get("token") or "").strip()
    if not TOKEN_RE.match(token):
        return JSONResponse({"error": "not a Telegram bot token (expected digits:hash from @BotFather)"}, status_code=400)
    security.store_token(token)
    from ishti.channels import service
    service.get().start()  # if the policy already enables Telegram, come up now
    return {"stored": True}


@router.delete("/api/remote/telegram/token")
async def del_token(request: Request):
    if not _origin_ok(request):
        return _forbidden()
    if not _auth(request):
        return _deny()
    from ishti.channels import policy, security, service
    security.forget_token()
    policy.save({"telegram_enabled": False})
    service.get().start()
    return {"deleted": True, "telegram_enabled": False}


@router.post("/api/remote/pin")
async def set_pin(request: Request):
    if not _origin_ok(request):
        return _forbidden()
    if not _auth(request):
        return _deny()
    from ishti.channels import security
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "body must be JSON"}, status_code=400)
    pin = str(body.get("pin") or "")
    if not (4 <= len(pin) <= 12):
        return JSONResponse({"error": "PIN must be 4-12 characters"}, status_code=400)
    security.set_pin(pin)
    return {"set": True}


@router.delete("/api/remote/pin")
async def del_pin(request: Request):
    if not _origin_ok(request):
        return _forbidden()
    if not _auth(request):
        return _deny()
    from ishti.channels import security
    from ishti.channels import policy
    state = security._read_state()
    state.pop("pin_hash", None)
    state.pop("pin_salt", None)
    security._write_state(state)
    security._pin_cache.update(hash=None, checked=0.0)
    policy.save({"local_pin_required": False})
    return {"deleted": True}


@router.post("/api/remote/enable")
async def enable_channel(request: Request):
    """Flip a channel switch (Telegram needs a stored token + pairing; WhatsApp needs its risk ack)."""
    if not _origin_ok(request):
        return _forbidden()
    if not _auth(request):
        return _deny()
    from ishti.channels import policy, security, service
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "body must be JSON"}, status_code=400)
    channel = body.get("channel")
    on = bool(body.get("enabled"))
    if channel == "telegram":
        if on and not security.has_token():
            return JSONResponse({"error": "store the bot token first (Settings -> Remote Control)"},
                                status_code=400)
        upd = {"telegram_enabled": on}
    elif channel == "whatsapp":
        upd = {"whatsapp_enabled": on}
        if on:
            upd["risk_ack"] = {**(policy.load().get("risk_ack") or {}), "whatsapp_automation": True}
    else:
        return JSONResponse({"error": "channel must be 'telegram' or 'whatsapp'"}, status_code=400)
    policy.save(upd)
    service.get().start()
    return {"channel": channel, "enabled": on}


@router.post("/api/remote/test")
async def test_message(request: Request):
    """Send 'ISHTI remote link works' to the paired Telegram chat (no real send to anyone else)."""
    if not _origin_ok(request):
        return _forbidden()
    if not _auth(request):
        return _deny()
    from ishti.channels import service
    ok = service.get().push("test", "ISHTI remote link works", urgent=True)
    return {"sent": bool(ok)}


def attach(app):
    """server.py calls this once (right after lifecycle_api.attach)."""
    app.include_router(router)

    async def _boot():
        from ishti.channels import service
        service.get().start()  # follows the policy; never raises
        from ishti.channels import policy
        if policy.load().get("remote_access_enabled"):
            try:
                from ishti import lifecycle, tunnel
                await asyncio.to_thread(tunnel.start, int(lifecycle._st.get("port") or 8888))
            except Exception:
                pass

    def _down():
        from ishti.channels import service
        try:
            service.get().stop()
        except Exception:
            pass
        try:
            from ishti import tunnel
            tunnel.stop()
        except Exception:
            pass

    app.on_event("startup")(_boot)
    app.on_event("shutdown")(_down)


if __name__ == "__main__":
    # offline self-check over a fresh FastAPI app (no real server): auth gate + policy round-trip
    import tempfile
    import os
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import ishti.channels.policy as P
    from ishti.channels import security as S

    tmp = tempfile.mkdtemp()
    P.PATH = os.path.join(tmp, "p.yaml")
    P.reset_cache()
    S.STATE_PATH = os.path.join(tmp, "s.json")

    app = FastAPI()
    attach(app)
    c = TestClient(app)
    # TestClient counts as a REMOTE caller in this repo's auth model (check.py: check_remote_auth),
    # so the API self-check presents the bearer token exactly like the tunnel/desktop-remote path.
    from interface.web_ui import server as _srv
    H = {"Authorization": f"Bearer {_srv.API_TOKEN}"}
    st = c.get("/api/remote/status", headers=H)
    assert st.status_code == 200, st.text
    assert c.get("/api/remote/status").status_code == 401  # no token: refused
    body = st.json()
    assert "telegram" in body and "whatsapp" in body and body["telegram"]["running"] is False
    # token validation (remote caller -> needs the header, like every other call here)
    bad = c.post("/api/remote/telegram/token", json={"token": "not a token"}, headers=H)
    assert bad.status_code == 400, (bad.status_code, bad.text)
    # policy round-trip through the API
    p = c.put("/api/remote/policy", json={"stale_command_max_age_s": 90}, headers=H)
    assert p.json()["stale_command_max_age_s"] == 90
    assert c.get("/api/remote/policy", headers=H).json()["stale_command_max_age_s"] == 90
    # enable telegram without a token -> refused with guidance
    r = c.post("/api/remote/enable", json={"channel": "telegram", "enabled": True}, headers=H)
    assert r.status_code == 400 and "token" in r.json()["error"]
    # whatsapp enable flips the risk ack with it
    r = c.post("/api/remote/enable", json={"channel": "whatsapp", "enabled": True}, headers=H)
    assert r.status_code == 200
    pol = c.get("/api/remote/policy", headers=H).json()
    assert pol["whatsapp_enabled"] is True and pol["risk_ack"]["whatsapp_automation"] is True
    # pairing round-trip through the API
    code = c.post("/api/remote/pair/start", headers=H).json()["code"]
    assert S.try_pair(code, 777) and c.get("/api/remote/status", headers=H).json()["telegram"]["paired_ids"] == [777]
    assert c.post("/api/remote/revoke", headers=H).json()["revoked_ids"] == 1
    # pin
    assert c.post("/api/remote/pin", json={"pin": "123456"}, headers=H).json()["set"] is True
    assert S.check_pin("123456") and c.delete("/api/remote/pin", headers=H).json()["deleted"] is True
    print("[self-check] channels.api OK - status, token guard, policy API, enable flow, pairing, pin")
