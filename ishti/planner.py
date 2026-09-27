"""Multi-skill plans: "check the latest Ollama version and remember it" -> web_search, then memory;
"check the weather and edit the latest video in downloads" -> weather and video, run in parallel.
Only requests that look like several tasks pay for the LLM planning call; the planner may still
answer with one step, in which case normal routing takes over."""
import concurrent.futures as cf
import json
import re

from ishti import llm

GATE = re.compile(r"\b(?:and|then|also|after\s+that|afterwards)\s+(?:then\s+|also\s+)?(?:please\s+)?"
                  r"(?:tell|open|find|search|check|remember|save|note|show|get|launch|start|summari[sz]e|read|"
                  r"write|look|give|set|forget|research|play|compare|translate|edit|organi[sz]e|remind|send|buy|"
                  r"download|install|trim|make|create|what|what's|how|where|when|who)\b", re.I)
# Clause boundaries for multi-task detection: connectors and sentence ends.
CLAUSE = re.compile(r"\s*(?:,?\s*\b(?:and\s+(?:also|then)|and|also|then|after\s+that|plus)\b|[;.!?]+(?:\s+|$))\s*", re.I)
READS = {"browser", "web_search", "web_fetch", "chat", "whatsapp"}  # "open X and tell me what's there" is one browser task
MAX_STEPS = 4
PREV = "{prev}"


def clause_skills(text, skills):
    """Laya (with the deterministic triggers first) routes each clause on its own; [(clause, skill)].
    Laya zero-shot scored 0/4 when asked "is this several tasks?" about the whole message, but it routes
    single-task clauses well, so several clauses landing on different skills is the multi-task signal."""
    from ishti.router import route
    # "Hi, how are you ... can you search X": greetings are not tasks (they became a "1. Hi, how are you" step)
    parts = [p for p in CLAUSE.split(text) if p and len(p.split()) >= 2 and not smalltalk(p)]
    return [(p, route(p, skills)["skill"]) for p in parts[:6]] if len(parts) >= 2 else []


SMALLTALK = re.compile(r"^\W*(?:(?:hi|hello|hey|salam|assalam\w*(?:\s+o\s+alaikum)?|good\s+(?:morning|afternoon|evening)|okay|ok|"
                       r"so|please|thanks?|thank\s+you|how\s+are\s+you(?:\s+doing)?|how\s+can\s+i\s+help\s+you(?:\s+today)?|"
                       r"i\s+am\s+(?:fine|good)|ishti|there|dear|bro)\W*)+$", re.I)


def smalltalk(clause):
    return bool(SMALLTALK.match(clause.strip()))


def candidate(text, skills=None, session=None):
    if len(text.split()) < 6:
        return False
    if not skills:
        return bool(GATE.search(text))
    found = clause_skills(text, skills)
    kinds = {k for _, k in found if k}
    several = len(kinds - {"chat"}) >= 2 or (bool(GATE.search(text)) and len(kinds) >= 2)
    if several and kinds <= READS and skills.get("browser") and skills["browser"].matches(text):
        several = False  # one browser task that also reads the page
    if several and kinds <= {"apps", "whatsapp"}:
        several = False  # opening whatsapp and sending a message is one unified whatsapp task
    if several and "whatsapp" in kinds and kinds <= {"apps", "whatsapp", "chat"}:
        # "open whatsapp and send message to bhai hi how are you in two different messages" is ONE messenger
        # task ("chat" here is the clause router's fallback, not a second task) - splitting it made each step
        # re-open the chat and answer "couldn't find the message box" twice.
        several = False
    if several and session is not None and session.get("trace"):
        session["trace"](type="tool", name="split", label="several tasks: " + ", ".join(f"{k} ({c[:40]})" for c, k in found))
    return several


