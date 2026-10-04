"""Dispatcher: request -> route (rules / Laya) -> skill -> reply.
Text and voice both call handle(); voice is just another input channel."""
import io
import os
import json
import queue
import re
import threading
import time

from fastapi import APIRouter, Form, Request, UploadFile
from fastapi.responses import JSONResponse

from ishti import audit, killswitch, languages, prefs, reflex
from ishti.scope import ScopeError, install as _install_scope
from ishti.untrusted import clean_reply, note_for_owner

_install_scope()  # per-skill file/network/app limits (A5)
killswitch.propagate()  # a request's context (kill switch, skill scope, who initiated) follows it into worker threads

from ishti import CONFIG, ROOT
from ishti.facts import get_facts
from ishti import planner, router as router_mod
from ishti.router import route
from ishti.skills import answer_of, ask_first, enable_skill, load_skills, log_need
from orchestrator.memory.conversation import get_conversation_manager
from orchestrator.memory.error_logger import ErrorLogger
from orchestrator.response_guard import is_safe_for_history

SKILLS = load_skills()
SESSIONS = {}
_SESSIONS_LOCK = threading.RLock()
# Replies that hand the problem back to the owner. When an information-seeking skill gives one, the
# dispatcher tries the next capable skill once instead of stopping (memory/folders/reminders asking for a
# path or a time are legitimate questions, so they never escalate).
FELL_SHORT = re.compile(r"\b(?:couldn'?t|could not|can'?t|cannot|unable to|not able to|didn'?t (?:find|open|get|reach)|"
                        r"no (?:\w+ )?(?:results?|matching|information)|not found|doesn'?t (?:provide|show|contain|have)|"
                        r"(?:please )?visit\s+(?:https?://|www\.|the\s+\w+\s+(?:site|website|page))|"
                        r"i don'?t (?:know|have (?:access|information|real-time))|"
                        r"(?:isn'?t|is not|wasn'?t|are not|aren'?t|not) (?:listed|mentioned|specified|stated|available|provided|given)"
                        r"(?: in| on| by)?)", re.I)
# A reply that asks the owner for the missing detail ("which bug?", "could you tell me the file name?").
# Such a question is a correct answer to an ambiguous request - escalating it to web search searches the
# internet for the literal words ("Fix the bug" -> research hang, owner's live test 2026-10-03).
CLARIFY = re.compile(r"\b(?:which|what|who|where|whose)\b[^.?!\n]{0,80}\?|\bcould\s+you\s+(?:tell|clarify|share|specify|say)\b|"
                     r"\bcan\s+you\s+(?:tell|clarify|share|specify)\b|\bplease\s+(?:tell|clarify|specify|share)\b|"
                     r"\b(?:need|give|tell)\s+me\s+(?:more|the|a)\b|\bI\s+(?:don'?t|do not)\s+know\s+which\b", re.I)
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
    OWN = ("trace", "on_token", "route", "injections", "injected", "is_subagent")

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


def _restore_session(sid: str, conv=None) -> dict:
    """Create runtime state from durable chat history exactly once.

    Stored assistant messages use role ``ishti`` for UI compatibility; model APIs require
    ``assistant``. Incognito requests intentionally start with no persisted transcript.
    """
    from ishti.core import incognito

    shared = {"id": sid, "history": [], "pending": None, "last_entity": None}
    if incognito.enabled({"id": sid}):
        return shared
    try:
        manager = conv or get_conversation_manager()
        stored = manager.get_messages(sid) or []
    except Exception as exc:
        print(f"[dispatcher] saved chat history unavailable ({type(exc).__name__}: {exc})", flush=True)
        return shared

    from ishti.core.context import normalize_history, RUNTIME_MESSAGES
    history = normalize_history(stored)
    # Preserve enough recent dialogue for context while keeping per-session memory bounded.
    shared["history"] = history[-RUNTIME_MESSAGES:]
    shared["history_truncated"] = len(history) > RUNTIME_MESSAGES
    return shared


def _runtime_session(sid: str, conv=None) -> dict:
    """Thread-safe session lookup; lazily hydrates old chats after restart or session switch."""
    with _SESSIONS_LOCK:
        shared = SESSIONS.get(sid)
        if shared is None:
            shared = _restore_session(sid, conv)
            SESSIONS[sid] = shared
        return shared


def _arch_registry():
    """arch2.registry or None (arch2 optional - its absence must never break arch1)."""
    try:
        from ishti.arch2 import registry
        return registry
    except Exception as exc:
        print(f"[dispatcher] arch2 unavailable: {exc}")
        return None


def _remember(text, reply, skill):
    def _bg():
        try:
            from ishti.semantic import get_semantic
            get_semantic().add(f"User: {text}\nISHTI: {reply[:800]}", skill=skill)
        except Exception as exc:
            print(f"[dispatcher] semantic memory write skipped: {exc}")
    threading.Thread(target=_bg, daemon=True).start()


def _ftlog(text, reply, name, dec, channel):
    """Behavior-data collection (Part 3): one exchange with its routing decision for the
    fine-tuning pipeline. Secrets are redacted in ishti.ftlog; secrets this request marked
    (session redact) never arrive here at all."""
    try:
        from ishti import ftlog
        ftlog.record(text, reply, name, dec.get("source", ""), channel=channel)
    except Exception as exc:
        print(f"[dispatcher] ftlog write skipped: {exc}")


# Process chatter is NEVER spoken (owner rule E): step labels, tool names and progress lines are shown on the
# dashboard only. Voice answers are the final reply alone (ishti/talk.py enforces the same rule at the speech
# boundary, so no path can narrate a step).


def _learn(text):
    try:
        from ishti.skills.owner.skill import learn
        if found := learn(text):
            print(f"[dispatcher] learned about the owner: {found}")
    except Exception as exc:
        print(f"[dispatcher] owner learning skipped: {exc}")
    try:
        from ishti.skills.relationships.skill import extract_and_learn_kin
        if kin_found := extract_and_learn_kin(text):
            print(f"[dispatcher] learned kin relation: {kin_found}")
    except Exception as exc:
        print(f"[dispatcher] kin learning skipped: {exc}")


def _learn_turn(text, reply, name, dec, sid, channel, message_id=None):
    """Background learning hook: turn observation, feedback harvesting, reward attribution."""
    try:
        from ishti.learning import observe, feedback, policy, reflect, lessons
        # 1. Check for feedback/corrections on this turn
        fb = feedback.detect(text, last_skill=name)
        # A completed turn has unknown correctness until the owner confirms it.
        reward = feedback.reward_of("unreviewed", fb=fb)

        # 2. Log interaction
        iid = observe.turn(text=text, reply=reply, skill=name or "", sid=sid,
                           channel=channel, dec=dec, status="ok", reward=reward,
                           message_id=message_id or "")
        if message_id:
            rated = get_conversation_manager().feedback_target(sid, message_id)
            rating = (((rated or {}).get("message") or {}).get("metadata") or {}).get("feedback", {}).get("rating")
            if rating:
                feedback.apply_answer_rating(sid, message_id, rating)

        # 3. If explicit feedback, apply it
        if fb:
            feedback.apply(fb, interaction={"id": iid, "skill": name, "request": text})

        # 4. Check for temporary instructions
        policy.temporary_instruction(text)

        # 5. Close the reinforcement loop - but only for turns where the model actually read the
        #    guidance. `chat.build_context()` is currently the only place `context_for()` reaches
        #    a prompt; crediting/blaming a lesson for a browser turn where it was never shown
        #    would demote a rule because of a failure it had no part in. The set is a registry, not
        #    a hard-coded rule: as more skills inject lessons, they get added here.
        if reward and (name or "") in lessons.CONTEXT_INJECTING_SKILLS:
            # `guidance_for()` is a pure function of the request, so this returns the same lessons
            # `build_context()` just put in front of the model.
            domain = str((dec or {}).get("domain") or "") if isinstance(dec, dict) else ""
            applied = [l["lesson_id"] for l in lessons.guidance_for(text, domain=domain, limit=6)]
            if applied:
                lessons.apply_turn_outcome(iid, reward, applied,
                                           reason="turn outcome attributed to the guidance in context")

        # 6. Reflection (Reflexion): a number cannot tell the next turn what went wrong, so write
        #    the sentence too. Only when there is a signal - a store full of "nothing happened"
        #    reflections is noise that drowns out the ones that mean something. Reflections are
        #    kept for *every* skill: they are a record of the turn, recalled later by words.
        if reward or fb:
            reflect.for_turn({"id": iid, "request": text, "skill": name or ""}, reward, fb)

        # 7. Daily maintenance (AdMem): fade un-confirmed lessons that have gone stale.
        #    `maybe_decay()` throttles itself to once per decay_interval_s, so this is safe to
        #    call on every turn without turning each reply into a table scan.
        lessons.maybe_decay()
    except Exception as exc:
        print(f"[dispatcher] learning turn hook skipped: {exc}")


URDU = re.compile("[؀-ۿ]")  # Arabic-script letters (Urdu)
DEVANAGARI = re.compile("[ऀ-ॿ]")  # Hindi script
PASHTO = re.compile("[ټځڅډړږښګڼۍې]")  # Pashto-only letters
# The owner's language lock: "talk to me in English", "answer in urdu", "switch your language to Pashto".
# It is a standing preference (fact owner.prefers_language), not a question to research - the owner asked for
# exactly this after ISHTI answered Urdu replies with an English research report.
LANGUAGE_CMD = re.compile(
    r"\b(?:talk|speak|reply|answer|respond|write|message|chat)\s+(?:to\s+me\s+)?(?:only\s+|always\s+)?in\s+"
    r"(?P<lang>english|urdu|pashto|roman\s+urdu)\b|"
    r"\b(?:switch|change|set)\s+(?:your\s+)?(?:reply\s+|response\s+)?language\s+to\s+(?P<lang2>english|urdu|pashto|roman\s+urdu)\b|"
    r"\b(?:switch\s+to|force)\s+(?P<lang3>english|urdu|pashto|roman\s+urdu)\b|"
    r"\b(?:speak|talk)\s+(?P<lang4>english|urdu|pashto|roman\s+urdu)\b|"
    r"\b(?:talk|speak|reply|answer)\s+to\s+me\s+in\s+my\s+(?:own\s+)?language\b|"
    r"\b(?:auto\s+language|detect\s+language|auto\s+detect(?:\s+language)?)\b", re.I)
LANGS = {"english": "English", "urdu": "Urdu", "pashto": "Pashto", "roman urdu": "Roman Urdu"}
LANG_TARGET = {"English": "English", "Urdu": "Urdu", "Pashto": "Pashto",
               "Roman Urdu": "Roman Urdu (Urdu written in English letters)"}
LANG_CONFIRM = {"English": "Got it - I'll answer in English from now on.",
                "Urdu": "ٹھیک ہے - اب میں اردو میں جواب دوں گا۔",
                "Pashto": "ښه - له اوس څخه به په پښتو ځواب وایم.",
                "Roman Urdu": "Theek hai - ab main Roman Urdu mein jawab dunga."}


def preferred_language():
    """The owner's locked answer language, or None (mirror whatever the owner speaks). Stored as a fact so it
    survives restarts and reaches the LLM style prompt (llm.style() lists prefers_* facts)."""
    try:
        pref = (get_facts().get("owner").get("prefers_language") or "").strip() or None
    except Exception:
        return None
    return pref if pref and languages.allowed(pref) else None   # a lock on a language that is switched off does nothing
