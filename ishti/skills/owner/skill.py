"""Owner commands: audio on/off, who am I, what do you know about me, what can you do, run diagnostics.
Also: personal facts the owner states ("I study at IMSciences") are saved, questions about the owner
("where do I study?") are answered from those facts, and questions about ISHTI itself ("why are you slow?")
are answered from its own skills and limits. None of these ever go to the web."""
import difflib
import json
import os
import re
import time

from ishti.facts import get_facts

I = re.I
AUDIO_OFF = re.compile(r"^(?:mute|stop (?:reading|talking)|be quiet|(?:turn )?(?:audio|voice|sound) off|turn off (?:the )?(?:audio|voice|sound))[.!]?$", I)
VOICE_SET = re.compile(r"^(?:please\s+)?(?:(?:switch|change|set)\s+(?:your\s+|the\s+)?voice\b|(?:switch|change)\s+(?:your\s+voice\s+)?to\s+.*\bvoice\b|"
                       r"(?:use|speak\s+(?:in|with))\s+(?:the\s+|a\s+|your\s+)?.*\bvoice\b|(?:switch|change)\s+to\s+(?:male|female)\b)", I)
VOICE_LIST = re.compile(r"\b(?:what|which|list|show)\b.*\bvoices\b|\bwhat\s+voices?\s+(?:do|can)\b", I)
AUDIO_ON = re.compile(r"^(?:unmute|read (?:that|it)(?: aloud| out loud)?|say it|speak up|(?:turn )?(?:audio|voice|sound) on|turn on (?:the )?(?:audio|voice|sound))[.!]?$", I)
WHO = re.compile(r"^(?:who am i|do you know who i am|who is your owner)\W*$", I)
KNOW = re.compile(r"^(?:what do you (?:know|remember) about me|show (?:me )?my (?:profile|memory|facts)|what have you saved)\W*$", I)
CAN = re.compile(r"^(?:what can you do|what are your (?:skills|capabilities)|show (?:your )?(?:skills|capabilities)|help)\W*$", I)
DOCTOR = re.compile(r"^(?:run (?:the )?diagnostics|(?:ishti )?doctor|system check|health check)\W*$", I)
ATTRS = (r"university|uni|college|school|major|degree|job|company|employer|profession|birthday|age|email|phone|"
         r"semester|department|field|full\s+name")
TELL = re.compile(r"^(?:(?:and|also|btw|by\s+the\s+way|fyi)[,\s]+)?(?:"
                  r"i\s+(?:study|am\s+studying|work|am\s+working|teach|was\s+born|graduated|did\s+my)\b|"
                  r"i(?:'m|\s+am)\s+(?:a|an|in|doing|pursuing|from)\b|"
                  rf"my\s+(?:{ATTRS})\s+(?:is|are)\b|"
                  r"(?:please\s+)?(?:remember|note|save|add|store|update|keep)\b.*?\b(?:memory|i|i'm|my|me)\b"
                  r")[^?]*$", I)
SAVE_MEMORY = re.compile(
    r"\b(?:add|save|store|update|put|write|keep|remember)\s+(?:this|that|it|everything)?\s*(?:in|to|into)?\s*(?:the\s+|your\s+|my\s+)?memory\b"
    r"|\b(?:remember|note\s+down|make\s+a\s+note)\s+(?:that\s+)?\b", I)
ABOUT_ME = re.compile(rf"\bmy\s+(?:{ATTRS}|name|city|country)\b", I)
VERBS = ("study", "work", "live", "born", "teach", "graduated")
SELF = re.compile(r"\bwhy\s+(?:are|is|do|did|does|can'?t|cant|won'?t|don'?t|dont|odnt|didn'?t|didnot)\s+(?:you|your|ishti)\b|"
                  r"\bfix\s+yourself\b|\b(?:can|do|could)\s+you\s+(?:do|perform|handle|run)\s+(?:multiple|parallel|many|several)\b|"
                  r"\byou\s+(?:do\s+not|don'?t|dont|can'?t|cant|never|didn'?t|didnot)\s+(?:perform|do|have|remember|understand|"
                  r"work|answer|reply|respond|support)\b|\byour\s+(?:responses?|answers?|speed|skills?|limits?|limitations?)\b", I)
