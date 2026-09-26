"""Dispatcher: request -> route (rules / Laya) -> skill -> reply.
Text and voice both call handle(); voice is just another input channel."""
import json
import threading
import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ishti import ROOT
from ishti.facts import get_facts
from ishti import router as router_mod
from ishti.router import route
from ishti.skills import load_skills
from orchestrator.memory.conversation import get_conversation_manager
from orchestrator.memory.error_logger import ErrorLogger
from orchestrator.response_guard import is_safe_for_history

SKILLS = load_skills()
SESSIONS = {}
HOOKS = {"confirm": None, "emit": None, "engine": None}  # filled by the web server for UI-bound skills
api = APIRouter()


def seed_owner():
    """One-time copy of the owner's profile into the fact store."""
    f = get_facts()
    if f.get("owner"):
        return
    try:
        from orchestrator.memory.user_profile import get_user_memory
        p = get_user_memory().get_profile()
        for attr, val in (("name", p.name), ("role", p.role), ("city", p.location.get("city")),
                          ("country", p.location.get("country"))):
            if val:
                f.set("owner", attr, val)
    except Exception as exc:
        print(f"[dispatcher] owner seed skipped: {exc}")


def _remember(text, reply, skill):
    try:
        from ishti.semantic import get_semantic
        get_semantic().add(f"User: {text}\nISHTI: {reply[:800]}", skill=skill)
    except Exception as exc:
        print(f"[dispatcher] semantic memory write skipped: {exc}")


def handle(text: str, session_id: str = None, channel: str = "text") -> dict:
    t0 = time.perf_counter()
    conv = get_conversation_manager()
    sid = session_id or conv.active_session_id or conv.new_chat("New Session")
    s = SESSIONS.setdefault(sid, {"id": sid, "history": [], "pending": None, "last_entity": None})

    pending = s.get("pending")
    dec = ({"skill": pending["skill"], "confidence": 1.0, "source": "pending"} if pending
           else route(text, SKILLS))
    tried, out = set(), None
    while dec["skill"]:
        name = dec["skill"]
        s["route"] = dec
        try:
            out = SKILLS[name].run(text, s)
        except Exception as exc:
            ErrorLogger.log_error(subsystem=f"SKILL_{name.upper()}", error=exc, user_prompt=text,
                                  context="dispatcher.handle")
            out = {"reply": f"The {name} skill failed: {type(exc).__name__}: {str(exc)[:160]}"}
        if out is not None:
            break
        tried.add(name)  # skill declined ("not mine") -> next best choice
        dec = route(text, SKILLS, exclude=tried)
    if out is None:
        name, out = None, {"reply": "I couldn't handle that - no skill accepted it."}

    reply = out["reply"]
    get_facts().log_route(text, name, dec.get("confidence", 0), dec.get("source", ""), json.dumps(dec))
    s["history"] = (s["history"] + [{"role": "user", "content": text},
                                    {"role": "assistant", "content": reply}])[-20:]
    if not out.get("persisted"):  # legacy skill writes its own session history
        conv.add_message(sid, "user", text)
        if is_safe_for_history(reply):
            conv.add_message(sid, "ishti", reply, sources=out.get("sources"))
    if name not in ("memory",) and is_safe_for_history(reply):  # exact facts already live in SQLite
        threading.Thread(target=_remember, args=(text, reply, name), daemon=True).start()
    return {**{k: v for k, v in out.items() if k not in ("reply", "persisted")},
            "request": text, "final_answer": reply, "mode": out.get("mode") or (name or "none").upper(), "skill": name,
            "sources": out.get("sources", []), "session_id": sid, "channel": channel, "route": dec,
            "latency_ms": round((time.perf_counter() - t0) * 1000)}


@api.post("/api/chat")
async def chat_endpoint(req: Request):
    import asyncio
    body = await req.json()
    text = (body.get("text") or body.get("prompt") or "").strip()
    if not text:
        return JSONResponse({"error": "empty text"}, status_code=400)
    return await asyncio.get_running_loop().run_in_executor(None, handle, text, body.get("session_id"))


@api.get("/api/skills")
async def skills_endpoint():
    return {"skills": [s.info() for s in SKILLS.values()], "laya_error": router_mod.laya_error}


@api.get("/api/facts")
async def facts_endpoint():
    f = get_facts()
    with f.lock:
        rows = f.db.execute("SELECT entity, attr, value, previous, updated_at FROM facts ORDER BY key").fetchall()
    return {"facts": [dict(zip(("entity", "attr", "value", "previous", "updated_at"), r)) for r in rows]}


@api.delete("/api/facts/{name}")
async def forget_endpoint(name: str):
    return {"deleted": get_facts().forget(name)}


def warm_up():
    """Load the Laya checkpoint now so the first real request doesn't pay ~4 s."""
    route("hello", SKILLS)


seed_owner()
threading.Thread(target=warm_up, daemon=True).start()