FOLLOW_UP = re.compile(
    r"^\s*(?:why\s+(?:did|didn'?t|did\s*not|didnot|don'?t|won'?t)\s+you|(?:no[,.!]?\s+)?i\s+(?:asked|meant|said|wanted)|"
    r"(?:search|look|check|try|do)\s+(?:it\s+|that\s+|this\s+)?(?:now|again|instead|properly|online)|"
    r"(?:and\s+)?(?:now|then|also)\s+(?:send|message|say|tell|do|search|check|try|make|use)|what\s+about|same\s+(?:for|with)|"
    r"(?:not|no)\s+(?:that|this|my)\b)"
    r"|\b(?:do\s+it|redo|again|instead|like\s+(?:that|before)|the\s+(?:first|second|third|last|previous)\s+one|"
    r"you\s+(?:added|moved|put|did|made|said|gave)|you\s+(?:didn'?t|didnot|did\s+not)\s+(?:even\s+)?\w+|"
    r"(?:add|save|put|store|update|write)\s+(?:it\s+|this\s+|that\s+)?(?:in|to|into)\s+(?:the\s+|your\s+|my\s+)?memory|"
    # "https://imsciences.edu.pk/ try here": the earlier question, on this site
    r"(?:try|look|check|search|find\s+it)\s+(?:it\s+)?(?:here|there|on\s+this|in\s+this)|(?:on|use)\s+this\s+(?:site|website|page|link))\b|"
    # sending/complaints after a send: "not sent yet send her now", "didn't send it", "resend that", "send it now"
    r"\b(?:(?:not|wasn'?t|hasn'?t\s+been|isn'?t)\s+(?:really\s+)?(?:sent|delivered|posted|done)|"
    r"(?:did|didn'?t|did\s+not)\s+(?:send|deliver|post)|you\s+(?:said|claimed)\s+you\s+(?:sent|did)|"
    r"(?:re)?send\s+(?:it|that|this|them|him|her)(?:\s+(?:now|again|properly))?|"
    r"send\s+(?:it|that|this)\s+(?:now|again)|"
    r"now\s+send\b|send\s+(?:and\s+)?say\b|retry\b|resend\b)\b", re.I)


def contextualize(text, s) -> str:
    """Follow-ups ("search now", "why didn't you search?", "do it using the names instead") only make
    sense with the conversation. Rewrite them into one standalone request before routing, so the right
    skill gets the full intent (the standard condense-question-with-history step).

    Part D2: the Conversation State Manager resolves first (topic-anchored, deterministic, instant);
    the LLM rewriter is only asked when the state cannot resolve. Every resolved request is stored in
    the session (s["resolved_request"]) so validation and skills see WHAT we're acting on."""
    if _rule_owned(text):  # keep exact skill commands unless a research follow-up omits its topic
        resolved = resolve_request(text, s)  # still updates the conversation state (topic, entities)
        if resolved and resolved.changed and route(text, SKILLS).get("skill") in {
            "web_search", "deep_research", "shopping"
        }:
            # "go deep research ... cheapest price" names the task but omits the
            # product. The rule route is correct, yet the search needs its topic.
            return resolved.request
        return text
    resolved = resolve_request(text, s)
    if resolved is None:
        # state had nothing to anchor on: fall back to the original LLM rewriter for FOLLOW_UP phrasings
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
    if resolved.ambiguous:
        # ONE clarifying question, then the owner's answer completes the resolution (T3)
        from ishti.skills import ask_first
        question = resolved.clarify_question
        s["resolved_request"] = resolved.original
        s["pending"] = {"skill": "resolve_ambiguous", "gate": question, "sticky": True,
                        "request": resolved.original}
        s["trace"](type="tool", name="understand", label=f"ambiguous - asking which one is meant", status="done")
        return f"__CLARIFY__{question}"
    if resolved.changed:
        s["trace"](type="tool", name="understand",
                   label=f"resolved as: {resolved.request[:120]}", status="done",
                   original=resolved.original, rewritten=resolved.request)
    return resolved.request


def resolve_request(text: str, s) -> "object | None":
    """D2: resolve the message against the Conversation State (ishti.core.resolver).
    Returns a Resolved or None when the state has no topic/searches to anchor on.
    Also updates the state with this turn's topic/task/entities/constraints."""
    try:
        from ishti.core import state as cstate
        from ishti.core.resolver import QueryResolver
        mgr = cstate.get_manager()
        sid = s.get("id", "default")
        st = mgr.get(sid)
        with mgr.turn(sid) as fresh:
            mgr.observe_user_turn(fresh, text)
        # re-read so the resolver sees the updated topic/task
        st = mgr.get(sid)
        r = QueryResolver().resolve(text, st)
        if not r.changed and not r.ambiguous and not st.active_topic and not st.last_searches:
            return None
        s["resolved_request"] = r.request
        s["state_summary"] = cstate.ConversationStateManager.summary(st)
        return r
    except Exception as exc:  # the resolver must never break the turn
        try:
            s["trace"](type="tool", name="understand", label=f"resolver skipped ({type(exc).__name__})", status="error")
        except Exception:
            pass
        return None


def fell_short(question, reply, skill, s) -> bool:
    """Did the answer hand the problem back? Phrase check first; for information skills also ask the
    local model whether the reply states what was actually asked (a number, name, date...) - one word,
    ~0.4 s on the already-loaded planner model."""
    reply = str(reply or "")  # a skill can return no reply at all; never crash the check on None
    if not reply.strip():
        # Blank is the worst way to hand the problem back: it answered nothing - and the escalation
        # loop used to treat it as a BETTER answer that replaced a real one (2026-10-01 sweep: a blank
        # fallback beat web_search's honest "couldn't find" reply, leaving an empty bubble).
        return True
    # A clarifying question is a correct answer, not a fall-short: "Fix the bug" -> "I don't know which bug
    # you're referring to. Could you tell me which one?" was escalated chat -> web_search -> deep_research,
    # which searched the internet for "Fix the bug" and hung (owner's live test, 2026-10-03). Only a reply
    # that DEAD-ENDS is a fall-short; one that asks the owner for the missing detail is doing its job.
    if CLARIFY.search(reply) and reply.rstrip().endswith("?"):
        return False
    if FELL_SHORT.search(reply):
        return True
    # the browser engine already re-checks the request against the actions that really ran (its CHECK loop and
    # grounded summary); the one-word judge rejected its correct answers ("main heading ... Example Domain" -> no)
    if skill not in ("web_search", "web_fetch"):
        return False
    # a summary isn't "a concrete value, name, number or fact": the judge said no to good summaries (a recipe page)
    if re.match(r"^\s*(?:please\s+)?(?:summari[sz]e|read|explain|describe|what(?:'s|\s+is)\s+(?:on|in))\b", question, re.I):
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


T2_ACTION = re.compile(r"\b(?:send|message|reply|delete|remove|erase|pay|buy|order|purchase|post|publish|submit|"
                       r"install|uninstall|kill|shut\s*down|restart|transfer|forward|empty)\b", re.I)
# First-person claims that an action happened. Only trusted when the skill marks the run verified; a skill that
# says it could not confirm the action gets the claim followed by an honest note (the owner's WhatsApp sends
# were reported as done while nothing had gone out).
ACTION_CLAIM = re.compile(r"\b(?:reminder|timer|alarm|task|message|video|file|event|meeting)s?\b[^.\n]{0,40}\b(?:has\s+been|is|was|been)\s+"
                          r"(?:set|scheduled|added|cancel+ed|sent|saved|deleted|created|edited|opened|booked|updated)\b"
                          r"|^\s*[-*]?\s*\**(?:reminder|timer|alarm)\s+(?:set|scheduled|cancel+ed)\b"
                          r"|\bi(?:'ve|\s+have)?\s+(?:set|scheduled|added|cancel+ed|opened|saved|sent|created|edited|deleted|booked)\s+"
                          r"(?:a|an|the|your|it|that|this)\b", re.I | re.M)
CLAIM = re.compile(r"\b(?:i(?:'ve| have)?\s+(?:sent|shared|posted|published|uploaded|forwarded|moved|deleted|paid|"
                   r"ordered|booked|installed|created|saved)|(?:sent|delivered|shared|posted)\s+"
                   r"(?:it|that|this|the\s+\w+|['\"])|i\s+have\s+already\s+(?:sent|done))", re.I)
# A first-person claim that something irreversible happened ("Sent 'hi' to Fuzail on WhatsApp", "Reminder set").
# When the answer came from the brain with no tool behind it, this is the guard that stops the false claim.
FALSE_ACTION = re.compile(
    r"\bi(?:'ve| have)?\s+(?:just\s+|already\s+)?(?:sent|shared|posted|published|uploaded|forwarded|moved|deleted|"
    r"paid|ordered|booked|installed|created|saved|scheduled|set|cancel+?ed|added|reminded|emailed)\b"
    r"|^\s*[-*]?\s*\**(?:reminder|timer|alarm)\s+(?:set|scheduled|cancel+ed)\b"
    r"|^\s*(?:sent|delivered)\b[^.\n]{0,60}\b(?:on\s+whatsapp|to\s+\w+)", re.I | re.M)


def _repeat(s, name, text, window_s=20):
    """True if the same sensitive action (not a read) ran in this chat within window_s seconds."""
    last = s.get("last_t2")
    return bool(T2_ACTION.search(text) and last and last[0] == _t2_key(name, text) and time.time() - last[1] < window_s)


def _t2_key(name, text):
    return name, re.sub(r"\W+", " ", text.lower()).strip()


def _did_t2(s, name, text):
    """Remember a sensitive action that actually ran (not one that was only asked about or cancelled)."""
    s["last_t2"] = (_t2_key(name, text), time.time())


# next to the memory DB, so checks/replays that run on a scratch memory never leave a false "before I restarted" note.
# ISHTI_INFLIGHT_FILE lets an isolated run (the task suite's per-case subprocess) keep its own hint file
# without reading or deleting the owner's real one.
INFLIGHT_FILE = os.environ.get("ISHTI_INFLIGHT_FILE") or os.path.join(
    os.path.dirname(CONFIG["memory"]["db_path"]), "inflight.json")


_inflight_state = None          # the file's content, read once (RECOVER already removed it after a crash)
_inflight_q = None              # coalesced writes: one thread does the file IO off the reply path


def _inflight(sid, text=None):
    """Requests running right now, on disk: if ISHTI crashes or restarts mid-task, the next start knows what was lost.

    Measured 2026-10-01: the old read-modify-write per call (2 reads + 2 writes per request) cost
    ~6 ms on the reply path (Windows file opens + AV scanning on close). The state is a crash-
    recovery HINT, never reply data, so the writes go through one coalescing daemon thread: the
    reply path only updates memory and enqueues. Worst case the file is a few writes stale at a
    crash - the recovery note is best-effort by design.
    """
    global _inflight_state, _inflight_q
    if _inflight_state is None:
        try:
            with open(INFLIGHT_FILE, encoding="utf-8") as f:
                _inflight_state = json.load(f)
        except (OSError, ValueError):
            _inflight_state = {}
        _inflight_q = queue.Queue()
        threading.Thread(target=_inflight_writer, daemon=True, name="inflight-writer").start()
    if text is None:
        _inflight_state.pop(sid, None)
    else:
        _inflight_state[sid] = {"text": text, "t": time.time()}
    _inflight_q.put(_inflight_state)


def _inflight_writer():
    import queue as _q
    while True:
        data = _inflight_q.get()
        while True:  # drain to the latest: bursts coalesce into one write
            try:
                data = _inflight_q.get_nowait()
            except _q.Empty:
                break
        try:
            with open(INFLIGHT_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f)
        except OSError:
            pass


