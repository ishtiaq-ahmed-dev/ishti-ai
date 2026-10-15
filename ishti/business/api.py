"""Local owner business API; explicit request validation even when mounted separately."""
import json
import os
import sqlite3
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field

from ishti.web_security import direct_loopback, request_origin_ok
from . import engine
from .store import Store


def owner(request: Request):
    if os.environ.get("ISHTI_BUSINESS_DESK", "1") == "0":
        raise HTTPException(503, "Business Desk is disabled on this installation.")
    if not direct_loopback(request) or not request_origin_ok(request):
        raise HTTPException(403, "Business data and observation settings require the local owner session.")


router = APIRouter(prefix="/api/business", dependencies=[Depends(owner)])


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Profile(Strict):
    name: str = Field(max_length=120)
    currency: str = Field(min_length=3, max_length=3)
    timezone: str = Field(max_length=80)
    goal: str = Field(default="", max_length=1000)
    enabled: bool = False
    interval_minutes: int = Field(default=60, ge=5, le=1440)
    lead_days: int = Field(default=3, ge=1, le=90)
    stale_days: int = Field(default=7, ge=1, le=90)
    revenue_drop_pct: int = Field(default=20, ge=5, le=90)


class Source(Strict):
    kind: str = Field(max_length=20)
    label: str = Field(max_length=120)
    content: str | None = Field(default=None, max_length=1024*1024)
    path: str | None = Field(default=None, max_length=1000)


class Decision(Strict):
    choice: str
    source_hash: str = Field(min_length=64, max_length=64)
    note: str = Field(default="", max_length=1000)


class Outcome(Strict):
    result: str
    amount: str = Field(default="0", max_length=30)
    receipt: str = Field(default="", max_length=300)
    note: str = Field(default="", max_length=1000)


class Consultation(Strict):
    question: str = Field(min_length=1, max_length=1500)


def invoke(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None
    except (OSError, RuntimeError, sqlite3.Error):
        raise HTTPException(503, "Business storage or source is unavailable; no plaintext fallback or external action occurred.") from None


@router.get("/dashboard")
def dashboard():
    return JSONResponse(invoke(engine.dashboard), headers={"Cache-Control": "no-store"})


@router.post("/profile")
def profile(body: Profile):
    invoke(engine.configure, body.model_dump())
    return invoke(engine.dashboard)


@router.post("/sources")
def source(body: Source):
    return invoke(engine.import_source, **body.model_dump())


@router.delete("/sources/{source_id}")
def remove_source(source_id: str):
    invoke(engine.remove_source, source_id)
    return {"removed": True}


@router.post("/observe")
def observe():
    result = invoke(engine.cycle, force=True, runner="owner")
    return {"cycle": result, "dashboard": invoke(engine.dashboard)}


@router.post("/decisions/{decision_id}")
def decide(decision_id: str, body: Decision):
    return invoke(engine.decide, decision_id, body.choice, body.source_hash, body.note)


@router.post("/decisions/{decision_id}/outcome")
def outcome(decision_id: str, body: Outcome):
    return invoke(engine.outcome, decision_id, **body.model_dump())


@router.post("/consult")
def consult(body: Consultation):
    return invoke(engine.consult, body.question)


@router.get("/export")
def export():
    # Explicit owner export includes sensitive data; not cached, never auto-uploaded.
    payload = json.dumps(invoke(Store().read), ensure_ascii=False, indent=2)
    return Response(payload, media_type="application/json", headers={"Cache-Control": "no-store",
                    "Content-Disposition": 'attachment; filename="ishti-business-export.json"'})