FEEDBACK = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "data", "feedback.md")
ISHTI_FACTS = (
    "ISHTI runs fully on the owner's PC: one 14B model on one GPU (Ollama). Simple answers take 1-5 s, web searches "
    "5-25 s, browser tasks 10-40 s because every page step needs the model. Messages sent while it is busy wait in a "
    "queue and run in order. A request with several parts ('do X and also Y') is split into steps that run one after "
    "another; truly parallel work is limited because the one GPU runs one model call at a time. Complaints and feature "
    "requests are written to data/feedback.md and fixed in the next build.")


def asks_about_me(t):
    """A question about the owner themselves: "where do I study?", "where i stid ?" (typo), "what's my major"."""
    if re.search(r"\bwhat\s+(?:do|does)\s+(?:i|my\s+\w+)\s+(?:like|love|prefer|hate|dislike)\b", t, re.I):
        return False  # likes live in the relationship graph (the relationships skill answers them)
    words = re.findall(r"[a-z']+", t.lower())
    if not words or not (t.rstrip().endswith("?") or words[0] in ("where", "what", "which", "when", "whats", "what's")):
        return False
    # a request mentioning a file path / branch / repo is about that thing, not about the owner
    # ("which branch am I on in D:/proj" is gitops'; "on" fuzzy-matching "born" must not win it)
    if re.search(r"[A-Za-z]:[\\/]|\bbranch\b|\brepo\b|\bcommit\b|\bgit\b", t, re.I):
        return False
    if ABOUT_ME.search(t) or ("i" in words and re.search(rf"\b(?:{ATTRS})\b", t, I)):  # "what semester am i in?"
        # "what should I get X for his birthday" is about X, not about the owner:
        # a third person's name/possessive in the question means it's a relationship question
        if re.search(r"\b(?:get|give|buy|find)\b.*\bfor\s+(?:him|her|them|his|my\s+\w+|[A-Z][a-z]+)\b", t):
            return False
        return True
    # ponytail: short questions only, "i <verb>" fuzzy-matched to a few personal verbs; "how do i live stream" is the known miss
    after_i = [words[i + 1] for i, w in enumerate(words[:-1]) if w == "i"]
    return len(words) <= 8 and any(difflib.get_close_matches(v, VERBS, n=1, cutoff=0.6) for v in after_i)


def match(text):
    t = text.strip()
    return (any(rx.match(t) for rx in (AUDIO_OFF, AUDIO_ON, WHO, KNOW, CAN, DOCTOR, VOICE_SET)) or bool(VOICE_LIST.search(t))
            or bool((TELL.match(t) or SAVE_MEMORY.search(t)) and not SCHEDULING.search(t))
            or bool(SELF.search(t)) or asks_about_me(t))


# "add a meeting to my calendar", "add a reminder": scheduling, not a fact about the owner
SCHEDULING = re.compile(r"\b(?:calendar|meeting|appointment|event|reminder|alarm|timer|schedule|invoice|expense)\b", re.I)