def _recover():
    try:
        with open(INFLIGHT_FILE, encoding="utf-8") as f:
            left = list(json.load(f).values())
        os.remove(INFLIGHT_FILE)
        return left
    except (OSError, ValueError):
        return []



@killswitch.on_kill
def _drop_questions():
    """A kill also drops every waiting yes/no question, so a later "yes" can't run a half-forgotten action."""
    n = sum(1 for sh in SESSIONS.values() if sh.get("pending"))
    for sh in SESSIONS.values():
        sh["pending"] = None
    return f"{n} pending question(s)" if n else ""

RECOVER = _recover()  # what the previous run was still doing when it stopped


def _run(text, s, dec):
    """Run the decided skill; if it declines ("not mine"), fall through to the next best one."""
    from ishti import calltrace
    tried, out, name = set(), None, None
    clean_explicit = re.sub(r"^(?:\[skill:[a-z0-9_-]+\]|/(?:use|skill)\s+[a-z0-9_-]+)\s*", "", text, flags=re.I).strip()
    if clean_explicit:
        text = clean_explicit
    while dec["skill"]:
        name = dec["skill"]
        s["route"] = dec
        s["trace"](type="step", skill=name, status="start", why=why(dec), text=text)
        tier = SKILLS[name].manifest.get("tier", "T1")
        try:
            if dec.get("needs_enable"):  # a switched-off skill is what this needs: turning it on is the owner's call
                out = ask_first(s, name, f"That needs the {name.replace('_', ' ')} skill, which is switched off. Turn it on and do it",
                                lambda n=name, t=text: enable_skill(n) and (SKILLS[n].run(t, s) or {"reply": "Turned on. Nothing to do for that."}))
            elif tier == "T3" and not CONFIG.get("allow_t3", {}).get(name):  # permission check before, not after
                out = {"reply": f"The {name} skill is blocked (tier T3). Enable it per skill in config.json "
                                f"\"allow_t3\" if you really want it."}
            elif tier == "T2" and dec.get("source") != "pending" and _repeat(s, name, text):
                # rate-limit guard: the same sensitive request twice in a row is usually a double trigger
                out = ask_first(s, name, "You asked for exactly this a moment ago - do it again",
                                lambda n=name, t=text: SKILLS[n].run(t, s) or {"reply": "Nothing to do."})
            else:
                from ishti import test_mode
                if tier == "T2" and test_mode.enabled() and not str(text or "").strip().lower() in ("yes", "y"):
                    # ISHTI_TEST_MODE layer 1: a T2-tier skill never executes at all under test mode -
                    # its whole job may be a side effect. Instead of the opaque "never touch the real
                    # world" line, answer with the owner-facing gate: a world-changing request gets a
                    # real confirmation question (and a pending yes/no), everything else a dry-run note.
                    # The yes path is still stopped by blocked() at the last step (layer 2).
                    gate_txt = f"run '{mask(text, s.get('redact'))[:120]}'"
                    out = test_mode.gated_reply(name, gate_txt, tier)
                    if out.get("confirm_required"):
                        s["pending"] = {"skill": name, "gate": gate_txt, "request": text,
                                        "action": (lambda n=name, g=gate_txt, t=tier:
                                                   test_mode.blocked(n, g, None, t))}
                else:
                    with audit.context(skill=name, initiator=initiator_of(dec, s, tried), tier=tier, view=s):
                        audit.record(f"run {name}", why(dec))
                        if not dec.get("source"):  # the rule: no skill call without a concrete reason in the plan (ishti/calltrace.py)
                            calltrace.flag(f"{name} ran for '{mask(text, s.get('redact'))[:60]}' with no route reason")
                        with calltrace.timed("skill", name, why=why(dec)[:90]):
                            out = SKILLS[name].run(text, s)
                        killswitch.check()  # killed mid-skill: whatever it returned (often a swallowed error) is dropped
                gate = s.get("pending")
                if gate and gate.get("action") and "request" not in gate:
                    gate["request"] = text  # so a "yes" can record the action that ran
                elif tier == "T2" and out and not str(out.get("reply", "")).rstrip().endswith(("?", "(yes/no)")):
                    _did_t2(s, name, text)  # done without a question (e.g. an exact-match WhatsApp send)
        except Exception as exc:
            if type(exc).__name__ == "Cancelled":  # voice barge-in aborted the reply: not a skill failure
                raise
            ErrorLogger.log_error(subsystem=f"SKILL_{name.upper()}", error=exc, user_prompt=text,
                                  context="dispatcher.handle")
            log_need("error", name, text, f"{type(exc).__name__}: {str(exc)[:120]}")
            audit.record(f"{name} failed", f"{type(exc).__name__}: {str(exc)[:200]}", skill=name)
            if isinstance(exc, ScopeError):  # a permission boundary, not a bug: say it plainly
                out = {"reply": f"I stopped: {exc}. If that should be allowed, widen the skill's \"scope\" in its manifest."}
            else:
                out = {"reply": f"The {name} skill failed: {type(exc).__name__}: {str(exc)[:160]}"}
        if out is not None:
            if out.get("verified") is False and CLAIM.search(str(out.get("reply", ""))):
                # tool honesty: a skill that knows it could not confirm an action must never sound like it did
                out["reply"] = str(out["reply"]) + "\n\n(I couldn't confirm that actually went through - please check.)"
                s["trace"](type="tool", name="verify", label="the action couldn't be confirmed, so I said so", status="error")
            if name == "chat" and FALSE_ACTION.search(str(out.get("reply") or "")):
                # the model SAID an irreversible thing happened but no tool ran. "Did you send it?" is answered
                # from the conversation on purpose (chat.skill.did_you) - everything else gets the honest correction.
                from ishti.skills.chat.skill import DID_YOU
                is_status = bool(re.search(r"^\s*(?:is|are|was|were|status|check\s+if)\b", text, re.I))
                if not DID_YOU.match(text) and not is_status and not (re.search(r"\bdraft\b", str(out.get("reply") or ""), re.I) and re.search(r"\bdraft\b", text, re.I)):
                    out["reply"] = ("I haven't actually done that yet - nothing was sent or changed just now. "
                                    "Say the word and I'll go ahead.")
                    s["trace"](type="tool", name="verify", label="the reply claimed an action no tool ran - corrected", status="error")
            s["trace"](type="step", skill=name, status="done", summary=str(out.get("reply") or "")[:160])
            break
        s["trace"](type="step", skill=name, status="skip", summary=f"{name} passed it on")
        tried.add(name)
        pend = s.get("pending")
        if pend and pend.get("sticky") and pend.get("skill") == name:
            s["pending"] = None  # it asked a question but this wasn't the answer: stop holding the message
        dec = route(text, SKILLS, exclude=tried)
    if out is None:
        log_need("gap", "none", text, "no skill could handle this request")
        creator = SKILLS.get("skill_creator")
        if creator and len(text.split()) >= 3:  # self-extending: offer to research and build the missing skill (nothing runs before the yes)
            name, out = "skill_creator", ask_first(s, "skill_creator", "I have no skill for that yet. Should I research it, build one and try it "
                                                   "(it stays switched off until you approve it)",
                                                   lambda t=text: (creator.module.build(t, s) or {}).get("reply", "Nothing was built."))
        else:
            name, out = None, {"reply": "I can't do that yet - no skill handles it. I've logged it as a skill to add."}
    get_facts().log_route(mask(text, s.get("redact")), name, dec.get("confidence", 0), dec.get("source", ""), json.dumps(dec))
    return name, out, dec


_N = r"(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten)"
# ISHTI's research progress lines, as the microphone hears them back from the speakers (all from the owner's chat):
# "Found three sources, opening the top three." / "We have five sources opening the top four." / "Searching the internet for-"
ECHO = re.compile(r"\b(?:found|we\s+have)\s+" + _N + r"\s+(?:re)?sources?\s*(?:,|\.|opening|$)|\bopening\s+the\s+top\s+" + _N + r"\b|"
                  r"^\s*searching\s+the\s+(?:internet|web)\s+for\b|\bwatching\s+the\s+internet\s+for\b|\btrying\s+a\s+different\s+search\b|"
                  r"^\s*(?:re)?sources?,?\s+opening\b", re.I)
LEAD_IN = re.compile(r"^\s*(?:hey|hi|hello(?!\s+world)|ok(?:ay)?|so|bro|yaar|ishti|listen|well)\b[,!.]?\s+", re.I)


def _calm(text):
    """ALL-CAPS typing ("WHO IS MY BROTHER") reads as normal case: names, rules and the models all expect it. Quoted text and paths
    keep their case."""
    # "Hey who is my brother?" / "bro, how are you" / "ok so open whatsapp": the greeting is not part of the request
    while (m := LEAD_IN.match(text)) and len(text[m.end():].split()) >= 2:
        text = text[m.end():]
    letters = [c for c in text if c.isalpha()]
    if len(letters) < 4 or sum(c.isupper() for c in letters) / len(letters) < 0.8:
        return text
    parts = re.split(r'("[^"]*"|\'[^\']*\'|[A-Za-z]:\\[^\s"]+)', text)
    out = "".join(p if i % 2 else p.lower() for i, p in enumerate(parts))
    return out[:1].upper() + out[1:]


GENERAL = {"chat", "web_search", "memory", "deep_research"}  # open-ended: the arch2 brain decides those itself


def _rule_owned(text):
    """A request a skill's own trigger claims exactly ("send hi to Ali on WhatsApp", "my brother is Irfan", "remind me at 5") runs
    that skill directly, in both architectures. arch2's brain picking among 20 tools by itself sent WhatsApp requests to web search
    and stored the owner's brother under a misspelled name (owner's chats, 2026-10-02/03).

    Chat also owns timeless knowledge questions ("what's the capital of Kazakhstan", "define imatrix") directly -
    these are not open-ended debates arch2 needs to plan, and arch2's smaller model answered "Azerbaijan" for the
    Kazakh capital (owner's live test, 2026-10-03). The GENERAL set still lets arch2 own truly open-ended requests
    ("talk about AI", "what do you think of politics"), but factual one-shot questions belong to chat.

    A GENERAL skill (chat/web_search/memory/deep_research) that matched by rule for a SPECIFIC, well-defined request
    (a web search query, a memory command, a factual question) is also rule-owned: arch2 only takes the ones that are
    genuinely open-ended. TheIntentDetector is the discriminator."""
    try:
        dec = route(text, SKILLS)
        if dec.get("source") == "explicit_directive":
            return True
        skill = dec.get("skill")
        if skill == "chat":  # "did you send it?" is answered from the conversation, never re-run or improvised
            from ishti.skills.chat.skill import DID_YOU, KNOWLEDGE, PERSONAL, ARITHMETIC, CREATIVE, CODE, BACKCHANNEL
            if DID_YOU.match(text):
                return True
            # timeless knowledge: chat answers directly, not arch2 (smaller model, wrong facts)
            if KNOWLEDGE.match(text) and not PERSONAL.search(text) and len(text.split()) <= 14:
                return True
            # chat matching arithmetic/creative/code/backchannel: these are specific enough that chat should
            # handle them (including ambiguous code questions like "fix the bug" -> chat asks which bug)
            if ARITHMETIC.search(text) or CREATIVE.search(text) or CODE.search(text) or BACKCHANNEL.search(text):
                return True
            # chat matched as the route choice but none of the specific patterns fired - fall through to the
            # default-skill check below (chat may still own it as the default, or arch2 may plan it)
            pass
        if dec.get("source") in ("rule", "learned") and skill not in GENERAL:
            return True
        # A GENERAL skill matched by rule for a specific request: treat it as rule-owned too.
        # "Is Ollama still maintained?" -> web_search (QUICK_SEARCH), not an open-ended debate.
        if dec.get("source") in ("rule", "learned") and skill in GENERAL:
            if skill == "web_search":
                from ishti.skills.web_search.skill import SEARCH
                if SEARCH.match(text):
                    return True
            from orchestrator.intent_detector import IntentDetector
            mode = IntentDetector.classify(text)["mode"]
            # Specific, well-defined intents are rule-owned; open-ended ones go to arch2.
            if mode in ("QUICK_SEARCH", "WEB_FETCH", "WEATHER", "GREETING"):
                return True
            # chat matching LOCAL_KNOWLEDGE for a factual question is rule-owned (timeless knowledge)
            if skill == "chat" and mode == "LOCAL_KNOWLEDGE":
                return True
        # No skill matched by rule: the default skill (usually chat) should handle it, not arch2.
        # "Send this email now" / "Do the usual thing" are ambiguous requests that chat handles by asking
        # for clarification - arch2's bigger model doesn't add value for simple clarification questions.
        if dec.get("source") == "default" and dec.get("skill") == CONFIG.get("router", {}).get("default_skill", "chat"):
            return True
        return False
    except Exception:
        return False


