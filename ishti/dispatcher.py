"""Dispatcher: request -> route (rules / Laya) -> skill -> reply.
Text and voice both call handle(); voice is just another input channel."""
import io
import os
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


class View(dict):
    """One request's view of a chat session. Its live trace, token stream and route are its own; everything
    else (history, pending question, last entity...) reads and writes through to the shared session. Lets
    several requests of one chat, and the parallel steps of one plan, run at once without mixing streams."""
    OWN = ("trace", "on_token", "route")

    def __init__(self, shared, **own):
        super().__init__({k: own.get(k) for k in self.OWN})
        self.shared = shared

    def __getitem__(self, k):
        return super().__getitem__(k) if k in self.OWN else self.shared[k]

    def __setitem__(self, k, v):
        super().__setitem__(k, v) if k in self.OWN else self.shared.__setitem__(k, v)

    def __contains__(self, k):
        return k in self.OWN or k in self.shared

    def get(self, k, default=None):
        return super().get(k, default) if k in self.OWN else self.shared.get(k, default)

    def setdefault(self, k, default=None):
        return super().setdefault(k, default) if k in self.OWN else self.shared.setdefault(k, default)

    def pop(self, k, *default):
        return super().pop(k, *default) if k in self.OWN else self.shared.pop(k, *default)


INFLIGHT = {}  # session id -> threading.Condition guarding its count of running requests


def _remember(text, reply, skill):
    try:
        from ishti.semantic import get_semantic
        get_semantic().add(f"User: {text}\nISHTI: {reply[:800]}", skill=skill)
    except Exception as exc:
        print(f"[dispatcher] semantic memory write skipped: {exc}")


# Spoken progress for voice requests: the steps an owner would want to hear while waiting, one at a time.
NARRATE = re.compile(r"^(?:Searching the internet|Trying a different search|Found \d+ sources|Reading through|"
                     r"\d+ sources? answer|writing|rendering|cutting a|transcribing|checking the video|open WhatsApp)", re.I)
_narrated = {"at": 0.0}


def narrate(ev):
    label = str(ev.get("label") or ev.get("detail") or "")
    if ev.get("type") != "tool" or not NARRATE.search(label) or time.time() - _narrated["at"] < 3:
        return
    _narrated["at"] = time.time()
    try:
        from ishti.voice import speak
        speak(re.split(r"[:(]", label)[0].strip().rstrip(".") + ".")  # "Searching the internet." not the whole query
    except Exception:
        pass


def _learn(text):
    try:
        from ishti.skills.owner.skill import learn
        if found := learn(text):
            print(f"[dispatcher] learned about the owner: {found}")
    except Exception as exc:
        print(f"[dispatcher] owner learning skipped: {exc}")


URDU = re.compile("[؀-ۿ]")  # Arabic-script letters (Urdu)
FOLLOW_UP = re.compile(
    r"^\s*(?:why\s+(?:did|didn'?t|did\s*not|didnot|don'?t|won'?t)\s+you|(?:no[,.!]?\s+)?i\s+(?:asked|meant|said|wanted)|"
    r"(?:search|look|check|try|do)\s+(?:it\s+|that\s+|this\s+)?(?:now|again|instead|properly|online)|"
    r"(?:and\s+)?(?:now|then|also)\s+(?:do|search|check|try|make|use)|what\s+about|same\s+(?:for|with)|"
    r"(?:not|no)\s+(?:that|this|my)\b)"
    r"|\b(?:do\s+it|redo|again|instead|like\s+(?:that|before)|the\s+(?:first|second|third|last|previous)\s+one|"
    r"you\s+(?:added|moved|put|did|made|said|gave)|you\s+(?:didn'?t|didnot|did\s+not)\s+(?:even\s+)?\w+|"
    r"(?:add|save|put|store|update|write)\s+(?:it\s+|this\s+|that\s+)?(?:in|to|into)\s+(?:the\s+|your\s+|my\s+)?memory)\b", re.I)


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
    shared = SESSIONS.setdefault(sid, {"id": sid, "history": [], "pending": None, "last_entity": None})
    events = []  # kept with the answer so a reopened chat still shows its thinking and graph

    def trace(**ev):
        ev = {"t": round((time.perf_counter() - t0) * 1000), **ev}
        events.append({k: v for k, v in ev.items() if k not in ("sample", "results")})
        if on_event:
            on_event(ev)
        if HOOKS["emit"]:  # every step also goes to the dashboard's live stream (voice/wake requests have no chat tab)
            HOOKS["emit"]("trace", {**{k: v for k, v in ev.items() if k not in ("sample", "results")},
                                    "session": sid, "channel": channel})
        if channel == "voice":
            narrate(ev)
    s = View(shared, trace=trace, on_token=on_token)
    s["trace"](type="received", text=text, channel=channel)

    # Independent requests of one chat run at once; a follow-up, a yes/no or a correction waits for the
    # requests already running, because it is about their answer.
    from ishti.skills import answer_of
    cond = INFLIGHT.setdefault(sid, threading.Condition())
    with cond:
        if shared.get("running") and (FOLLOW_UP.search(text) or CORRECTION.match(text.strip()) or answer_of(text)):
            s["trace"](type="tool", name="wait", label="waiting for your earlier request to finish first", status="start")
            cond.wait_for(lambda: not shared.get("running"), timeout=600)
            s["trace"](type="tool", name="wait", label="earlier request finished", status="done")
        shared["running"] = shared.get("running", 0) + 1
    try:
        return _handle(text, sid, conv, s, events, t0, channel)
    finally:
        with cond:
            shared["running"] -= 1
            cond.notify_all()