def tell(t, session):
    from ishti import llm
    from ishti.skills import trace
    context_to_extract = t
    # If the user says "add in memory" or "save it in memory" without explicit facts,
    # pull what they just said from session history!
    history = session.get("history", []) if isinstance(session, dict) else []
    if len(t.split()) <= 6 and SAVE_MEMORY.search(t) and history:
        for m in reversed(history):
            if m.get("role") == "user" and m.get("content") != t:
                context_to_extract = f"{m['content']}\n(Requested to save: {t})"
                break
    raw = llm.ask(f"The owner told their assistant this about themselves:\n{context_to_extract}\n\nExtract every fact about the owner as "
                  "one JSON object of short snake_case attribute -> short value. Current status counts too (semester, "
                  "year, job, current project, tools they use) - it is updated when it changes. Examples: "
                  '"i am in 5th semester ok" -> {"semester": "5th"}; "i am in th semester" -> {"semester": "7th"}; '
                  '"i work at Systems Ltd as a data analyst" -> {"job": "data analyst", "company": "Systems Ltd"}; '
                  '"remember that i use vs code daily" -> {"editor": "VS Code"}. Fix obvious typos and write names with proper capitals. '
                  "Output {} only if it says nothing about the owner (e.g. a mood like 'I'm tired').", system=None, temperature=0,
                  max_tokens=100, response_format={"type": "json_object"})
    try:
        found = json.loads(re.search(r"\{.*\}", raw, re.S)[0])
    except (TypeError, ValueError):
        found = {}
    found = {re.sub(r"\W+", "_", str(k)).strip("_").lower(): str(v).strip()
             for k, v in (found.items() if isinstance(found, dict) else []) if str(v).strip()}
    if not found:
        if SAVE_MEMORY.search(t):
            clean_val = re.sub(r"^(?:please\s+)?(?:remember|add|save|store|update)\s+(?:in|to|into)?\s*(?:the\s+|your\s+|my\s+)?memory[:\s]*", "", t, flags=re.I).strip()
            if clean_val:
                get_facts().set("owner", "note", clean_val)
                trace(session, "memory", f"saved note: {clean_val}")
                return {"reply": f"Updated your memory: note: {clean_val}.", "sources": []}
        from ishti.skills.chat import skill as chat
        return chat.run(t, session)
    lines = []
    for k, v in found.items():
        old = get_facts().set("owner", k, v)  # upsert: returns the previous value
        lines.append(f"{k.replace('_', ' ')}: {old} -> {v}" if old and old != v else f"{k.replace('_', ' ')}: {v}")
    trace(session, "memory", "saved: " + ", ".join(lines))
    return {"reply": "Updated your memory: " + "; ".join(lines) + ".", "sources": []}


# Only messages that can carry a lasting fact: a standing preference, or the owner describing themselves.
LEARNABLE = re.compile(r"\b(?:prefer|always|never|not\s+needed|no\s+need|don'?t\s+(?:want|need|like)|should(?:n'?t)?|"
                       r"i\s+(?:like|love|hate|want\s+(?:it|you|ishti|them|the)|usually|mostly|am|'m|work|study|use|build|"
                       r"make|develop|run|teach|manage|have\s+been)|i'm)\b", I)
LEARN_EXAMPLES = (
    'how many pdfs are in my downloads -> {}\n'
    'tell me a joke -> {}\n'
    'check my whatsapp and tell me unread chats -> {}\n'
    'i work at a bank as a data analyst -> {"job": "data analyst", "company_type": "bank"}\n'
    'answer me in simple english always -> {"prefers_language": "simple English"}\n'
    'the suggestion buttons are not needed -> {"prefers_suggestion_buttons": "not shown"}\n'
    'always give me prices in a table with links -> {"prefers_price_answers": "a table with store links"}\n'
    'at my job i build mobile apps in flutter -> {"job_work": "builds mobile apps", "stack": "Flutter"}\n'
    'never ask me twice before opening an app -> {"prefers_app_opening": "open apps without asking twice"}')