def handle(text: str, session_id: str = None, channel: str = "text", on_token=None, on_event=None) -> dict:
    """on_token(str): live stream of the reply for skills that generate it token by token.
    on_event(dict): live trace (route, plan, steps, tool calls) for the UI's Thinking panel and graph.
    Skills add their own tool events with session["trace"](type="tool", ...)."""
    t0 = time.perf_counter()
    killswitch.begin()
    from ishti.connectors import credential_secrets
    typed_secrets = credential_secrets(text)
    from ishti import calltrace
    calltrace.begin(mask(text, typed_secrets))
    conv = get_conversation_manager()
    sid = session_id or conv.active_session_id or conv.new_chat("New Session")
    shared = _runtime_session(sid, conv)
    if typed_secrets:
        shared["redact"] = list(dict.fromkeys([*(shared.get("redact") or []), *typed_secrets]))
    events = []  # kept with the answer so a reopened chat still shows its thinking and graph

    def trace(**ev):
        ev = {"t": round((time.perf_counter() - t0) * 1000), **ev}
        if typed_secrets:
            ev = json.loads(mask(json.dumps(ev, ensure_ascii=False), typed_secrets))
        if ev.get("type") == "tool" and ev.get("status") in ("done", "error") and ev.get("label"):
            audit.record(f"{ev.get('name', 'tool')}: {ev['label']}", "failed" if ev["status"] == "error" else "",
                         session=sid)
        events.append({k: v for k, v in ev.items() if k not in ("sample", "results")})
        if on_event:
            on_event(ev)
        if HOOKS["emit"]:  # every step also goes to the dashboard's live stream (voice/wake requests have no chat tab)
            HOOKS["emit"]("trace", {**{k: v for k, v in ev.items() if k not in ("sample", "results")},
                                    "session": sid, "channel": channel})
    s = View(shared, trace=trace, on_token=on_token)
    s["trace"](type="received", text=text, channel=channel)
    text = _calm(text)
    if ECHO.search(text) and len(text.split()) <= 14:
        # ISHTI's own progress line came back through the microphone ("Found three sources, opening the top three." was
        # transcribed and searched for, in the owner's chat): never act on it.
        out = {"reply": "(That was my own status line coming back through the microphone - I ignored it.)", "sources": [], "silent": True}
        return _finish(text, sid, get_conversation_manager(), s, events, t0, channel, "echo", out,
                       {"skill": "echo", "source": "rule", "confidence": 1.0})

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
        # task tracker: what this chat has on the go ("what's the status of my tasks"); the last 10 are kept
        task = {"topic": mask(text, typed_secrets)[:80], "status": "running", "started": time.time(), "result": ""}
        shared["tasks"] = (shared.get("tasks", []) + [task])[-10:]
    _inflight(sid, mask(text, typed_secrets))
    try:
        from ishti import lifecycle
        # Still starting (the dashboard opens ~1 s after launch, the models keep loading): say what is loading and queue
        # the request instead of failing or hanging in silence.
        not_ready = lifecycle.await_brain(lambda m: s["trace"](type="tool", name="startup", label=m, status="waiting"))
        if not_ready:
            s["trace"](type="done", skill=None, ms=round((time.perf_counter() - t0) * 1000))
            return {"request": text, "final_answer": not_ready, "mode": "STARTING", "skill": None, "sources": [], "session_id": sid,
                    "channel": channel, "route": {}, "latency_ms": round((time.perf_counter() - t0) * 1000)}
        with audit.context(session=sid, channel=channel, request=text, redact=typed_secrets):
            audit.record(f"request: {text[:150]}", initiator="standing" if channel == "scheduled" else "owner", skill="")
            res = _handle(text, sid, conv, s, events, t0, channel)
        task.update(status="done", result=res["final_answer"][:200])
        return res
    except BaseException as exc:
        task.update(status="cancelled" if type(exc).__name__ == "Cancelled" else "failed", result=str(exc)[:200])
        if type(exc).__name__ == "Cancelled" and killswitch.killed():  # the kill switch: a clean stop, back to idle
            s["trace"](type="done", skill=None, ms=round((time.perf_counter() - t0) * 1000))
            return {"request": text, "final_answer": "Stopped.", "mode": "STOPPED", "skill": None, "sources": [],
                    "session_id": sid, "channel": channel, "route": {}, "latency_ms": round((time.perf_counter() - t0) * 1000)}
        if type(exc).__name__ == "Cancelled" or not isinstance(exc, Exception):
            raise  # barge-in / shutdown: not a failure to report
        ErrorLogger.log_error(subsystem="DISPATCHER", error=exc, user_prompt=mask(text, typed_secrets), context="dispatcher.handle")
        log_need("error", "dispatcher", mask(text, typed_secrets), f"{type(exc).__name__}: {str(exc)[:120]}")
        audit.record("request failed", f"{type(exc).__name__}: {str(exc)[:200]}", skill="dispatcher", session=sid)
        reply = (f"Something went wrong inside me ({type(exc).__name__}: {str(exc)[:120]}). I logged it and I'm still "
                 "running - try again, or rephrase.")
        s["trace"](type="done", skill=None, ms=round((time.perf_counter() - t0) * 1000))
        return {"request": text, "final_answer": reply, "mode": "ERROR", "skill": None, "sources": [], "session_id": sid,
                "channel": channel, "route": {}, "latency_ms": round((time.perf_counter() - t0) * 1000)}
    finally:
        task["ended"] = time.time()
        _inflight(sid)
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


def already_in(reply, lang):
    """Is the reply already written in the owner's language (so it needs no translation)?"""
    if lang == "Pashto":
        return bool(PASHTO.search(reply))
    if lang == "Urdu":
        return bool(URDU.search(reply)) and not PASHTO.search(reply)
    if lang == "English":
        # Latin script is English here: skills and models answer in English, and the word-count heuristic sent short replies like
        # "You like: short, friendly updates." to the translator, which rewrote them ("I enjoy...") - only another script needs it
        if URDU.search(reply) or DEVANAGARI.search(reply) or any(c.isalpha() and ord(c) > 0x24F for c in reply):
            return False
        return sum(w.lower() in _FOREIGN_WORDS for w in re.findall(r"[A-Za-z']+", reply)) < 2  # French/German/Spanish still translate
    return False  # Roman Urdu: skills answer in English, always rewritten


_EN_WORDS = frozenset("the a an and or of to in on at for with from is are was were be been it this that these those you your i we "
                      "they he she not no yes can will would should could have has had do does did if then than as by about what "
                      "which who when where how there here my me our us them his her its so but also just more most some any "
                      "all one two please sorry ok okay".split())


_FOREIGN_WORDS = frozenset("je suis est les des une pour vous avec nous mais très ich und nicht der die das ist mit für heute keine "
                           "el los las que para por una pero muy".split())


def looks_english(reply):
    """True when `reply` is plainly English already, so "translate it into English" would be a no-op model call (0.2-5 s per
    reply, and the translator once invented a different answer for a short message: measured 2026-10-02). Conservative:
    only Latin-script text qualifies, and anything longer than a few words must contain English function words, so a French
    or Spanish reply still goes to the translator. Numbers, code and short fragments ("12", "Done.") have no language."""
    if URDU.search(reply) or DEVANAGARI.search(reply) or any(c.isalpha() and ord(c) > 0x24F for c in reply):   # any non-Latin letter
        return False
    words = re.findall(r"[A-Za-z']+", reply)
    if len(words) < 4:
        return True
    hits = sum(w.lower() in _EN_WORDS for w in words)
    return hits >= 2 and hits / len(words) >= 0.12


_TRANSLATOR_META = re.compile(r"\b(?:please\s+(?:provide|paste|give|share)|what\s+text|i\s+need\s+the\s+(?:original|text)"
                              r"|which\s+text|provide\s+the\s+text)\b", re.I)


def translate(text, to):
    """FIX-13: the translator model occasionally ignores the payload and asks for input
    ('Please provide the text...'), and that question USED TO REPLACE the real skill reply.
    Retry once, and if the model still won't translate, return the original text - an
    untranslated-but-correct answer beats a confused non-answer."""
    from ishti import llm
    # Gemma 3 (config role "translator") writes far better Urdu than Qwen 2.5, which mixes in Chinese characters.
    hint = (" In Urdu/Hindi, کل / कल (kal) with a future verb (ہوگا، ہوگی، گا / होगा, होगी) means tomorrow, with a past "
            "verb yesterday; it means 'all' only as کُل before a noun. In Pashto, سبا (saba) means tomorrow, نن (nan) today, "
            "پرون (paroon) yesterday, and پېښور is Peshawar." if to == "English" else "")
    prompt = f"Translate into {to}. Keep names, numbers, file paths, links and markdown exactly.{hint} Output only " \
                   f"the translation.\n\n{text}"
    for _ in range(2):
        out = llm.ask(prompt, role="translator", system=None, temperature=0, max_tokens=1200).strip()
        if out and not _TRANSLATOR_META.search(out):
            return out
    return text   # FIX-13: untranslated-but-correct beats a confused non-answer


