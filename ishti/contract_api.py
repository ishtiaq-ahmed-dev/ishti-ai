"""Contract v2 HTTP surface (docs/API_CONTRACT.md §14). server.py: contract_api.attach(app).

Thin layer over ishti/runs.py (the state owner), ishti/profiles.py, ishti/prompt_compiler.py,
ishti/agents.py and ishti/connectors.py. No business logic here beyond shaping and validation.
"""
from __future__ import annotations

import os
import sys

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ishti import runs

router = APIRouter()

_bad = lambda msg, code=400: JSONResponse({"error": msg}, status_code=code)


async def _body(req: Request) -> dict:
    try:
        data = await req.json()
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


# ---------------------------------------------------------------- chats & messages (14.1)

@router.post("/api/chats")
async def chats_create(req: Request):
    body = await _body(req)
    chat = runs.new_chat(profile=body.get("profile") or None)
    if body.get("profile"):
        runs.publish("profile_changed", {"chat_id": chat["chat_id"], "profile": chat["profile"]})
    return chat


@router.get("/api/chats")
async def chats_list(limit: int = 200):
    return {"chats": runs.list_chats(limit=limit)}


@router.get("/api/chats/{chat_id}/messages")
async def chat_messages(chat_id: str, after_seq: int = 0):
    if not runs.get_chat(chat_id):
        return _bad("unknown chat_id", 404)
    return {"chat_id": chat_id, "messages": runs.messages_after(chat_id, after_seq)}


@router.post("/api/chats/{chat_id}/messages")
async def chat_send(chat_id: str, req: Request):
    body = await _body(req)
    text = str(body.get("text") or "").strip()
    if not text:
        return _bad("text is required")
    try:
        res = runs.add_user_message(chat_id, text, client_msg_id=body.get("client_msg_id"),
                                    attachments=body.get("attachments"),
                                    channel=str(body.get("channel") or "text"))
    except ValueError as exc:
        return _bad(str(exc), 404 if "unknown chat" in str(exc) else 400)
    msg = res["message"]
    if res["duplicate"]:
        run = runs.get_run(msg.get("run_id") or "") if msg.get("run_id") else None
        return {"message_id": msg["message_id"], "run_id": msg.get("run_id") or "",
                "duplicate": True, "state": (run or {}).get("state", "unknown")}
    run = runs.start_run(chat_id, msg["message_id"], profile=msg.get("profile"))
    from ishti.run_executor import launch
    launch(run["run_id"])  # server-side thread; independent of any UI connection (WP1)
    return {"message_id": msg["message_id"], "run_id": run["run_id"], "duplicate": False}


# ---------------------------------------------------------------- runs (14.2)

@router.get("/api/runs")
async def runs_list(chat_id: str | None = None, limit: int = 100):
    return {"runs": runs.list_runs(chat_id=chat_id, limit=limit)}


@router.get("/api/runs/{run_id}")
async def run_get(run_id: str):
    run = runs.get_run(run_id)
    if not run:
        return _bad("unknown run_id", 404)
    return run


@router.post("/api/runs/{run_id}/cancel")
async def run_cancel(run_id: str):
    out = runs.cancel_run(run_id)
    if not out.get("cancelled") and "state" not in out:
        return _bad("unknown run_id", 404)
    return {"run_id": run_id, **out}


# ---------------------------------------------------------------- events (14.3) + feedback (14.4)

@router.get("/api/events")
async def events_since(since_seq: int = 0, limit: int = 2000):
    """HTTP replay twin of /ws/events?since_seq=N (same log)."""
    return {"events": runs.events_since(since_seq, limit), "last_seq": runs.last_seq()}


@router.post("/api/feedback")
async def feedback_post(req: Request):
    body = await _body(req)
    try:
        out = runs.add_feedback(body.get("message_id"), body.get("run_id"),
                                int(body.get("rating", 0)),
                                reasons=body.get("reasons") or [],
                                comment=str(body.get("comment") or ""),
                                correction=str(body.get("correction") or ""))
    except (ValueError, TypeError) as exc:
        return _bad(str(exc))
    return out