def learn(text, save=True):
    """Runs in the background after every message: lasting facts about the owner (profile, professional
    data, working style, how they want ISHTI to behave) go to the exact fact store, updated in place.
    The chat skill puts every owner fact in its context, so preferences shape later answers."""
    from ishti import llm
    question = text.rstrip().endswith("?") or re.match(r"\s*(?:what|where|which|who|when|how|why|do|does|can|is|are)\b", text, I)
    if TELL.match(text.strip()) or not LEARNABLE.search(text) or len(text) > 1500 or (
            question and not re.search(r"\b(?:prefer|always|never|should)\b", text, I)):
        return {}
    owner = {a: v for a, v in get_facts().get("owner").items() if a != "entity"}
    known = "\n".join(f"- {a}: {v}" for a, v in owner.items()) or "(none)"
    raw = llm.ask(
        f"Already saved about the owner (never repeat these):\n{known}\n\nThe owner's message to their assistant:\n"
        f"{text}\n\nDoes this message state something NEW and LASTING about the owner - their studies, job, skills, "
        "projects, how they work - or a standing preference for how the assistant should behave? Most messages are "
        "just tasks and contain nothing: then output {}. But a message that says how the assistant should always, never "
        "or generally behave (answer format, length, language, UI, what it should or shouldn't do) IS a standing "
        "preference: save it. Never guess or describe the task itself. Keys are short "
        "snake_case, preferences about the assistant start with prefers_, reuse a saved key when it changes, values are short phrases that keep the owner's full meaning. "
        f"Examples:\n{LEARN_EXAMPLES}\n\nOutput only one JSON object (at most 3 entries).",
        system=None, temperature=0, max_tokens=120)
    try:
        found = json.loads(re.search(r"\{.*\}", raw, re.S)[0])
    except (TypeError, ValueError):
        return {}
    found = {re.sub(r"\W+", "_", str(k)).strip("_").lower()[:40]: str(v).strip()[:160]
             for k, v in (found.items() if isinstance(found, dict) else []) if str(v).strip()}
    # grounded: a saved value must share a real word (first 5 letters) with the message - the 14B model
    # otherwise copies its few-shot examples ("works at night" for a message that never says night)
    stems = {w[:5] for w in re.findall(r"[a-z0-9]{3,}", text.lower())}
    found = {k: v for k, v in found.items() if any(w[:5] in stems for w in re.findall(r"[a-z0-9]{3,}", v.lower())
                                                    if w not in ("not", "yes", "the", "and", "owner"))}
    same = lambda a, b: a.lower() in b.lower() or b.lower() in a.lower()
    found = dict([(k, v) for k, v in found.items()  # only what's new: the model likes to restate saved facts
                  if not any(same(v, str(old)) for old in owner.values()) and not (k in owner and same(v, owner[k]))][:3])
    if save:
        for k, v in found.items():
            get_facts().set("owner", k, v)
    return found


def about_me(t):
    from ishti import llm
    facts = "\n".join(f"- {a}: {v}" for a, v in get_facts().get("owner").items() if a != "entity") or "(nothing saved)"
    out = llm.ask(f"Saved facts about the owner:\n{facts}\n\nThe owner asks (may have typos): {t}\n\nAnswer in one short "
                  "sentence using only these facts, speaking to the owner as 'you'. If the facts don't contain it, say "
                  "you don't have that saved yet and ask them to tell you once.", system=None, temperature=0, max_tokens=80)
    return {"reply": out, "sources": []}


def about_ishti(t):
    from ishti import llm
    from ishti.dispatcher import SKILLS
    os.makedirs(os.path.dirname(FEEDBACK), exist_ok=True)
    with open(FEEDBACK, "a", encoding="utf-8") as f:  # read at the start of every build loop
        f.write(f"- {time.strftime('%Y-%m-%d %H:%M')} {t.strip()}\n")
    skills = "\n".join(f"- {n} ({s.status}): {s.manifest['route']}" for n, s in SKILLS.items())
    out = llm.ask(f"About you (ISHTI):\n{ISHTI_FACTS}\n\nYour skills:\n{skills}\n\nThe owner says: {t}\n\nAnswer honestly "
                  "in 1-3 short sentences as ISHTI, from the facts above only. Don't claim abilities you don't have. If "
                  "it's a complaint, acknowledge it and say it has been logged for the next build.",
                  temperature=0.2, max_tokens=160)
    return {"reply": out, "sources": []}