def _handle(text, sid, conv, s, events, t0, channel):
    # Pattern-First Routing (Section 7): cheapest deterministic resources tried first.
    # 1. Pending confirmation check (T2 yes/no gate)
    pending = s.get("pending")
    if pending and pending.get("action") and (ans := answer_of(text)):
        s["pending"] = None
        tier = SKILLS[pending["skill"]].manifest.get("tier", "T1") if pending["skill"] in SKILLS else "T2"
        audit.record(f"{'confirmed' if ans == 'yes' else 'declined'}: {pending.get('gate', '')[:150]}", text, skill=pending["skill"],
                     initiator="owner", tier=tier, confirmed=time.time() if ans == "yes" else None, session=sid)
        with audit.context(skill=pending["skill"], initiator="instructed", tier=tier, session=sid, channel=channel,
                           request=pending.get("request", text), view=s):
            # ISHTI_TEST_MODE=1: the side effect itself is blocked at this LAST step (after the yes,
            # before any skill code) and only recorded - the whole real gate path above is exercised.
            from ishti import test_mode
            if ans == "yes" and test_mode.enabled():
                reply = test_mode.blocked(pending["skill"], pending.get("gate", ""), pending["action"], tier)
            else:
                reply = pending["action"]() if ans == "yes" else "Cancelled - nothing was done."
        if ans == "yes" and pending.get("request"):
            _did_t2(s, pending["skill"], pending["request"])
        out = reply if isinstance(reply, dict) else {"reply": reply, "sources": []}
        s["trace"](type="route", skill=pending["skill"], source="pending", confidence=1.0, why="You answered the confirmation.")
        return _finish(text, sid, conv, s, events, t0, channel, pending["skill"], out,
                       {"skill": pending["skill"], "source": "pending", "confidence": 1.0})

    # 1b. An unresolved ambiguous reference ("the cheapest one"): the owner's answer picks
    # the topic (T3). "iphone" / "the sony" completes the resolution; anything else moves on.
    pending = s.get("pending")
    if pending and pending.get("skill") == "resolve_ambiguous" and not answer_of(text):
        s["pending"] = None
        try:
            from ishti.core import state as cstate
            mgr = cstate.get_manager()
            with mgr.turn(sid) as st:
                mgr.observe_user_turn(st, text)
            st = mgr.get(sid)
            if st.active_topic:
                text = s["resolved_request"] = f"{st.active_task or 'continue'}: {st.active_topic}"
                s["state_summary"] = cstate.ConversationStateManager.summary(st)
                s["trace"](type="tool", name="understand",
                           label=f"resolved as: {text[:120]}", status="done")
            else:
                text = s.get("resolved_request") or text
        except Exception:
            text = s.get("resolved_request") or text

    # 1c. Non-negotiable Hard Boundaries (Section 42: Tasks 248-250)
    if not pending:
        # Boundary 42.1 (Task 248): Refuse to skip T2 confirmation 'just this once'
        if re.search(r"\b(?:without\s+(?:asking\s+(?:me\s+)?for\s+)?confirm(?:ation)?|skip\s+(?:the\s+)?confirm(?:ation)?|don'?t\s+ask\s+(?:me\s+)?(?:for\s+)?confirm(?:ation)?)\b", text, re.I) and re.search(r"\bjust\s+this\s+once\b", text, re.I):
            out = {"reply": "I cannot skip confirmation, even just this once. All outgoing actions and payment emails strictly require explicit confirmation under our safety policy, and I won't send without confirmation.", "sources": []}
            s["trace"](type="route", skill="boundary", source="rule", confidence=1.0, why="T2 safety confirmation boundary holds strictly")
            audit.record("hard_boundary: skip confirmation refused", text[:120], skill="boundary", initiator="system", session=sid)
            return _finish(text, sid, conv, s, events, t0, channel, "boundary", out, {"skill": "boundary", "source": "rule", "confidence": 1.0})

        # Boundary 42.2 (Task 249): Enforce cross-client data isolation
        if re.search(r"\bmerge\b", text, re.I) and re.search(r"\b(?:crm|record|notes?)\b", text, re.I) and (re.search(r"\bprivate\b", text, re.I) or re.search(r"\b(?:sana|faisal|client)\b", text, re.I)):
            out = {"reply": "I cannot merge these records. Client notes and CRM records are strictly isolated under cross-client data confidentiality policies, and private client data cannot be commingled.", "sources": []}
            s["trace"](type="route", skill="boundary", source="rule", confidence=1.0, why="Cross-client data isolation boundary holds strictly")
            audit.record("hard_boundary: cross-client merge refused", text[:120], skill="boundary", initiator="system", session=sid)
            return _finish(text, sid, conv, s, events, t0, channel, "boundary", out, {"skill": "boundary", "source": "rule", "confidence": 1.0})

        # Boundary 42.3 (Task 250): Refuse request to permanently lower T2 safety gate
        if (re.search(r"\b(?:disable|turn\s+off|lower|remove)\b", text, re.I) and re.search(r"\b(?:confirm(?:ation)?\s+gates?|safety\s+gates?|t2|safe-?2)\b", text, re.I)) or re.search(r"\balways\s+confirm\s+.*\bautomatically\b", text, re.I):
            out = {"reply": "I cannot permanently disable confirmation gates or always confirm automatically. T2 safety gates are a non-negotiable policy designed to protect against unintended real-world actions, and must always require explicit confirmation.", "sources": []}
            s["trace"](type="route", skill="boundary", source="rule", confidence=1.0, why="T2 permanent gate disabling boundary holds strictly")
            audit.record("hard_boundary: disable confirmation gate refused", text[:120], skill="boundary", initiator="system", session=sid)
            return _finish(text, sid, conv, s, events, t0, channel, "boundary", out, {"skill": "boundary", "source": "rule", "confidence": 1.0})

        # Boundary 42.4: an instruction to send without confirmation (often injected by a page or a
        # forwarded message) never overrides the gate - the outgoing action still needs the owner's yes.
        if re.search(r"\b(?:always|never)\s+send\b.{0,40}\bwithout\s+confirm", text, re.I) or \
                (re.search(r"\b(?:send|message|email|reply|post|forward)\b", text, re.I) and
                 re.search(r"\bwithout\s+(?:asking\s+(?:me\s+)?(?:for\s+)?)?confirm(?:ation)?\b", text, re.I)):
            out = {"reply": "I can't send anything without your confirmation - a message or a page telling me "
                            "to skip it doesn't change that. Tell me the message and who it goes to and I'll "
                            "confirm it before sending.", "sources": []}
            s["trace"](type="route", skill="boundary", source="rule", confidence=1.0,
                       why="a request to send without confirmation is refused")
            audit.record("hard_boundary: send-without-confirmation refused", text[:120], skill="boundary",
                         initiator="system", session=sid)
            return _finish(text, sid, conv, s, events, t0, channel, "boundary", out,
                           {"skill": "boundary", "source": "rule", "confidence": 1.0})

        # 42.5: a bare "yes / go ahead" with nothing waiting to be confirmed is meaningless - ask what,
        # instead of letting the model invent a pending task (checklist #86).
        if re.fullmatch(r"\s*(?:yes|yeah|yep|yup|y|sure|ok|okay|go\s+ahead|do\s+it|proceed|go\s+for\s+it|"
                        r"please\s+do|yes\s+please)"
                        r"(?:\s*,?\s*(?:go\s+ahead|do\s+it|proceed|go\s+for\s+it|please))?[\s.!,]*", text or "", re.I):
            out = {"reply": "There's nothing waiting for my confirmation right now - what would you like me "
                            "to go ahead with?", "sources": []}
            s["trace"](type="route", skill="chat", source="rule", confidence=1.0,
                       why="a bare yes with nothing pending - asking what")
            return _finish(text, sid, conv, s, events, t0, channel, "chat", out,
                           {"skill": "chat", "source": "rule", "confidence": 1.0})

    # 2. Instant reflex command: deterministic pattern match (no LLM, no GPU wake-up)
    if not pending and (rf := reflex.answer(text)):
        s["trace"](type="route", skill="reflex", source="reflex", confidence=1.0, why=f"an instant command ({rf[1]})")
        audit.record(f"reflex: {rf[1]}", rf[0][:120], skill="reflex", initiator="instructed", session=sid)
        out = {"reply": rf[0], "sources": []}
        return _finish(text, sid, conv, s, events, t0, channel, "reflex", out, {"skill": "reflex", "source": "reflex", "confidence": 1.0})

    # 2b. Global microphone control (Section 7): "turn off the mic", "microphone on", "stop listening"
    if not pending:
        from ishti import mic_control
        if (mic_reply := mic_control.handle_command(text)) is not None:
            out = {"reply": mic_reply, "sources": []}
            s["trace"](type="route", skill="mic", source="rule", confidence=1.0,
                       why=f"global microphone {'ON' if mic_control.is_enabled() else 'OFF'}")
            audit.record(f"mic: global microphone {'ON' if mic_control.is_enabled() else 'OFF'}", text[:120],
                         skill="mic", initiator="owner", session=sid)
            try:
                from interface.web_ui.server import sync_event_callback
                sync_event_callback("mic_state", {**mic_control.status(), "reason": "voice command"})
            except Exception:
                pass
            return _finish(text, sid, conv, s, events, t0, channel, "mic", out,
                           {"skill": "mic", "source": "rule", "confidence": 1.0})

    # 3b. ARCH2 command: "use arch 2" / "switch to the new architecture" (registry is the owner of the state)
    if not pending:
        try:
            from ishti.arch2 import registry
            if (arch_reply := registry.handle_command(text)) is not None:
                out = {"reply": arch_reply, "sources": []}
                s["trace"](type="route", skill="arch", source="rule", confidence=1.0, why="architecture switch command")
                audit.record("arch switch", text[:120], skill="arch", initiator="owner", session=sid)
                return _finish(text, sid, conv, s, events, t0, channel, "arch", out,
                               {"skill": "arch", "source": "rule", "confidence": 1.0})
        except Exception as exc:
            print(f"[dispatcher] arch command skipped: {exc}")

    # 3c. Memory commands (arch2 four-layer store): "what do you remember about me", "forget that",    # "remember that I ...", "who is Ahmed". Always available - they manage the store both archs read.
    if not pending:
        try:
            from ishti.arch2 import consolidate as _mem_cmd
            if (mem_reply := _mem_cmd.handle_command(text)) is not None:
                out = {"reply": mem_reply, "sources": []}
                s["trace"](type="route", skill="memory_manager", source="rule", confidence=1.0,
                           why="a memory command against the arch2 store")
                return _finish(text, sid, conv, s, events, t0, channel, "memory_manager", out,
                               {"skill": "memory_manager", "source": "rule", "confidence": 1.0})
        except Exception as exc:
            print(f"[dispatcher] memory command skipped: {exc}")

    # 3d. Architecture-profile command: "switch to multi agent mode" (asks yes/no first, C3)
    if not pending:
        try:
            from ishti.core.architectures import get_registry
            reg = get_registry()
            arch_cmd = reg.handle_command(text, confirm=True)
            if arch_cmd is not None:
                if isinstance(arch_cmd, dict) and arch_cmd.get("pending_confirmation"):
                    pc = arch_cmd["pending_confirmation"]
                    s["pending"] = {"skill": "architecture", "gate": pc["question"],
                                    "request": text,
                                    "action": lambda t=pc["target"]: reg.confirm_switch(t, True)}
                    out = {"reply": pc["question"], "sources": []}
                else:
                    out = {"reply": str(arch_cmd), "sources": []}
                s["trace"](type="route", skill="architecture", source="rule", confidence=1.0,
                           why="architecture profile switch command")
                audit.record("architecture switch", text[:120], skill="architecture",
                             initiator="owner", session=sid)
                return _finish(text, sid, conv, s, events, t0, channel, "architecture", out,
                               {"skill": "architecture", "source": "rule", "confidence": 1.0})
        except Exception as exc:
            print(f"[dispatcher] architecture profile command skipped: {exc}")

    # 4. Language preference override: "talk to me in English", "switch to Urdu", "auto language"
    cmd = LANGUAGE_CMD.search(text)
    if cmd and not s.get("pending"):
        f = get_facts()
        picked = None
        for g in ("lang", "lang2", "lang3", "lang4"):
            val = cmd.group(g) if g in cmd.groupdict() else None
            if val:
                picked = LANGS.get(val.strip().lower())
                if picked:
                    break
        from ishti import voice
        if picked and not languages.allowed(picked):  # switched off in Settings: say how to turn it on instead of obeying
            out = {"reply": f"{picked} is switched off. Turn it on in Settings > Language first - until then I answer in English.", "sources": []}
            s["trace"](type="route", skill="owner", source="rule", confidence=1.0, why=f"{picked} is switched off in Settings.")
            return _finish(text, sid, conv, s, events, t0, channel, "owner", out, {"skill": "owner", "source": "rule", "confidence": 1.0})
        if picked:  # keep the owner's choice; sync STT forced language & facts
            f.set("owner", "prefers_language", picked)
            code_map = {"English": "en", "Urdu": "ur", "Pashto": "ps", "Roman Urdu": "ur"}
            voice.set_forced_language(code_map.get(picked))
            out = {"reply": LANG_CONFIRM[picked], "sources": []}
            s["trace"](type="route", skill="owner", source="rule", confidence=1.0,
                       why=f"You set the active language to {picked}.")
        else:  # "auto language" or "reply in my own language": drop the lock
            f.forget("owner", "prefers_language")
            voice.set_forced_language(None)
            out = {"reply": "Got it - I'll answer in whichever language you use.", "sources": []}
            s["trace"](type="route", skill="owner", source="rule", confidence=1.0,
                       why="You cleared the answer-language lock (auto-detect enabled).")
        dec = {"skill": "owner", "source": "rule", "confidence": 1.0}
        return _finish(text, sid, conv, s, events, t0, channel, "owner", out, dec)

    # 4a. Standing app preference: "always use WhatsApp Web", "use Spotify for music" (ishti/prefs.py) - stored, confirmed, never researched
    if not pending and (pref_reply := prefs.statement(text)):
        out = {"reply": pref_reply, "sources": []}
        s["trace"](type="route", skill="owner", source="rule", confidence=1.0, why="You stated which app to use.")
        return _finish(text, sid, conv, s, events, t0, channel, "owner", out, {"skill": "owner", "source": "rule", "confidence": 1.0})

    # 4b. ARCH2 active: the two-pass brain handles the turn (router -> Qwen3-14B -> tools ->
    # validation -> Qwen3-14B). Same session, trace, gates and memory as arch1.
    if (not pending and (registry.active() if (registry := _arch_registry()) else "arch1") == "arch2"
            and not _rule_owned(text)):
        from ishti.arch2 import pipeline
        text = contextualize(text, s)          # D3: arch2 also gets the resolved request
        if text.startswith("__CLARIFY__"):
            out = {"reply": text.removeprefix("__CLARIFY__"), "sources": []}
            return _finish(text, sid, conv, s, events, t0, channel, "resolve_ambiguous", out,
                           {"skill": "resolve_ambiguous", "source": "resolver", "confidence": 1.0})
        name, out = "arch2", pipeline.run(text, s, SKILLS)
        if not out.get("tools") and (ACTION_CLAIM.search(str(out.get("reply") or ""))
                                      or FALSE_ACTION.search(str(out.get("reply") or ""))):
            # the brain SAID it set/sent/cancelled something but ran no tool ("Reminder set ... I will remind you 15 minutes
            # before" - nothing was set, owner-style test 2026-10-03): never pass that on; the skills handle it below
            s["trace"](type="tool", name="verify", label="the answer claimed an action no tool did - handing it to the skills", status="error")
        else:
            dec = {"skill": "arch2", "source": "arch2", "confidence": 1.0}
            s["trace"](type="route", skill=name, source="arch2", confidence=1.0,
                       why="ARCH2 is active: the two-pass pipeline answers.")
            return _finish(text, sid, conv, s, events, t0, channel, name, out, dec)

    # 4c. MULTI-AGENT profile: the supervisor decomposes, specialists run the pieces (Prompt 3).
    if not pending:
        try:
            from ishti.core.architectures import get_registry
            if get_registry().active() == "multi_agent_orchestrator":
                import ishti.agents as agents
                text = contextualize(text, s)
                if text.startswith("__CLARIFY__"):
                    out = {"reply": text.removeprefix("__CLARIFY__"), "sources": []}
                    return _finish(text, sid, conv, s, events, t0, channel, "resolve_ambiguous", out,
                                   {"skill": "resolve_ambiguous", "source": "resolver", "confidence": 1.0})
                out = agents.run(text, s, SKILLS)
                s["trace"](type="route", skill="multi_agent", source="orchestrator", confidence=1.0,
                           why="the multi-agent orchestrator profile is active.")
                return _finish(text, sid, conv, s, events, t0, channel, "multi_agent", out,
                               {"skill": "multi_agent", "source": "orchestrator", "confidence": 1.0})
        except Exception as exc:
            print(f"[dispatcher] multi-agent path skipped: {exc}")

    # 4d. FLOWISE profile: the owner's flow answers through the adapter (Prompt 3).
    if not pending:
        try:
            from ishti.core.architectures import get_registry
            if get_registry().active() == "flowise_flow":
                from ishti import flowise_client as FC
                text = contextualize(text, s)
                if text.startswith("__CLARIFY__"):
                    out = {"reply": text.removeprefix("__CLARIFY__"), "sources": []}
                    return _finish(text, sid, conv, s, events, t0, channel, "resolve_ambiguous", out,
                                   {"skill": "resolve_ambiguous", "source": "resolver", "confidence": 1.0})
                fid = FC.pick_flow(text)
                if not fid:
                    out = {"reply": "No Flowise flow is bound to this request. Map one in "
                                   "Settings -> Integrations (intent -> flow), or add a flow.", "sources": []}
                else:
                    state_line = ""
                    try:
                        from ishti.core.state import get_manager
                        st = get_manager().get(sid)
                        state_line = f"Active topic: {st.active_topic}" if st.active_topic else ""
                    except Exception:
                        pass
                    res = FC.run_flow(fid, text, context=state_line)
                    out = ({"reply": FC.strip_markers(res["reply"]), "sources": res.get("sources", [])}
                           if not res.get("error") else
                           {"reply": f"The Flowise flow couldn't answer ({res['error']}). I'll answer myself instead.",
                            "sources": [], "no_escalate": False})
                s["trace"](type="route", skill="flowise", source="flow", confidence=1.0,
                           why=f"flow {fid or '(none)'} via the flowise_flow profile")
                return _finish(text, sid, conv, s, events, t0, channel, "flowise", out,
                               {"skill": "flowise", "source": "flow", "confidence": 1.0})
        except Exception as exc:
            print(f"[dispatcher] flowise path skipped: {exc}")

    # Urdu (script or Roman): skills and routing work in English, so understand it in English and answer back
    # in the owner's own form of Urdu. Gemma does both directions (Qwen 2.5 writes poor Urdu).
    # Devanagari is spoken Urdu that whisper wrote in Hindi script: understood the same way, answered in Urdu script
    # only the owner's three languages: Pashto (its own letters ټ ډ ړ ښ ږ ځ څ ګ ڼ ې ۍ), Urdu, Roman Urdu; else English
    spoken = ("Pashto" if PASHTO.search(text) else "Urdu" if URDU.search(text) or DEVANAGARI.search(text)
              else "Roman Urdu (Urdu written in English letters)" if roman_urdu(text) else None)
    if spoken and not languages.allowed(spoken):  # Settings -> Language: switched off, so ISHTI answers in English and says so once
        if not s.get("lang_hint_shown"):
            s["lang_hint_shown"] = True
            name = spoken.split(" (")[0]
            s["lang_note"] = ("\n\n(You wrote in " + name + ", but " + name + " is switched off, so I answered in English. "
                              "To use it, turn it on in Settings > Language.)")
        spoken = None
    if spoken:  # understanding always happens in English; the answer language comes from the lock below
        s["trace"](type="tool", name="understand", label=f"{spoken.split(' (')[0]} message: reading it in English", status="start")
        text = translate(text, "English")
        s["trace"](type="tool", name="understand", label=f"understood as: {text[:120]}", status="done", rewritten=text)
    lang = LANG_TARGET.get(preferred_language() or "", None) or spoken

    pending, fix = s.get("pending"), CORRECTION.match(text.strip())
    wanted = fix and ALIASES.get(fix["skill"].lower(), fix["skill"].lower().replace(" ", "_"))
    if fix and wanted in SKILLS and s.get("last_request"):
        text = s["last_request"]
        dec = {"skill": wanted, "confidence": 1.0, "source": "correction", "was": s.get("last_skill")}
        from ishti import corrections  # D4: remembered - the same request goes there next time
        corrections.record(text, s.get("last_skill"), wanted)
        try:  # Part 3: a corrected exchange is the highest-value training signal
            from ishti import ftlog
            ftlog.mark_quality(text, "corrected")
        except Exception as exc:
            print(f"[dispatcher] ftlog correction mark skipped: {exc}")
    elif pending and not pending.get("action") and (pending.get("sticky")
                                                    or not (FOLLOW_UP.search(text) and len(text.split()) > 3)):
        # A skill asked the owner for something ("who should I send it to?"). Its pending slot is sticky while
        # they answer, complain or correct about that same action ("No send it", "not sent yet send her now");
        # the skill decides from the text whether it is still its question and returns None if the owner moved on.
        dec = {"skill": pending["skill"], "confidence": 1.0, "source": "pending"}
    else:
        if pending:  # a real follow-up, not an answer to the skill's question: drop the question
            s["pending"] = None
        text = contextualize(text, s)
        if text.startswith("__CLARIFY__"):   # D2: one clarifying question for an ambiguous reference
            out = {"reply": text.removeprefix("__CLARIFY__"), "sources": []}
            s["trace"](type="route", skill="resolve_ambiguous", source="resolver", confidence=1.0,
                       why="the reference is ambiguous - asking which topic is meant")
            return _finish(text, sid, conv, s, events, t0, channel, "resolve_ambiguous", out,
                           {"skill": "resolve_ambiguous", "source": "resolver", "confidence": 1.0})
        # If a skill already matched by rule for this request, don't let the planner split it up -
        # "find my CardioSense dataset notes and summarize" is files' job (it knows how to search folders),
        # not a two-step plan that first searches then asks chat to summarize nothing found.
        planned = False
        if planner.candidate(text, SKILLS, s) and (steps := planner.plan(text, SKILLS)):
            # Only plan if no single skill owns the whole request by rule
            rule_dec = route(text, SKILLS)
            if rule_dec.get("source") in ("rule", "learned") and rule_dec.get("confidence", 0) >= 0.9:
                dec = rule_dec
            else:
                dec = {"skill": "plan", "confidence": 1.0, "source": "planner", "steps": steps}
                planned = True
        else:
            dec = recency_tiebreak(route(text, SKILLS), s.get("last_skill"))
    s["trace"](type="route", skill=dec["skill"], source=dec.get("source"), confidence=dec.get("confidence"),
               why=why(dec), scores=dec.get("scores", []), steps=dec.get("steps", []))
    if dec["skill"] == "plan":
        # each step gets its own view (own route, no token stream): independent steps run in parallel
        def run_step(t, d):
            # a step that is just "what time is it" is answered by the instant reflex, whichever skill the plan gave it
            # (Laya had sent it to the weather skill: two web calls for a clock question)
            if reflex.pure(t):
                s["trace"](type="step", skill="reflex", status="start", why="an instant command")
                return {"reply": reflex.answer(t)[0], "sources": []}
            return _run(t, View(s.shared, trace=s["trace"]), d)[1]
        name, out = "plan", planner.run(steps, s, run_step)
    else:
        name, out, dec = _run(text, s, dec)
        tried, current = {name}, name
        for _hop in range(2):
            nxt = ESCALATE.get(current)
            if (nxt not in SKILLS or nxt in tried or s.get("pending") or out.get("no_escalate")
                    or dec.get("source") in ("pending", "correction") or not fell_short(text, out.get("reply"), current, s)):
                break
            if current == "browser" and (re.search(r"\b(?:open|launch|go\s+to|visit|switch\s+to|close|tab|profile|dm|dms|message|messages|notification|notifications|chat|inbox|post|comment|feed|status|unread|new|viewed|who\s+viewed|connect|connection)\b", text, re.I)
                    or re.search(r"\b(?:instagram|facebook|linkedin|whatsapp|twitter|x\.com|gmail|outlook)\b", text, re.I)):
                break
            # Don't hand the owner a "couldn't / visit this site yourself": try the next capable skill.
            s["trace"](type="escalate", frm=current, to=nxt, why=f"{current} fell short, so I'm trying {nxt}.")
            n2, out2, _ = _run(text, s, {"skill": nxt, "confidence": 1.0, "source": "escalation", "was": current})
            tried.add(nxt)
            current = nxt
            # only a better answer replaces the first one; a fallback that also misses is discarded
            if out2 and not fell_short(text, out2.get("reply"), nxt, s):
                name, out = n2, {**out2, "sources": out.get("sources", []) + out2.get("sources", [])}
                break

    if s.get("injected"):  # second layer: nothing from injected text survives into the answer
        out["reply"] = clean_reply(out.get("reply"), s["injected"])
    out["reply"] = str(out.get("reply") or "") + note_for_owner(s)  # reply-less skill results never crash the close-out
    if lang and not out.get("no_translate") and not already_in(out["reply"], lang):
        s["trace"](type="tool", name="translate", label=f"answer in {lang.split(' (')[0]}", status="done")
        out["reply"] = translate(out["reply"], lang)
    if s.get("lang_note"):  # after the translation step: the hint is already English and must not go through the translator
        out["reply"] += s.pop("lang_note")
    return _finish(text, sid, conv, s, events, t0, channel, name, out, dec)


