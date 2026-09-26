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
# Replies that hand the problem back to the owner. When an information-seeking skill gives one, the
# dispatcher tries the next capable skill once instead of stopping (memory/folders/reminders asking for a
# path or a time are legitimate questions, so they never escalate).
FELL_SHORT = re.compile(r"\b(?:couldn'?t|could not|can'?t|cannot|unable to|not able to|didn'?t (?:find|open|get|reach)|"
                        r"no (?:\w+ )?(?:results?|matching|information)|not found|doesn'?t (?:provide|show|contain|have)|"
                        r"(?:please )?visit\s+(?:https?://|www\.|the\s+\w+\s+(?:site|website|page))|"
                        r"i don'?t (?:know|have (?:access|information|real-time))|"
                        r"(?:isn'?t|is not|wasn'?t|are not|aren'?t|not) (?:listed|mentioned|specified|stated|available|provided|given)"
                        r"(?: in| on| by)?)", re.I)
# Next skill to try when one falls short (followed up to twice): browser -> web_search -> deep_research.
ESCALATE = {"browser": "web_search", "chat": "web_search", "web_fetch": "browser", "web_search": "deep_research"}
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


FOLLOW_UP = re.compile(
    r"^\s*(?:why\s+(?:did|didn'?t|did\s*not|didnot|don'?t|won'?t)\s+you|(?:no[,.!]?\s+)?i\s+(?:asked|meant|said|wanted)|"
    r"(?:search|look|check|try|do)\s+(?:it\s+|that\s+|this\s+)?(?:now|again|instead|properly|online)|"
    r"(?:and\s+)?(?:now|then|also)\s+(?:do|search|check|try|make|use)|what\s+about|same\s+(?:for|with)|"
    r"(?:not|no)\s+(?:that|this|my)\b)"
    r"|\b(?:do\s+it|redo|again|instead|like\s+(?:that|before)|the\s+(?:first|second|third|last|previous)\s+one|"
    r"you\s+(?:added|moved|put|did|made|said|gave))\b", re.I)


def contextualize(text, s) -> str:
    """Follow-ups ("search now", "why didn't you search?", "do it using the names instead") only make
    sense with the conversation. Rewrite them into one standalone request before routing, so the right
    skill gets the full intent (the standard condense-question-with-history step)."""
    if not s["history"] or not FOLLOW_UP.search(text):
        return text
    from ishti import llm
    turns = "\n".join(f"{'Owner' if m['role'] == 'user' else 'ISHTI'}: {m['content'][:400]}" for m in s["history"][-6:])
    s["trace"](type="tool", name="understand", label="read the conversation to understand the follow-up", status="start")
    out = llm.ask(
        f"Conversation so far:\n{turns}\n\nOwner's new message: {text}\n\n"
        "Rewrite the owner's new message as ONE complete, standalone request that an assistant could act on "
        "without the conversation. If the owner is complaining that the last answer was wrong or did the wrong "
        "thing, rewrite it as their original request done the right way (for example 'search the internet for "
        "...'). Keep their wording, file names and paths exactly. Output only the rewritten request.",
        system=None, temperature=0, max_tokens=120).strip().strip('"')
    rewritten = out if 3 <= len(out.split()) <= 80 else text
    s["trace"](type="tool", name="understand", label=f"understood as: {rewritten[:120]}", status="done", rewritten=rewritten)
    return rewritten