# ponytail: word-list detector for Roman Urdu; English that happens to use two of these words ("hai" is rare in
# English) would be translated too. Upgrade path: a Laya language question once it is fine-tuned.
ROMAN_URDU = {"mujhe", "mujhay", "kya", "kia", "hai", "hain", "mera", "meri", "mere", "aap", "ap", "kaise", "kaisay", "karo",
              "kardo", "sunao", "batao", "btao", "nahi", "nahin", "kyun", "kyon", "kahan", "kab", "acha", "achha", "theek",
              "hoga", "hogi", "raha", "rahi", "tha", "thi", "wala", "wali", "kuch", "bhi", "yeh", "ye", "woh", "wo", "abhi",
              "chahiye", "dekho", "bolo", "karna", "karta", "karti", "aur", "lekin", "agar", "tum", "hum", "hamara",
              "mein", "main", "kaisa", "kaisi", "kal", "ka", "ki", "ke", "ko", "se", "ek", "kitna", "kitni", "kaun", "koi"}


def roman_urdu(text):
    words = re.findall(r"[a-z]+", text.lower())
    return len(words) >= 2 and sum(w in ROMAN_URDU for w in words) >= max(2, len(words) // 4)


def translate(text, to):
    from ishti import llm
    # Gemma 3 (config role "translator") writes far better Urdu than Qwen 2.5, which mixes in Chinese characters.
    hint = (" In Urdu, کل (kal) with a future verb (ہوگا، ہوگی، گا) means tomorrow, with a past verb yesterday; "
            "it means 'all' only as کُل before a noun." if to == "English" else "")
    return llm.ask(f"Translate into {to}. Keep names, numbers, file paths, links and markdown exactly.{hint} Output only "
                   f"the translation.\n\n{text}", role="translator", system=None, temperature=0, max_tokens=1200).strip() or text


def _handle(text, sid, conv, s, events, t0, channel):
    # Urdu (script or Roman): skills and routing work in English, so understand it in English and answer back
    # in the owner's own form of Urdu. Gemma does both directions (Qwen 2.5 writes poor Urdu).
    lang = "Urdu" if URDU.search(text) else "Roman Urdu (Urdu written in English letters)" if roman_urdu(text) else None
    if lang:
        s["trace"](type="tool", name="understand", label=f"{lang.split(' (')[0]} message: reading it in English", status="start")
        text = translate(text, "English")
        s["trace"](type="tool", name="understand", label=f"understood as: {text[:120]}", status="done", rewritten=text)

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
        if planner.candidate(text, SKILLS, s) and (steps := planner.plan(text, SKILLS)):
            dec = {"skill": "plan", "confidence": 1.0, "source": "planner", "steps": steps}
        else:
            dec = route(text, SKILLS)
    s["trace"](type="route", skill=dec["skill"], source=dec.get("source"), confidence=dec.get("confidence"),
               why=why(dec), scores=dec.get("scores", []), steps=dec.get("steps", []))
    if dec["skill"] == "plan":
        # each step gets its own view (own route, no token stream): independent steps run in parallel
        name, out = "plan", planner.run(steps, s, lambda t, d: _run(t, View(s.shared, trace=s["trace"]), d)[1])
    else:
        name, out, dec = _run(text, s, dec)
        tried, current = {name}, name
        for _hop in range(2):
            nxt = ESCALATE.get(current)
            if (nxt not in SKILLS or nxt in tried or s.get("pending") or out.get("no_escalate")
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
    if lang and not (lang == "Urdu" and URDU.search(reply)):
        s["trace"](type="tool", name="translate", label=f"answer in {lang.split(' (')[0]}", status="done")
        reply = out["reply"] = translate(reply, lang)
    s["trace"](type="done", skill=name, ms=round((time.perf_counter() - t0) * 1000))
    s["last_request"], s["last_skill"] = text, name
    s["history"] = (s["history"] + [{"role": "user", "content": text},
                                    {"role": "assistant", "content": reply}])[-20:]
    if not out.get("persisted"):  # legacy skill writes its own session history
        conv.add_message(sid, "user", text)
        if is_safe_for_history(reply):
            conv.add_message(sid, "ishti", reply, sources=out.get("sources"), metadata={"events": events[-150:]})
    if name not in ("memory",) and is_safe_for_history(reply):  # exact facts already live in SQLite
        threading.Thread(target=_remember, args=(text, reply, name), daemon=True).start()
    threading.Thread(target=_learn, args=(text,), daemon=True).start()  # the owner's lasting facts and preferences
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
    channel = "voice" if body.get("channel") == "voice" else "text"  # voice = narrated aloud, as the wake word does
    return await asyncio.get_running_loop().run_in_executor(None, handle, text, body.get("session_id"), channel)


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


@api.post("/api/facts")
async def save_fact_endpoint(req: Request):
    try:
        data = await req.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)
    entity = str(data.get("entity", "")).strip()
    attr = str(data.get("attr", "")).strip()
    value = str(data.get("value", "")).strip()
    old_attr = data.get("old_attr")
    if not entity or not attr:
        return JSONResponse({"error": "Entity and attribute are required"}, status_code=400)

    f = get_facts()
    if old_attr and str(old_attr).strip() != attr:
        f.forget(entity, str(old_attr).strip())
    prev = f.set(entity, attr, value)
    return {"status": "ok", "entity": entity, "attr": attr, "value": value, "previous": prev}


MEDIA_EXT = {".mp4": "video/mp4", ".webm": "video/webm", ".mov": "video/quicktime", ".mkv": "video/x-matroska",
             ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp",
             ".mp3": "audio/mpeg", ".wav": "audio/wav", ".m4a": "audio/mp4"}


def media_roots():
    from ishti.skills.folders.skill import known_folders
    return [os.path.join(ROOT, "data")] + [p for k, p in known_folders().items() if k in ("downloads", "videos", "pictures", "desktop", "music")]


@api.get("/api/media")
async def media(path: str, download: int = 0):
    """Play/download a media file shown in the chat. Only media types, only under ISHTI's data folder or the
    owner's media folders, so a crafted path can't read anything else."""
    from fastapi.responses import FileResponse
    real = os.path.realpath(path)
    ext = os.path.splitext(real)[1].lower()
    inside = any(os.path.commonpath([real, os.path.realpath(r)]) == os.path.realpath(r) for r in media_roots()
                 if os.path.splitdrive(real)[0].lower() == os.path.splitdrive(os.path.realpath(r))[0].lower())
    if ext not in MEDIA_EXT or not inside or not os.path.isfile(real):
        return JSONResponse({"error": "not an allowed media file"}, status_code=404)
    return FileResponse(real, media_type=MEDIA_EXT[ext], filename=os.path.basename(real) if download else None)


@api.get("/api/markets/watchlist")
async def markets_watch():
    import asyncio
    from ishti.skills.markets import skill as mk

    def cards():
        import concurrent.futures as cf
        def safe(item):
            try:
                return mk.quote(item)
            except Exception as exc:
                return {**item, "label": item["id"], "error": f"{type(exc).__name__}"}
        with cf.ThreadPoolExecutor(8) as pool:
            return list(pool.map(safe, mk.watchlist()))
    return {"items": await asyncio.get_running_loop().run_in_executor(None, cards)}


@api.post("/api/markets/watchlist")
async def markets_watch_edit(req: Request):
    """{"add": "nvidia"} or {"remove": {"kind", "id"}}"""
    import asyncio
    from ishti.skills.markets import skill as mk
    body = await req.json()
    items = mk.watchlist()
    if body.get("add"):
        item = await asyncio.get_running_loop().run_in_executor(None, mk.resolve, str(body["add"])[:60])
        if not item:
            return JSONResponse({"error": f"couldn't find '{body['add']}'"}, status_code=404)
        if item not in items:
            items = mk.watchlist(items + [item])
    if isinstance(body.get("remove"), dict):
        items = mk.watchlist([i for i in items if i != body["remove"]])
    return await markets_watch()


@api.get("/api/markets/analyze")
async def markets_analyze(q: str):
    """The analyzer page: the same answer the chat gives for 'analyze <q>', plus chart data."""
    import asyncio
    from ishti.skills.markets import skill as mk
    out = await asyncio.get_running_loop().run_in_executor(None, mk.run, f"analyze {q[:80]} stock", {})
    return out or JSONResponse({"error": "nothing found"}, status_code=404)


@api.get("/api/about")
async def about_get():
    """About you: what ISHTI knows about the owner, their custom instructions, and exactly what is sent."""
    from ishti import llm
    from ishti.skills.chat.skill import build_context
    owner = {a: v for a, v in get_facts().get("owner").items() if a != "entity"}
    return {"facts": {a: v for a, v in owner.items() if a != "instructions" and not a.startswith("prefers_")},
            "prefs": {a: v for a, v in owner.items() if a.startswith("prefers_")},
            "instructions": owner.get("instructions", ""),
            "sent": {"style": llm.style(), "context": build_context("")}}


@api.post("/api/about/instructions")
async def about_instructions(req: Request):
    text = str((await req.json()).get("instructions", "")).strip()[:4000]
    if text:
        get_facts().set("owner", "instructions", text)
    else:
        get_facts().forget("owner", "instructions")
    return await about_get()


@api.delete("/api/facts/{name}/{attr}")
async def forget_attr_endpoint(name: str, attr: str):
    return {"deleted": get_facts().forget(name, attr)}


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


@api.get("/api/chrome_profiles")
async def chrome_profiles_get():
    """Which of the owner's Chrome profiles ISHTI may use, and whether each is connected (never the token)."""
    from ishti import CONFIG
    from ishti.mcp_client import load_tokens
    tokens = load_tokens()
    out = []
    for name, cfg in CONFIG.get("mcp_servers", {}).items():
        if cfg.get("token_key"):
            args = cfg.get("args", [])
            profile = next((args[i + 1] for i, a in enumerate(args[:-1]) if a == "--profile-dir-name"), "")
            out.append({"key": cfg["token_key"], "server": name, "profile": profile, "purpose": cfg.get("note", ""),
                        "connected": bool(tokens.get(cfg["token_key"]))})
    return {"profiles": out}


@api.post("/api/chrome_profiles")
async def chrome_profiles_set(req: Request):
    """Save a Playwright Extension token for one profile (local data/mcp_tokens.json, git-ignored)."""
    from ishti import CONFIG
    from ishti.mcp_client import TOKENS_PATH, get_hub, load_tokens, strip_token
    body = await req.json()
    key, token = str(body.get("key", "")), strip_token(str(body.get("token", "")))
    keys = {c["token_key"]: n for n, c in CONFIG.get("mcp_servers", {}).items() if c.get("token_key")}
    if key not in keys or len(token) > 400 or not re.fullmatch(r"[\w\-.=+/:]*", token):
        return JSONResponse({"error": "unknown profile or malformed token"}, status_code=400)
    tokens = {**load_tokens(), key: token}
    if not token:
        tokens.pop(key, None)  # empty = disconnect
    os.makedirs(os.path.dirname(TOKENS_PATH), exist_ok=True)
    with open(TOKENS_PATH, "w", encoding="utf-8") as f:
        json.dump(tokens, f)
    get_hub().close(keys[key])  # reconnect with the new token on next use
    return await chrome_profiles_get()