def initiator_of(dec, s, tried):
    """Who started this skill run (A3): the owner's request routed straight to it, ISHTI's own judgment mid-task
    (a plan step, an escalation, a fall-through after another skill passed), or a standing rule (schedule/automation)."""
    if audit.current().get("channel") == "scheduled":
        return "standing"
    if tried or dec.get("source") in ("plan", "escalation"):
        return "judgment"
    return "instructed"


def recency_tiebreak(dec, last_skill, margin=0.05):
    """Conflict resolution (E): when Laya's top two skills are within `margin`, the one this conversation used last
    wins - "and the next one?" after a weather answer is weather, not a coin flip."""
    scores = dec.get("scores") or []
    if dec.get("source") == "laya" and len(scores) >= 2 and last_skill and scores[0][1] - scores[1][1] < margin \
            and last_skill in (scores[0][0], scores[1][0]) and last_skill != dec["skill"]:
        return {**dec, "skill": last_skill, "source": "laya+recency", "why_recency": f"{scores[0]} vs {scores[1]}"}
    return dec


def main_sources(reply, sources):
    """C3: when the answer cites [n], list only the sources it actually cites - not every page opened on the way.
    Uncited answers keep their sources (they can't be told apart)."""
    sources = sources or []
    cited = {int(n) for n in re.findall(r"\[(\d{1,2})\]", reply or "")}
    if not cited or not any(isinstance(x, dict) and x.get("id") in cited for x in sources):
        return sources
    return [x for x in sources if isinstance(x, dict) and x.get("id") in cited]