@router.get("/api/feedback/stats")
async def feedback_stats():
    return runs.feedback_stats()


# ---------------------------------------------------------------- profiles (14.4)

@router.get("/api/profile")
async def profile_get():
    from ishti import profiles
    return {"active": profiles.load_active()}


@router.put("/api/profile")
async def profile_put(req: Request):
    from ishti import profiles
    body = await _body(req)
    if body.get("active") not in profiles.PROFILES:
        return _bad("active must be 'personal' or 'business'")
    active = profiles.set_active(body["active"])
    runs.publish("profile_changed", {"active": active})
    return {"active": active}


# ---------------------------------------------------------------- connectors (14.4)

def _connector_rows() -> list[dict]:
    """Unified registry view (WP8 start): the OAuth/token catalog PLUS the channel connectors
    (whatsapp, telegram) and the registry families that come later (mcp, a2a, api)."""
    rows: list[dict] = []
    try:
        from ishti import connectors as C
        for c in C.catalog():
            rows.append({"id": c.get("service"), "kind": c.get("kind", "oauth"),
                         "status": c.get("state", "disconnected"),
                         "configured": bool(c.get("has_credentials") or c.get("connected")),
                         "detail": c.get("account", "")})
    except Exception as exc:  # catalog must never take the API down
        rows.append({"id": "catalog_error", "kind": "api", "status": "error",
                     "configured": False, "detail": str(exc)[:200]})
    rows.append({"id": "whatsapp", "kind": "channel", "status": "disconnected",
                 "configured": False, "detail": "QR link flow (WP8)"})
    rows.append({"id": "telegram", "kind": "channel", "status": "disconnected",
                 "configured": False, "detail": "bot token + pairing (WP8)"})
    return rows


@router.get("/api/connectors")
async def connectors_list():
    return {"connectors": _connector_rows()}


@router.get("/api/connectors/{cid}/status")
async def connector_status(cid: str):
    row = next((r for r in _connector_rows() if r["id"] == cid), None)
    if row is None:
        return _bad("unknown connector", 404)
    from ishti.channels import policy
    pol = policy.load()
    detail = {"whatsapp": {"paired": bool(pol.get("whatsapp_enabled")),
                           "detail": "risk ack + QR link flow"},
              "telegram": {"paired": bool(pol.get("telegram_enabled")),
                           "detail": "bot token + pairing"}}.get(cid, {})
    return {"id": cid, "status": row["status"], "configured": row["configured"],
            "detail": row["detail"], **detail}


@router.post("/api/connectors/{cid}/connect")
async def connector_connect(cid: str):
    if cid not in ("whatsapp", "telegram"):
        return _bad("connect is available for whatsapp|telegram only")
    from ishti.channels import service
    svc = service.get()
    if cid == "whatsapp":
        return {"ok": True, "detail": "QR flow: use /api/inputs/wa-start then GET /api/inputs/wa-qr"}
    ok = svc.start()
    return {"ok": bool(ok), "detail": "telegram poller start requested"}


@router.post("/api/connectors/{cid}/disconnect")
async def connector_disconnect(cid: str):
    if cid not in ("whatsapp", "telegram"):
        return _bad("disconnect is available for whatsapp|telegram only")
    from ishti.channels import service
    svc = service.get()
    try:
        svc.stop()
        return {"ok": True, "detail": f"{cid} stopped"}
    except Exception as exc:
        return {"ok": False, "detail": str(exc)[:200]}


@router.post("/api/connectors/{cid}/test")
async def connector_test(cid: str):
    if cid != "telegram":
        return _bad("test send is telegram-only (self-chat test for whatsapp is owner-authorized)")
    from ishti.channels import service
    ok = service.get().push("test", "ISHTI connector test", urgent=True)
    return {"ok": bool(ok), "detail": "sent to the paired chat" if ok else "not connected"}