def plan(text, skills):
    """[{"skill", "request"}, ...] with >= 2 steps, or None (a single skill can do it)."""
    live = {n: s.manifest["route"] for n, s in skills.items() if s.status != "broken"}
    out = llm.ask(
        "Split the owner's request into steps, one skill per step. Skills:\n"
        + "\n".join(f"- {n}: {r}" for n, r in live.items())
        + '\n\nReturn JSON {"steps": [{"skill": "<name>", "request": "<self-contained request for that skill>"}]}. '
        f"Use a single step if one skill can do the whole request; a browser step includes reading and acting on the "
        f"page it opens. At most {MAX_STEPS} steps. Keep the owner's wording. "
        f"Where a step needs the previous step's answer, write {PREV} in its request.\n\nRequest: {text}",
        system=None, temperature=0, max_tokens=300, response_format={"type": "json_object"})
    try:
        steps = json.loads(out).get("steps") or []
    except (ValueError, AttributeError):
        return None
    steps = [s for s in steps if isinstance(s, dict) and s.get("skill") in live and str(s.get("request", "")).strip()
             and not (s["skill"] == "chat" and smalltalk(re.sub(r"[?,.!]", " ", str(s["request"]))))]
    steps = fold(steps)
    return steps[:MAX_STEPS] if len(steps) >= 2 else None


def fold(steps):
    """A chat step right after a browser step is about that page, which chat can't see ("open Classroom in Chrome
    and let me know if there is anything new" became browser + a chat "I don't have access"): it joins the
    browser step."""
    out = []
    for step in steps:
        if out and step["skill"] == "chat" and out[-1]["skill"] == "browser":
            ask = step["request"].replace(PREV, "what you found there")
            out[-1] = {**out[-1], "request": f"{out[-1]['request'].rstrip(' .')}, then {ask[0].lower()}{ask[1:]}"}
        else:
            out.append(step)
    return out


def run(steps, session, run_step):
    """run_step(text, decision) -> skill output. Steps that don't use {prev} are independent and run in
    parallel; a chain runs in order and stops if a step waits for the owner (a pending confirmation), so
    nothing after an unconfirmed action runs."""
    dec = lambda i, step: {"skill": step["skill"], "confidence": 1.0, "source": "plan", "step": i}

    def safe(text, d):
        """A failing step must not take the others down (in parallel the first exception lost every result)."""
        try:
            return run_step(text, d)
        except Exception as exc:
            if type(exc).__name__ == "Cancelled":
                raise
            return {"reply": f"The {d['skill']} skill failed: {type(exc).__name__}: {str(exc)[:120]}", "failed": True}

    failed = lambda o: o.get("failed") or o["reply"].startswith(("The ", "I can't do that yet")) and " failed:" in o["reply"][:80]

    def report(outs, total):
        """Multi-step recovery: say exactly what completed and what didn't - no silent partial state."""
        bad = [i for i, o in enumerate(outs, 1) if failed(o)]
        if not bad and len(outs) == total:
            return ""
        done = [str(i) for i in range(1, len(outs) + 1) if i not in bad]
        skipped = list(range(len(outs) + 1, total + 1))
        return ("\n\n**Status:** " + (f"done {', '.join(done)}" if done else "nothing completed")
                + (f"; failed {', '.join(map(str, bad))}" if bad else "")
                + (f"; not started {', '.join(map(str, skipped))}" if skipped else "") + ".")

    if not any(PREV in s["request"] for s in steps):
        # ponytail: one thread per step; model calls still queue on the single GPU, but web, files, APIs and
        # the browser overlap. Browser steps serialize on the browser skill's own lock.
        with cf.ThreadPoolExecutor(len(steps)) as pool:
            outs = list(pool.map(lambda p: safe(p[1]["request"], dec(*p)), enumerate(steps, 1)))
        replies = [f"**{i}. {s['request'][:70]}**\n{o['reply']}" for i, (s, o) in enumerate(zip(steps, outs), 1)]
        return {"reply": "\n\n".join(replies) + report(outs, len(steps)),
                "sources": [x for o in outs for x in o.get("sources", [])], "mode": "PLAN", "steps": steps}
    replies, sources, prev, outs = [], [], "", []
    for i, step in enumerate(steps, 1):
        text = step["request"].replace(PREV, prev[:400])
        out = safe(text, dec(i, step))
        outs.append(out)
        replies.append(out["reply"])
        sources += out.get("sources", [])
        prev = out["reply"]
        if failed(out) and i < len(steps):  # later steps depend on this one's answer
            break
        if session.get("pending"):
            if i < len(steps):
                replies.append(f"(Waiting for your answer before the remaining {len(steps) - i} step(s).)")
            return {"reply": "\n\n".join(r for r in replies if r), "sources": sources, "mode": "PLAN", "steps": steps}
    return {"reply": "\n\n".join(r for r in replies if r) + report(outs, len(steps)), "sources": sources,
            "mode": "PLAN", "steps": steps}