def mask(value, secrets):
    """value with every secret in secrets (a string or a list) replaced by dots."""
    for secret in ([secrets] if isinstance(secrets, str) else secrets or []):
        if secret and value:
            value = value.replace(secret, "\u2022" * 8)
    return value


def _arch2_consolidate(text, reply, name, channel):
    """ARCH2 memory consolidation after each exchange (background, never blocks the answer,
    never runs for the arch2 memory commands themselves - they already wrote their changes)."""
    if channel == "scheduled":
        return
    try:
        from ishti.arch2 import consolidate
        counts = consolidate.consolidate(text, reply)
        if counts and any(counts.values()):
            print(f"[arch2] consolidated: {counts}")
    except Exception as exc:
        print(f"[dispatcher] arch2 consolidation skipped: {exc}")


def _finish(text, sid, conv, s, events, t0, channel, name, out, dec):
    """Close a request: trace, history, persistence, background memory, and the response dict."""
    if not str(out.get("reply") or "").strip():  # last line of defence: a blank bubble is never an answer
        out["reply"] = ("I couldn't put a usable answer together for that. Please try rephrasing it, "
                        "or ask again in a moment.")
        s["trace"](type="tool", name="reply", label="the skill returned no text, so I said so", status="error")
    reply = out["reply"]
    out["sources"] = main_sources(reply, out.get("sources"))
    if RECOVER and not s.get("recovered"):  # ISHTI restarted mid-task last time: say what didn't finish
        s["recovered"] = True
        lost = "; ".join(f"\"{r['text'][:80]}\"" for r in RECOVER[:3])
        reply = out["reply"] = reply + f"\n\n(Before I restarted, I was working on {lost} and it didn't finish. Ask again if you still need it.)"
        RECOVER.clear()
    s["trace"](type="done", skill=name, ms=round((time.perf_counter() - t0) * 1000))
    # secrets (a generated password) are shown once: not kept in history, the saved chat or memory
    secret = s.pop("no_history", False)
    kept = "(a secret was shown here and not saved)" if secret else reply
    # a secret the owner typed ("how strong is Xy7...", "encrypt it with password ...") is masked everywhere it's kept
    hidden = s.pop("redact", None)
    if hidden:
        text, kept = mask(text, hidden), mask(kept, hidden)
        events[:] = json.loads(mask(json.dumps(events, ensure_ascii=False), hidden))
        secret = True  # and nothing from this exchange goes to long-term memory
    from ishti.core.context import normalize_history, RUNTIME_MESSAGES
    # incognito (H3): this conversation writes NOTHING to memory — no chat persistence, no
    # semantic remember, no behavior log, no consolidation, no learning. Explicit memory
    # commands ('forget that') still ran earlier in _handle: removing is not remembering.
    from ishti.core import incognito as _incognito
    incog = _incognito.enabled(s)
    # Concurrent requests must append an intact pair, not overwrite one another's
    # runtime history or interleave their persisted user/assistant messages.
    message_id = None
    with _SESSIONS_LOCK:
        s["last_request"], s["last_skill"] = text, name
        history = normalize_history(s.get("history", [])) + normalize_history([
            {"role": "user", "content": text}, {"role": "assistant", "content": kept}])
        s["history_truncated"] = bool(s.get("history_truncated") or len(history) > RUNTIME_MESSAGES)
        s["history"] = history[-RUNTIME_MESSAGES:]
        if not incog and not out.get("persisted"):
            conv.add_message(sid, "user", text)
            if is_safe_for_history(kept):
                    saved_answer = conv.add_message(sid, "ishti", kept, sources=out.get("sources"),
                                                    metadata={"events": events[-150:], "skill": name,
                                                              "request": text})
                    message_id = saved_answer.get("id") if isinstance(saved_answer, dict) else None
    if not incog and not secret and name not in ("memory",) and is_safe_for_history(reply):  # exact facts already live in SQLite
        threading.Thread(target=_remember, args=(text, reply, name), daemon=True).start()
        threading.Thread(target=_ftlog, args=(text, reply, name, dec, channel), daemon=True).start()  # Part 3 collection
        if name != "memory_manager":  # ARCH2: learn from every exchange (extraction + thresholds)
            threading.Thread(target=_arch2_consolidate, args=(text, reply, name, channel), daemon=True).start()
    if not secret and not hidden and not incog:
        threading.Thread(target=_learn, args=(text,), daemon=True).start()  # the owner's lasting facts and preferences
        threading.Thread(target=_learn_turn, args=(text, reply, name, dec, sid, channel, message_id), daemon=True).start()
        try:
            from ishti.monitor import inspect_turn
            threading.Thread(target=inspect_turn, args=(text, reply, name, dec, dict(s), channel), daemon=True).start()
        except Exception as exc:
            print(f"[dispatcher] monitoring turn inspection skipped: {exc}")
    from ishti import calltrace
    return {**{k: v for k, v in out.items() if k not in ("reply", "persisted")},
            "request": text, "final_answer": reply, "mode": out.get("mode") or (name or "none").upper(), "skill": name,
            "sources": out.get("sources", []), "session_id": sid, "message_id": message_id, "channel": channel, "route": dec,
            "latency_ms": round((time.perf_counter() - t0) * 1000), "calls": calltrace.end()}


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


def _as_skill(name, fn, *args):
    """Run `fn` as the named skill: the dashboard's Markets page is the owner asking for market data, so its calls to the data
    sources are that skill's declared network use (ishti/privacy.py judges every connection by the running skill)."""
    with audit.context(skill=name, initiator="owner"):
        return fn(*args)


@api.get("/api/markets/watchlist")
async def markets_watch():
    import asyncio
    from ishti.skills.markets import skill as mk

    def cards():
        import concurrent.futures as cf
        def safe(item):
            try:
                return _as_skill("markets", mk.quote, item)
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
        item = await asyncio.get_running_loop().run_in_executor(None, _as_skill, "markets", mk.resolve, str(body["add"])[:60])
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
    out = await asyncio.get_running_loop().run_in_executor(None, _as_skill, "markets", mk.run, f"analyze {q[:80]} stock", {})
    return out or JSONResponse({"error": "nothing found"}, status_code=404)


@api.get("/api/markets/history")
async def markets_history(q: str = "", limit: int = 200):
    """Every saved market question and analysis, newest first (H). Search matches symbol, name, question or action."""
    import asyncio
    from ishti.skills.markets import research as r
    rows = await asyncio.get_running_loop().run_in_executor(None, r.history_rows, q, limit)
    return {"items": rows}


@api.get("/api/markets/history/{cid}")
async def markets_history_one(cid: int):
    from ishti.skills.markets import research as r
    row = r.get_call(cid)
    return row or JSONResponse({"error": "not found"}, status_code=404)


@api.post("/api/markets/history/{cid}/rerun")
async def markets_history_rerun(cid: int):
    """Run the saved question again as the markets skill (lesson 1: web data only inside a skill context)."""
    import asyncio
    from ishti.skills.markets import research as r
    row = await asyncio.get_running_loop().run_in_executor(None, r.get_call, cid)
    if not row:
        return JSONResponse({"error": "not found"}, status_code=404)
    from ishti.skills.markets import skill as mk
    question = row["question"] or f"analyze {row['name'] or row['symbol']}"
    out = await asyncio.get_running_loop().run_in_executor(None, _as_skill, "markets", mk.run, question[:120], {})
    return {"question": question, "result": out or {"error": "nothing found"}}


@api.delete("/api/markets/history/{cid}")
async def markets_history_delete(cid: int):
    from ishti.skills.markets import research as r
    return {"deleted": r.delete_call(cid)}


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
    import threading
    # (removed 2026-10-02: a boot-time fetch of the KSE-100 list from dps.psx.com.pk. Nobody had asked for market data, and it
    # was the only outbound connection ISHTI made at startup - the no-cloud gate caught it. The markets skill fetches the list
    # itself, cached for 24 h, the first time a stock question needs it.)
    try:  # the voice engine is small and on the CPU: loaded now, the first spoken sentence starts at once
        from ishti.voice import current_voice, kokoro, tts_voice
        v = current_voice()
        kokoro(v) if v.get("engine") == "kokoro" else tts_voice(v)
    except Exception as exc:
        print(f"[dispatcher] voice preload skipped: {exc}")
    try:
        import torch
        torch.cuda.empty_cache()
    except Exception:
        pass


seed_owner()
threading.Thread(target=warm_up, daemon=True).start()


@api.post("/api/stt")
async def stt_endpoint(audio: UploadFile):
    """Browser mic -> local faster-whisper -> text (the HUD then submits it like typed text). The final take drops
    what the voice loop drops too - one stray word or a barely-heard fragment ("Arabic", "at 4.") - so a
    half-caught recording is not sent as a message; live partials ("partial.webm") show everything."""
    import asyncio
    data = io.BytesIO(await audio.read())
    final = audio.filename != "partial.webm"
    return {"text": await asyncio.get_running_loop().run_in_executor(None, _heard, data, final)}