@router.put("/api/connectors/{cid}/config")
async def connector_config(cid: str, req: Request):
    if cid not in ("whatsapp", "telegram"):
        return _bad("config is available for whatsapp|telegram only")
    body = await _body(req)
    from ishti.channels import policy
    upd = {}
    if isinstance(body.get("control_numbers"), list):
        upd["allowed_telegram_ids"] = [str(n) for n in body["control_numbers"]]
    if isinstance(body.get("policy"), dict):
        upd.update({k: v for k, v in body["policy"].items() if k in policy.DEFAULTS})
    if upd:
        policy.save(upd)
    return {"saved": True, "connector": cid}


# ---------------------------------------------------------------- agents (14.4)

@router.get("/api/agents")
async def agents_list():
    from ishti import agents
    out = []
    for aid, spec in agents.AGENTS.items():
        st = runs.AGENT_STATE.get(aid, {})
        out.append({"id": aid, "skills": spec.get("skills", []), "goal": spec.get("goal", ""),
                    "state": st.get("state", "idle"), "detail": st.get("detail", "")})
    return {"agents": out}


@router.get("/api/agents/{aid}/messages")
async def agent_messages(aid: str):
    from ishti import agents
    if aid not in agents.AGENTS:
        return _bad("unknown agent", 404)
    return {"agent_id": aid, "messages": runs.AGENT_MESSAGES.get(aid, [])}


# ---------------------------------------------------------------- learning (14.4)

@router.get("/api/learning/status")
async def learning_status():
    lessons = proposals = 0
    dataset_rows = 0
    try:
        from ishti.learning import store
        if store.enabled():
            row = store.row("SELECT COUNT(*) AS n FROM lessons")
            lessons = int(row["n"]) if row else 0
            row = store.row("SELECT COUNT(*) AS n FROM dataset")
            dataset_rows = int(row["n"]) if row else 0
    except Exception as exc:
        from ishti.core.logutil import swallowed
        swallowed("ishti/contract_api.py:learning_status", exc)
    proposals = len(runs.list_proposals("open"))
    return {"mode": runs.learning_mode(), "lessons": lessons, "proposals_open": proposals,
            "dataset_rows": dataset_rows}


@router.get("/api/skills/proposals")
async def proposals_list(status: str | None = None):
    return {"proposals": runs.list_proposals(status)}


@router.post("/api/skills/proposals/{pid}/{action}")
async def proposal_action(pid: str, action: str):
    if action not in ("approve", "reject"):
        return _bad("action must be approve|reject")
    try:
        p = runs.resolve_proposal(pid, approve=(action == "approve"))
    except ValueError as exc:
        return _bad(str(exc), 404)
    return {"id": pid, "status": p["status"]}


@router.put("/api/learning/mode")
async def learning_mode_put(req: Request):
    body = await _body(req)
    try:
        return {"mode": runs.set_learning_mode(str(body.get("mode") or ""))}
    except ValueError as exc:
        return _bad(str(exc))


# ---------------------------------------------------------------- prompt layers (14.4, read-only)

@router.get("/api/prompt/layers")
async def prompt_layers():
    from ishti import profiles, prompt_compiler
    return prompt_compiler.layers(profiles.load_active())


def attach(app):
    app.include_router(router)
    # recovery is idempotent and cheap; runs it at every boot (WP1 restart recovery)
    app.on_event("startup")(_recover)


def _recover() -> None:
    try:
        runs.recover_interrupted()
    except Exception as exc:
        print(f"[contract_api] recovery skipped: {exc}")


