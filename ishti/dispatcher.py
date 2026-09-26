"""Dispatcher: request -> route (rules / Laya) -> skill -> reply.
Text and voice both call handle(); voice is just another input channel."""
import io
import json
import re
import threading
import time

from fastapi import APIRouter, Form, Request, UploadFile
from fastapi.responses import JSONResponse

from ishti import ROOT
from ishti.facts import get_facts
from ishti import planner, router as router_mod
from ishti.router import route
from ishti.skills import load_skills
from orchestrator.memory.conversation import get_conversation_manager
from orchestrator.memory.error_logger import ErrorLogger
from orchestrator.response_guard import is_safe_for_history

SKILLS = load_skills()
SESSIONS = {}
# "that should have been web search" / "use the browser for that" -> re-run the last request with
# that skill and log it as a labelled example for fine-tuning Laya (NEEDS.md #1).
CORRECTION = re.compile(
    r"^(?:no[,.!]?\s+)?(?:(?:that|this|it)\s+(?:should\s+(?:have\s+)?(?:been|gone\s+to|used)|was\s+(?:meant\s+)?for)"
    r"|use)\s+(?:the\s+)?(?P<skill>[a-z_ ]+?)(?:\s+skill)?(?:\s+for\s+(?:that|this|it))?[.!]?$", re.I)
ALIASES = {"search": "web_search", "web": "web_search", "internet": "web_search", "web search": "web_search",
           "pc": "legacy", "computer": "legacy", "files": "legacy", "browser": "browser", "llm": "chat"}
HOOKS = {"emit": None}  # the web server sets emit(event, data) so skills can push HUD events
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


def why(dec) -> str:
    """One human sentence on why this skill was chosen (shown in the Thinking panel)."""
    src, skill = dec.get("source"), dec.get("skill")
    if src == "rule":
        return f"The request matches the {skill} skill's pattern."
    if src == "laya":
        runner = [n for n, _ in dec.get("scores", [])[1:2]]
        return (f"Laya (typed-decision model, {dec.get('ms', 0):.0f} ms) picked {skill} at {dec['confidence']:.0%}"
                + (f", ahead of {runner[0]}." if runner else "."))
    if src == "pending":
        return f"This answers the question {skill} asked you."
    if src == "correction":
        return f"You corrected the route: re-running your last request with {skill}."
    if src == "planner":
        return f"Several tasks in one request, so I planned {len(dec.get('steps', []))} steps."
    if src == "plan":
        return f"Plan step {dec.get('step')}: {skill}."
    return "No skill claimed it with confidence, so the local model answers directly."


def _run(text, s, dec):
    """Run the decided skill; if it declines ("not mine"), fall through to the next best one."""
    tried, out, name = set(), None, None
    while dec["skill"]:
        name = dec["skill"]
        s["route"] = dec
        s["trace"](type="step", skill=name, status="start", why=why(dec), text=text)
        try:
            out = SKILLS[name].run(text, s)
        except Exception as exc:
            ErrorLogger.log_error(subsystem=f"SKILL_{name.upper()}", error=exc, user_prompt=text,
                                  context="dispatcher.handle")
            out = {"reply": f"The {name} skill failed: {type(exc).__name__}: {str(exc)[:160]}"}
        if out is not None:
            s["trace"](type="step", skill=name, status="done", summary=out["reply"][:160])
            break
        s["trace"](type="step", skill=name, status="skip", summary=f"{name} passed it on")
        tried.add(name)
        dec = route(text, SKILLS, exclude=tried)
    if out is None:
        name, out = None, {"reply": "I couldn't handle that - no skill accepted it."}
    get_facts().log_route(text, name, dec.get("confidence", 0), dec.get("source", ""), json.dumps(dec))
    return name, out, dec


def handle(text: str, session_id: str = None, channel: str = "text", on_token=None, on_event=None) -> dict:
    """on_token(str): live stream of the reply for skills that generate it token by token.
    on_event(dict): live trace (route, plan, steps, tool calls) for the UI's Thinking panel and graph.
    Skills add their own tool events with session["trace"](type="tool", ...)."""
    t0 = time.perf_counter()
    conv = get_conversation_manager()
    sid = session_id or conv.active_session_id or conv.new_chat("New Session")
    s = SESSIONS.setdefault(sid, {"id": sid, "history": [], "pending": None, "last_entity": None})
    s["on_token"] = on_token
    s["trace"] = lambda **ev: on_event and on_event({"t": round((time.perf_counter() - t0) * 1000), **ev})
    s["trace"](type="received", text=text, channel=channel)

    pending, fix = s.get("pending"), CORRECTION.match(text.strip())
    wanted = fix and ALIASES.get(fix["skill"].lower(), fix["skill"].lower().replace(" ", "_"))
    if fix and wanted in SKILLS and s.get("last_request"):
        text = s["last_request"]
        dec = {"skill": wanted, "confidence": 1.0, "source": "correction", "was": s.get("last_skill")}
    elif pending:
        dec = {"skill": pending["skill"], "confidence": 1.0, "source": "pending"}
    elif (planner.candidate(text) and not (SKILLS.get("browser") and SKILLS["browser"].matches(text))
          and (steps := planner.plan(text, SKILLS))):  # a browser task already covers "…and tell me what's there"
        dec = {"skill": "plan", "confidence": 1.0, "source": "planner", "steps": steps}
    else:
        dec = route(text, SKILLS)
    s["trace"](type="route", skill=dec["skill"], source=dec.get("source"), confidence=dec.get("confidence"),
               why=why(dec), scores=dec.get("scores", []), steps=dec.get("steps", []))
    if dec["skill"] == "plan":
        name, out = "plan", planner.run(steps, s, lambda t, d: _run(t, s, d)[1])
    else:
        name, out, dec = _run(text, s, dec)

    reply = out["reply"]
    s["trace"](type="done", skill=name, ms=round((time.perf_counter() - t0) * 1000))
    s["last_request"], s["last_skill"] = text, name
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