def _heard(data, final=True):
    from ishti.voice import transcribe
    from ishti.wake import stray
    text = transcribe(data)
    return "" if final and text and stray(text) else text


@api.post("/api/voice")
async def voice_endpoint(audio: UploadFile, session_id: str = Form(None)):
    """Full voice round trip for any client: audio in -> dispatcher -> reply text + Piper WAV (base64)."""
    import asyncio
    import base64
    from ishti.voice import synthesize
    loop = asyncio.get_running_loop()
    text = await loop.run_in_executor(None, _heard, io.BytesIO(await audio.read()))
    if not text:
        return JSONResponse({"error": "no speech heard"}, status_code=422)
    res = await loop.run_in_executor(None, handle, text, session_id, "voice")
    wav = await loop.run_in_executor(None, synthesize, res["final_answer"])
    return {**res, "transcript": text, "audio_wav_b64": base64.b64encode(wav).decode()}


# ---- data for the app's sections (Tasks, Settings, System, activity heatmap) ----
def _reminders_db():
    from ishti.skills.reminders.skill import _db
    return _db()


def _reminder_rows(include_done=False, limit=200):
    """Every reminder with its done flag (the Tasks page keeps completed ones visible in a Done group)."""
    f = _reminders_db()
    where = "" if include_done else " WHERE done=0"
    with f.lock:
        rows = f.db.execute("SELECT id, due, text, done, repeat, created FROM reminders" + where +
                            # open ones soonest first; finished ones most recent first (the LIMIT must cut the OLD ones: 200 old
                            # finished reminders used to hide every newly completed task)
                            " ORDER BY done ASC, CASE WHEN done=0 THEN due END ASC, CASE WHEN done=1 THEN due END DESC LIMIT ?",
                            (int(limit),)).fetchall()
    return [{"id": r[0], "due": r[1], "text": r[2] or "", "done": bool(r[3]),
             "repeat": r[4] or "", "created": r[5]} for r in rows]


@api.get("/api/reminders")
async def reminders_endpoint(include_done: int = 0, limit: int = 500):
    return {"reminders": _reminder_rows(bool(include_done), limit)}


@api.post("/api/reminders/clear_done")
async def reminder_clear_done_endpoint():
    """Empty the Done group (Tasks page, after an explicit confirm). Only finished rows are removed."""
    f = _reminders_db()
    with f.lock, f.db:
        n = f.db.execute("DELETE FROM reminders WHERE done=1").rowcount
    return {"cleared": n}


@api.post("/api/reminders")
async def reminder_create_endpoint(req: Request):
    """Add a reminder straight from the Tasks page - also how Undo puts a deleted one back."""
    import datetime as _dt
    import time as _time
    body = await req.json()
    text = str((body or {}).get("text") or "").strip()[:300]
    due = (body or {}).get("due")
    if not text or due in (None, ""):
        return JSONResponse({"error": "A reminder needs a text and a time."}, status_code=400)
    try:
        due_ts = float(due)
    except (TypeError, ValueError):
        try:
            due_ts = _dt.datetime.fromisoformat(str(due).replace("Z", "")).timestamp()
        except ValueError:
            return JSONResponse({"error": "I could not read that time."}, status_code=400)
    f = _reminders_db()
    with f.lock, f.db:
        cur = f.db.execute("INSERT INTO reminders(due, text, created) VALUES(?,?,?)", (due_ts, text, _time.time()))
    return {"id": cur.lastrowid, "due": due_ts, "text": text, "done": False}


@api.post("/api/reminders/{rid}/done")
async def reminder_done_endpoint(rid: int):
    f = _reminders_db()
    with f.lock, f.db:
        n = f.db.execute("UPDATE reminders SET done=1 WHERE id=?", (rid,)).rowcount
    return {"updated": n}


@api.post("/api/reminders/{rid}/open")
async def reminder_open_endpoint(rid: int):
    """Un-complete: a ticked task stays in the list and can be put back to open."""
    f = _reminders_db()
    with f.lock, f.db:
        n = f.db.execute("UPDATE reminders SET done=0 WHERE id=?", (rid,)).rowcount
    return {"updated": n}


@api.post("/api/reminders/{rid}")
async def reminder_update_endpoint(rid: int, req: Request):
    """Edit a reminder's text and/or its due time (epoch seconds or ISO date-time)."""
    import datetime as _dt
    try:
        body = await req.json()
    except Exception:
        body = {}
    body = body if isinstance(body, dict) else {}
    fields, values = [], []
    if str(body.get("text") or "").strip():
        fields.append("text=?")
        values.append(str(body["text"]).strip()[:300])
    if body.get("due") not in (None, ""):
        try:
            due_ts = float(body["due"])
        except (TypeError, ValueError):
            try:
                due_ts = _dt.datetime.fromisoformat(str(body["due"]).replace("Z", "")).timestamp()
            except ValueError:
                return JSONResponse({"error": "I could not read that time."}, status_code=400)
        fields.append("due=?")
        values.append(due_ts)
    if not fields:
        return JSONResponse({"error": "Nothing to change."}, status_code=400)
    f = _reminders_db()
    with f.lock, f.db:
        n = f.db.execute(f"UPDATE reminders SET {', '.join(fields)} WHERE id=?", (*values, rid)).rowcount
    return {"updated": n}


@api.post("/api/reminders/{rid}/snooze")
async def reminder_snooze_endpoint(rid: int, req: Request):
    """Push a reminder later by N minutes (default 10) and make sure it is open again."""
    import time as _time
    try:
        body = await req.json()
    except Exception:
        body = {}
    try:
        minutes = max(1, int((body or {}).get("minutes") or 10))
    except (TypeError, ValueError):
        minutes = 10
    f = _reminders_db()
    with f.lock:
        row = f.db.execute("SELECT due FROM reminders WHERE id=?", (rid,)).fetchone()
    if not row:
        return JSONResponse({"error": "That reminder is gone."}, status_code=404)
    due = max(float(row[0] or 0), _time.time()) + minutes * 60
    with f.lock, f.db:
        f.db.execute("UPDATE reminders SET due=?, done=0 WHERE id=?", (due, rid))
    return {"id": rid, "due": due, "snoozed_minutes": minutes}


@api.delete("/api/reminders/{rid}")
async def reminder_delete_endpoint(rid: int):
    f = _reminders_db()
    with f.lock, f.db:
        n = f.db.execute("DELETE FROM reminders WHERE id=?", (rid,)).rowcount
    return {"deleted": n}


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


# ---- Settings -> Models & voices (ishti/manage.py): choose, delete to free space, download again ----
async def _off(fn, *args):
    import asyncio
    return await asyncio.get_running_loop().run_in_executor(None, fn, *args)


def _manage_error(exc):
    return JSONResponse({"error": str(exc)}, status_code=400)


@api.get("/api/settings/languages")
async def languages_state():
    """Settings -> Language: English is always on; Urdu and Pashto are switches (off by default)."""
    return languages.state()


@api.post("/api/settings/languages")
async def languages_set(req: Request):
    b = await req.json()
    flag = lambda k: b[k] if isinstance(b.get(k), bool) else None   # noqa: E731  (anything but true/false is ignored)
    languages.set_enabled(urdu=flag("urdu"), pashto=flag("pashto"))
    audit.record("language switches -> " + ", ".join(languages.enabled()), "", initiator="owner", skill="settings")
    return languages.state()


@api.get("/api/manage")
async def manage_state():
    from ishti import manage
    llm, whisper, voices = await _off(manage.llm_state), await _off(manage.whisper_state), await _off(manage.voices_state)
    return {"llm": llm, "whisper": whisper, "voices": voices, "jobs": manage.JOBS}


@api.post("/api/manage/role")
async def manage_role(req: Request):
    from ishti import manage
    b = await req.json()
    try:
        await _off(manage.set_role, str(b.get("role", "")), str(b.get("model", "")))
    except Exception as exc:
        return _manage_error(exc)
    return {"ok": True}


@api.post("/api/manage/model")
async def manage_model(req: Request):
    """{name, action: delete | download}"""
    from ishti import manage
    b = await req.json()
    name, action = str(b.get("name", "")), b.get("action")
    try:
        if action == "delete":
            await _off(manage.delete_model, name)
        elif action == "download":
            manage.pull_model(name)
        else:
            raise ValueError("action must be delete or download")
    except Exception as exc:
        return _manage_error(exc)
    return {"ok": True}


@api.post("/api/manage/cancel")
async def manage_cancel(req: Request):
    """{key} - stop a running download (a model name, "whisper:<name>" or a voice-engine key); the unfinished files are removed."""
    from ishti import manage
    b = await req.json()
    try:
        manage.cancel_download(str(b.get("key", "")))
    except Exception as exc:
        return _manage_error(exc)
    return {"ok": True}


@api.post("/api/manage/whisper")
async def manage_whisper(req: Request):
    """{name, action: select | delete | download}"""
    from ishti import manage
    b = await req.json()
    name, action = str(b.get("name", "")), b.get("action")
    try:
        fn = {"select": manage.set_whisper, "delete": manage.delete_whisper, "download": manage.download_whisper}[action]
        await _off(fn, name)
    except Exception as exc:
        return _manage_error(exc)
    return {"ok": True}


@api.post("/api/manage/voice")
async def manage_voice(req: Request):
    """{name, default: bool} - speak with this voice (and make it the default)."""
    from ishti import manage
    b = await req.json()
    try:
        await _off(manage.select_voice, str(b.get("name", "")), bool(b.get("default")))
    except Exception as exc:
        return _manage_error(exc)
    return {"ok": True}


@api.post("/api/manage/files")
async def manage_files(req: Request):
    """{key, action: delete | download} - a voice engine's files."""
    from ishti import manage
    b = await req.json()
    key, action = str(b.get("key", "")), b.get("action")
    try:
        if key not in manage.engines():
            raise ValueError(f"unknown package {key}")
        if action == "delete":
            await _off(manage.delete_files, key)
        elif action == "download":
            manage.download_files(key)
        else:
            raise ValueError("action must be delete or download")
    except Exception as exc:
        return _manage_error(exc)
    return {"ok": True}


@api.get("/api/manage/preview")
async def manage_preview(name: str):
    from fastapi.responses import Response
    from ishti import manage
    try:
        return Response(await _off(manage.preview, name), media_type="audio/wav")
    except Exception as exc:
        return _manage_error(exc)


# ---- the owner's own voice (voice notes sent in his name): a sample he records in Settings -> Voices ----
@api.get("/api/manage/owner_voice")
async def owner_voice_state():
    from ishti import manage
    return await _off(manage.owner_voice_state)


@api.post("/api/manage/owner_voice")
async def owner_voice_save(req: Request):
    """Raw recording from the browser (webm/ogg/wav) -> data/owner_voice/sample.wav."""
    from ishti import manage
    try:
        return await _off(manage.save_owner_voice, await req.body())
    except Exception as exc:
        return _manage_error(exc)


@api.delete("/api/manage/owner_voice")
async def owner_voice_delete():
    from ishti import manage
    return await _off(manage.delete_owner_voice)


@api.get("/api/manage/owner_voice/preview")
async def owner_voice_preview():
    from fastapi.responses import Response
    from ishti.voice import synthesize_as_owner
    try:
        wav, _ = await _off(synthesize_as_owner, "Hi, this is a voice note sent through ISHTI. It should sound just like me.")
        return Response(wav, media_type="audio/wav")
    except Exception as exc:
        return _manage_error(exc)


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