if __name__ == "__main__":
    import tempfile
    import os
    os.environ.setdefault("ISHTI_TEST_MODE", "1")
    runs.set_db_path(os.path.join(tempfile.mkdtemp(), "runs.db"))
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    app = FastAPI()
    attach(app)
    c = TestClient(app)
    ch = c.post("/api/chats", json={}).json()
    assert ch["profile"] == "personal" and ch["chat_id"]
    assert c.get("/api/chats").json()["chats"][0]["chat_id"] == ch["chat_id"]
    assert c.get("/api/events?since_seq=0").json()["events"][0]["type"] == "profile_changed"
    # idempotent send: same client_msg_id -> same message/run, marked duplicate
    body = {"client_msg_id": "m-1", "text": "list my downloads folder"}
    r1 = c.post(f"/api/chats/{ch['chat_id']}/messages", json=body).json()
    r2 = c.post(f"/api/chats/{ch['chat_id']}/messages", json=body).json()
    assert r1["message_id"] and r1["run_id"] and not r1.get("duplicate")
    assert r2.get("duplicate") is True and r2["message_id"] == r1["message_id"]
    assert r2["run_id"] == r1["run_id"]
    run = c.get(f"/api/runs/{r1['run_id']}").json()
    assert run["state"] in ("queued", "running", "done", "failed", "cancelled")
    assert c.get("/api/runs/doesnotexist").status_code == 404
    msgs = c.get(f"/api/chats/{ch['chat_id']}/messages").json()["messages"]
    assert msgs[0]["role"] == "user" and msgs[0]["seq"] > 0
    ev = c.get("/api/events?since_seq=0").json()
    assert ev["events"] and {"message_created", "run_started"} <= {e["type"] for e in ev["events"]}
    assert ev["last_seq"] >= len(ev["events"])
    fb = c.post("/api/feedback", json={"message_id": "m", "run_id": run["run_id"], "rating": 1,
                                       "reasons": ["good"], "comment": "", "correction": ""}).json()
    assert fb["feedback_id"] and fb["state"] in ("stored", "duplicate")
    assert c.get("/api/feedback/stats").json()["total"] >= 1
    prof = c.put("/api/profile", json={"active": "business"}).json()
    assert prof["active"] == "business"
    assert c.put("/api/profile", json={"active": "chaos"}).status_code == 400
    assert c.get("/api/profile").json()["active"] == "business"
    assert c.put("/api/profile", json={"active": "personal"}).json()["active"] == "personal"
    conns = c.get("/api/connectors").json()["connectors"]
    assert isinstance(conns, list) and conns
    assert c.get("/api/connectors/whatsapp/status").json()["id"] == "whatsapp"
    assert c.get("/api/connectors/nope/status").status_code == 404
    assert c.put("/api/connectors/telegram/config",
                 json={"control_numbers": ["+923001234567"]}).json()["saved"] is True
    agents = c.get("/api/agents").json()["agents"]
    assert any(a["id"] == "researcher" for a in agents)
    assert c.get("/api/agents/researcher/messages").json()["agent_id"] == "researcher"
    assert c.get("/api/agents/ghost/messages").status_code == 404
    ls = c.get("/api/learning/status").json()
    assert ls["mode"] in ("off", "propose", "auto_t0")
    pr = c.post("/api/skills/proposals", json={}).status_code  # 405: no bare POST
    assert pr == 405
    p = runs.add_proposal("demo", skill="demo_skill")
    assert c.post(f"/api/skills/proposals/{p['id']}/approve").json()["status"] == "approved"
    assert c.post("/api/skills/proposals/x/approve").status_code == 404
    assert c.put("/api/learning/mode", json={"mode": "propose"}).json()["mode"] == "propose"
    assert c.put("/api/learning/mode", json={"mode": "total"}).status_code == 400
    lay = c.get("/api/prompt/layers").json()
    assert lay["layers"][0]["name"] == "core" and lay["total_chars"] > 0
    print("[self-check] contract_api OK - chats/messages/runs/feedback/profile/connectors/"
          "agents/learning/prompt-layers all reachable")
