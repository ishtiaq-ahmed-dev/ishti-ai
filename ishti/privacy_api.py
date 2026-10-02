"""GET/POST /api/privacy: the outbound ledger, the Internet master switch and what was hardened (ishti/privacy.py).
attach(app) is called once from interface/web_ui/server.py; it also installs the egress guard, so any entry point that
builds the server app is covered."""
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ishti import audit, privacy

router = APIRouter()


@router.get("/api/privacy")
async def get_privacy():
    return privacy.snapshot()


@router.post("/api/privacy")
async def set_privacy(req: Request):
    """{internet: true|false}. OFF blocks every external connection, even inside web skills."""
    try:
        body = await req.json()
        on = privacy.set_internet(body.get("internet") if isinstance(body, dict) else None)
    except ValueError as exc:  # not JSON, or not a boolean
        return JSONResponse({"error": str(exc)}, status_code=400)
    audit.record(f"internet switch -> {'ON' if on else 'OFF'}", initiator="owner", skill="privacy")
    return privacy.snapshot()


def attach(app):
    privacy.install()
    app.include_router(router)