if __name__ == "__main__":
    classroom = [{"skill": "markets", "request": "Nvidia price"},
                 {"skill": "browser", "request": "Open Chrome and go to Google Classroom"},
                 {"skill": "chat", "request": f"Let me know if there are new assignments in {PREV}"}]
    assert fold(classroom) == [classroom[0], {"skill": "browser", "request": "Open Chrome and go to Google Classroom, then "
                                              "let me know if there are new assignments in what you found there"}]
    assert fold(classroom[:1] + classroom[2:]) == classroom[:1] + classroom[2:]  # chat after other skills stays
    assert candidate("check the latest version of ollama and remember it")
    assert candidate("find the file ishti_logo and then open task manager")
    assert not candidate("tell me the pros and cons of rust")
    assert not candidate("what is the difference between TCP and UDP")
    calls = []
    fake = lambda text, dec: calls.append((dec["skill"], text)) or {"reply": f"R{len(calls)}", "sources": []}
    out = run([{"skill": "web_search", "request": "latest ollama version"},
               {"skill": "memory", "request": f"remember ollama latest is {PREV}"}], {}, fake)
    assert calls[1] == ("memory", "remember ollama latest is R1") and out["reply"] == "R1\n\nR2", (calls, out)
    s = {"pending": None}
    stop = lambda text, dec: (s.update(pending={"skill": "browser"}) or {"reply": "Go ahead?", "sources": []})
    assert "Waiting" in run([{"skill": "browser", "request": "a"}, {"skill": "chat", "request": f"b {PREV}"}], s, stop)["reply"]
    import threading
    import time
    seen = []
    slow = lambda text, dec: (seen.append(threading.get_ident()), time.sleep(0.3)) and {} or {"reply": text.upper(), "sources": []}
    t = time.time()
    out = run([{"skill": "weather", "request": "weather today"}, {"skill": "video", "request": "list videos"}], {}, slow)
    assert time.time() - t < 0.5 and len(set(seen)) == 2, "independent steps must run in parallel"
    assert out["reply"].index("WEATHER TODAY") < out["reply"].index("LIST VIDEOS")
    assert [p for p in CLAUSE.split("check the weather today and also edit the video. then remind me at 5") if p] == [
        "check the weather today", "edit the video", "remind me at 5"]
    boom = lambda text, dec: (_ for _ in ()).throw(ValueError("disk full")) if dec["step"] == 2 else {"reply": "ok", "sources": []}
    par = run([{"skill": "weather", "request": "a"}, {"skill": "video", "request": "b"}, {"skill": "system", "request": "c"}], {}, boom)
    assert "failed: ValueError" in par["reply"] and "done 1, 3; failed 2" in par["reply"], par["reply"]
    seq = run([{"skill": "web_search", "request": "x"}, {"skill": "video", "request": "y"},
               {"skill": "memory", "request": f"z {PREV}"}], {}, boom)
    assert "done 1; failed 2; not started 3" in seq["reply"], seq["reply"]
    print("[self-check] planner OK - gate, {prev} chaining, stops at a pending confirmation")