@api.get("/api/route_log")
async def route_log_endpoint(limit: int = 500):
    """Routing decisions for fine-tuning Laya: corrections are gold labels, rules are high-precision."""
    f = get_facts()
    with f.lock:
        rows = f.db.execute("SELECT ts, text, skill, confidence, source FROM route_log ORDER BY ts DESC LIMIT ?",
                            (limit,)).fetchall()
    return {"rows": [dict(zip(("ts", "text", "skill", "confidence", "source"), r)) for r in rows]}


@api.delete("/api/facts/{name}")
async def forget_endpoint(name: str):
    return {"deleted": get_facts().forget(name)}


def warm_up():
    """Load Laya and whisper now so the first real request doesn't pay their load time."""
    route("hello", SKILLS)
    try:
        from ishti.voice import stt_model
        stt_model()
    except Exception as exc:
        print(f"[dispatcher] whisper preload skipped: {exc}")


seed_owner()
threading.Thread(target=warm_up, daemon=True).start()


@api.post("/api/stt")
async def stt_endpoint(audio: UploadFile):
    """Browser mic -> local faster-whisper -> text (the HUD then submits it like typed text)."""
    import asyncio
    from ishti.voice import transcribe
    data = io.BytesIO(await audio.read())
    return {"text": await asyncio.get_running_loop().run_in_executor(None, transcribe, data)}


@api.post("/api/voice")
async def voice_endpoint(audio: UploadFile, session_id: str = Form(None)):
    """Full voice round trip for any client: audio in -> dispatcher -> reply text + Piper WAV (base64)."""
    import asyncio
    import base64
    from ishti.voice import synthesize, transcribe
    loop = asyncio.get_running_loop()
    text = await loop.run_in_executor(None, transcribe, io.BytesIO(await audio.read()))
    if not text:
        return JSONResponse({"error": "no speech heard"}, status_code=422)
    res = await loop.run_in_executor(None, handle, text, session_id, "voice")
    wav = await loop.run_in_executor(None, synthesize, res["final_answer"])
    return {**res, "transcript": text, "audio_wav_b64": base64.b64encode(wav).decode()}


# ---- data for the app's sections (Tasks, Settings, System, activity heatmap) ----
@api.get("/api/reminders")
async def reminders_endpoint():
    from ishti.skills.reminders.skill import _db, _pending
    return {"reminders": [{"id": i, "due": d, "text": t} for i, d, t in _pending(_db())]}


@api.post("/api/reminders/{rid}/done")
async def reminder_done_endpoint(rid: int):
    from ishti.skills.reminders.skill import _db
    f = _db()
    with f.lock, f.db:
        n = f.db.execute("UPDATE reminders SET done=1 WHERE id=?", (rid,)).rowcount
    return {"updated": n}


@api.get("/api/prefs")
async def prefs_get():
    from orchestrator.memory.user_profile import get_user_memory
    p = get_user_memory().get_preferences()
    from ishti import CONFIG
    return {"audio_output": p.audio_output, "wake_enabled": CONFIG["voice"]["wake_enabled"],
            "wake_model": CONFIG["voice"]["wake_model"], "models": CONFIG["llm"]["models"]}


@api.post("/api/prefs")
async def prefs_set(req: Request):
    from orchestrator.memory.user_profile import get_user_memory
    body = await req.json()
    if "audio_output" in body:
        get_user_memory().update_preferences(audio_output=bool(body["audio_output"]))
    return await prefs_get()


@api.get("/api/activity")
async def activity_endpoint(days: int = 365):
    f = get_facts()
    since = time.time() - days * 86400
    with f.lock:
        per_day = f.db.execute("SELECT date(ts, 'unixepoch', 'localtime') d, COUNT(*) FROM route_log WHERE ts>=? "
                               "GROUP BY d ORDER BY d", (since,)).fetchall()
        per_skill = f.db.execute("SELECT skill, COUNT(*) c FROM route_log WHERE ts>=? GROUP BY skill ORDER BY c DESC",
                                 (since,)).fetchall()
    return {"days": [{"date": d, "count": c} for d, c in per_day],
            "skills": [{"skill": s or "none", "count": c} for s, c in per_skill]}


@api.get("/api/system")
async def system_endpoint():
    import asyncio
    from desktop.computer_use import get_computer_use_agent
    return await asyncio.get_running_loop().run_in_executor(None, get_computer_use_agent().get_system_telemetry)