def fell_short(question, reply, skill, s) -> bool:
    """Did the answer hand the problem back? Phrase check first; for information skills also ask the
    local model whether the reply states what was actually asked (a number, name, date...) - one word,
    ~0.4 s on the already-loaded planner model."""
    if FELL_SHORT.search(reply):
        return True
    if skill not in ("web_search", "browser", "web_fetch"):
        return False
    from ishti import llm
    s["trace"](type="tool", name="verify", label="check the answer really answers the question", status="start")
    # Wording picked on a labelled set (7/8; stricter phrasings rejected correct 2026 answers because the
    # model's own knowledge ends earlier - hence today's date and "live web sources").
    verdict = llm.ask(f"Today is {time.strftime('%A %d %B %Y')}. The answer comes from live web sources.\n"
                      f"Question: {question}\nAnswer: {reply[:1200]}\n\nDoes the answer actually give the information "
                      "the question asks for (a concrete value, name, number or fact), even partly? Answer no only if "
                      "it gives none of it, says it could not find it, or only points to where to look. Reply yes or no.",
                      system=None, temperature=0, max_tokens=3).lower()
    ok = verdict.startswith("yes")
    s["trace"](type="tool", name="verify", status="done" if ok else "error",
               label="the answer is specific" if ok else "the answer misses what was asked")
    return not ok


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
    if src == "escalation":
        return f"{dec.get('was')} couldn't finish it, so {skill} takes over."
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
    elif pending and not (FOLLOW_UP.search(text) and len(text.split()) > 3):
        dec = {"skill": pending["skill"], "confidence": 1.0, "source": "pending"}
    else:
        if pending:  # a real follow-up, not an answer to the skill's question: drop the question
            s["pending"] = None
        text = contextualize(text, s)
        if (planner.candidate(text) and not (SKILLS.get("browser") and SKILLS["browser"].matches(text))
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
        tried, current = {name}, name
        for _hop in range(2):
            nxt = ESCALATE.get(current)
            if (nxt not in SKILLS or nxt in tried or s.get("pending")
                    or dec.get("source") in ("pending", "correction") or not fell_short(text, out["reply"], current, s)):
                break
            # Don't hand the owner a "couldn't / visit this site yourself": try the next capable skill.
            s["trace"](type="escalate", frm=current, to=nxt, why=f"{current} fell short, so I'm trying {nxt}.")
            n2, out2, _ = _run(text, s, {"skill": nxt, "confidence": 1.0, "source": "escalation", "was": current})
            tried.add(nxt)
            current = nxt
            # only a better answer replaces the first one; a fallback that also misses is discarded
            if out2 and not fell_short(text, out2["reply"], nxt, s):
                name, out = n2, {**out2, "sources": out.get("sources", []) + out2.get("sources", [])}
                break

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
    """Load Laya now so the first real request doesn't pay ~4 s. Whisper is NOT preloaded: it would sit
    on ~1.6 GB of VRAM next to the 14B model and push it into slow shared memory; it loads on first use."""
    route("hello", SKILLS)
    try:
        import torch
        torch.cuda.empty_cache()
    except Exception:
        pass


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


@api.get("/api/overview")
async def overview_endpoint():
    """Everything the Overview page shows: pipeline stage counts, skills, the owner's data."""
    from ishti import CONFIG
    from ishti import router as r
    f = get_facts()
    with f.lock:
        by_source = dict(f.db.execute("SELECT source, COUNT(*) FROM route_log GROUP BY source").fetchall())
        by_skill = dict(f.db.execute("SELECT skill, COUNT(*) FROM route_log GROUP BY skill").fetchall())
        total = f.db.execute("SELECT COUNT(*) FROM route_log").fetchone()[0]
        facts = f.db.execute("SELECT entity, attr, value FROM facts ORDER BY key, attr").fetchall()
    try:
        from ishti.skills.reminders.skill import _db, _pending
        reminders = len(_pending(_db()))
    except Exception:
        reminders = 0
    try:
        from ishti.semantic import get_semantic
        memories = get_semantic().col.count()
    except Exception:
        memories = 0
    from orchestrator.memory.user_profile import get_user_memory
    prefs = get_user_memory().get_preferences()
    return {
        "requests": total, "by_source": by_source, "by_skill": by_skill,
        "laya": {"loaded": r._laya is not None, "error": r.laya_error, "model": CONFIG["router"]["laya_model"]},
        "models": CONFIG["llm"]["models"], "voice": {k: CONFIG["voice"][k] for k in ("whisper_model", "piper_voice", "wake_model")},
        "skills": [{**s.info(), "uses": by_skill.get(n, 0)} for n, s in SKILLS.items()],
        "owner": [{"entity": e, "attr": a, "value": v} for e, a, v in facts if e == "owner"],
        "facts": [{"entity": e, "attr": a, "value": v} for e, a, v in facts if e != "owner"],
        "reminders": reminders, "memories": memories,
        "prefs": {"audio_output": prefs.audio_output, "response_length": prefs.response_length},
    }