def run(text, session):
    t = text.strip()
    if TELL.match(t):
        return tell(t, session)
    if SELF.search(t):
        return about_ishti(t)
    if asks_about_me(t) and not any(rx.match(t) for rx in (WHO, KNOW, CAN)):
        return about_me(t)
    if VOICE_SET.match(t) or VOICE_LIST.search(t):
        from ishti.voice import current_voice, pick_voice, roster
        names = ", ".join(v["name"] for v in roster())
        chosen = pick_voice(t) if VOICE_SET.match(t) else None
        if not chosen:
            return {"reply": f"My voices: {names}. I'm using {current_voice()['name']}. Say e.g. \"use the deep male voice\".",
                    "sources": []}
        get_facts().set("owner", "voice", chosen["name"])  # remembered across restarts
        return {"reply": f"Switched to {chosen['name']}.", "sources": []}
    if AUDIO_OFF.match(t) or AUDIO_ON.match(t):
        if re.match(r"^(?:read|say)\b", t, I):  # "read that aloud": speak the last answer once
            last = next((m["content"] for m in reversed(session.get("history", [])) if m["role"] == "assistant"), "")
            if last:
                from ishti.voice import speak
                speak(last)
                return {"reply": "Reading it aloud.", "sources": []}
            return {"reply": "There's nothing to read yet.", "sources": []}
        if AUDIO_OFF.match(t):
            from ishti.talk import talker
            talker().stop()
            return {"reply": "Stopped speaking.", "sources": []}
        return {"reply": "Typed chats get text replies. For a spoken conversation press **Live** in the chat (or say "
                         "the wake word); \"read that aloud\" reads my last answer once.", "sources": []}
    if WHO.match(t):
        o = get_facts().get("owner")
        where = ", ".join(x for x in (o.get("city"), o.get("country")) if x)
        return {"reply": f"You're {o.get('name', 'my owner')}" + (f", {o['role']}" if o.get("role") else "")
                         + (f", in {where}" if where else "") + ".", "sources": []}
    if KNOW.match(t):
        f = get_facts()
        with f.lock:
            rows = f.db.execute("SELECT entity, attr, value FROM facts ORDER BY key, attr").fetchall()
        return {"reply": "\n".join(f"- {e} {a}: {v}" for e, a, v in rows) or "Nothing saved yet.", "sources": []}
    if CAN.match(t):
        # a person asking "what can you do" wants the gist, not 124 raw route strings: one line per area, with the skills in it
        from ishti.dispatcher import SKILLS
        areas = {}
        for n, s in SKILLS.items():
            if s.status == "working":
                areas.setdefault(s.category or "General", []).append(n.replace("_", " "))
        lines = [f"- **{cat}** ({len(ns)}): {', '.join(sorted(ns)[:6])}{' ...' if len(ns) > 6 else ''}" for cat, ns in sorted(areas.items())]
        return {"reply": f"I can do {sum(len(v) for v in areas.values())} kinds of things, all on this PC:\n" + "\n".join(lines)
                         + "\n\nJust ask in plain words - e.g. \"remind me at 5 to call Ammi\", \"send hi to Ishtiaq on WhatsApp\", "
                           "\"what's the price of bitcoin\". The full list is on the Skills page.", "sources": []}
    from orchestrator.doctor import IshtiDoctor
    # Interactive diagnostics must answer within a chat turn: 20 s cap (static checks + bounded live
    # probes). The DOCTOR tab can afford the full budget; a chat reply cannot (loop-2 fix).
    report = IshtiDoctor().check_all(budget_s=20)
    bad = report["errors"] + report["warnings"]
    return {"reply": ("All checks passed." if not bad else "Issues:\n" + "\n".join(f"- {b}" for b in bad[:8]))
                     + " Full report: DOCTOR tab.", "sources": []}


if __name__ == "__main__":
    assert match("what do you know about me?")
    assert not match("what is the weather")
    print("[self-check] owner OK - routing")
