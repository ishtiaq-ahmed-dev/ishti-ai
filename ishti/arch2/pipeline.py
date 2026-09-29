"""ARCH2 pipeline - the spec's two-pass architecture, stage by stage.

    YOU -> STT (arch1's wake/voice, unchanged) -> Intent Router (embeddings)
        -> 5-20 skills shortlist -> Qwen3-14B (THINK / PLAN / CALL TOOLS / CONVERSE)
        -> Browser | PC | Email tools (arch1's real skills, same T2 gates/scopes/audit)
        -> Validation layer -> Qwen3-14B final response -> Kokoro TTS -> YOU

The Intent Router stage: deterministic rules first, then Laya's typed-decision scores
(0.2 ms on CPU) rank every usable skill for THIS request; the top SHORTLIST_MAX are the
"5-20 skills" the brain sees. The brain's tool calls are parsed with jsonio's hardened
JSON parser and every tool run is judged by the validation pass (JSON-constrained, with
an explicit safe default when the model/JSON fails).

Safety rule (spec 24): memory NEVER authorizes an action. High-impact verbs always go
through ask_first's yes/no gate, exactly like arch1; the skills' configured T2 tiers
are the only authority.
"""
import re

from ishti.arch2 import memory, retrieval
from ishti.arch2.jsonio import parse_json
from ishti.skills import trace

SHORTLIST_MIN, SHORTLIST_MAX = 5, 20  # the spec's 5-20 window

SYSTEM_PASS1 = """You are ISHTI's reasoning core. You think, plan, call tools and converse.

Tools available for THIS request (call at most one per turn, by exact name):
{tools}

Use a tool whenever the request needs the PC, the web, files, messages or any real action.
Answer directly (no tool) for conversation, explanations and anything the context already
covers.

Reply with ONLY one of:
- {{"tool": "<skill name>", "input": "<the exact request for that skill>"}}
- the final conversational answer itself (plain text)

Context you may use (the known facts are exact and override anything else):
{context}"""

VALIDATE_PROMPT = """The user asked: {request}
The assistant called the tool "{tool}" and the tool returned:
{result}

Did the tool's output actually accomplish the request (the action ran, or the real
information was retrieved)? Reply ONLY JSON:
{{"ok": true/false, "missing": "<what is missing or wrong, empty if none>"}}"""

FINAL_SYSTEM = """You are ISHTI. Write the final answer to the owner.
Rules: clean readable formatting; bullet points for lists; a Markdown table for comparisons
or numbers; no unrequested code; direct and concise; reply in English, Urdu or Pashto -
whichever the owner used.
Tool results and the context below are your evidence - never invent what they do not show."""

CONFIRM_RE = re.compile(r"\b(?:send|message|reply|delete|remove|erase|pay|buy|order|purchase|post|publish|"
                        r"install|uninstall|kill|shut\s*down|restart|transfer|forward|empty)\b", re.I)
MAX_TOOL_ROUNDS = 3
_CORE_TOOLS = ("chat", "memory", "web_search")  # always on the shortlist


def _usable(skills: dict) -> dict:
    return {n: s for n, s in sorted(skills.items()) if s.status != "broken"}


def _laya_scores(request: str, usable: dict) -> dict:
    """Laya's per-skill scores for this request (arch1's router - already paid for, 0.2 ms).
    {skill: confidence} or {} when Laya is unavailable."""
    try:
        from ishti.router import route as _route
        dec = _route(request, usable)
        scores = {n: c for n, c in (dec.get("scores") or [])}
        if dec.get("skill") and dec["skill"] not in scores:
            scores[dec["skill"]] = max(scores.values(), default=1.0)
        return scores
    except Exception:
        return {}


def shortlist(skills: dict, request: str) -> list:
    """The '5-20 skills' stage: core tools + Laya's ranking for this request, capped.
    Returns skill NAMES in the order they should be shown to the brain."""
    usable = _usable(skills)
    names = list(usable)
    if len(names) > SHORTLIST_MAX:
        scores = _laya_scores(request, usable)
        head = [n for n, _ in sorted(scores.items(), key=lambda kv: -kv[1]) if n in usable]
        core = [n for n in _CORE_TOOLS if n in usable]
        names = list(dict.fromkeys(core + head + names))[:max(SHORTLIST_MIN, SHORTLIST_MAX)]
    return names


def _tool_manifests(skills: dict, request: str) -> str:
    usable = _usable(skills)
    return "\n".join(f"- {n}: {usable[n].manifest.get('description', '')[:110]}"
                     for n in shortlist(skills, request))


def ranked_names(skills: dict, request: str) -> list:
    """The shortlist as names (used in the unknown-tool recovery message)."""
    return shortlist(skills, request)


def _needs_confirmation(text: str) -> bool:
    """Spec 24: high-impact verbs always confirm, no matter what memory 'knows'."""
    return bool(CONFIRM_RE.search(text or ""))


def _run_tool(skills, name, text, session, tool_log):
    try:
        return skills[name].run(text, session) or {"reply": f"The {name} skill had nothing to do for this."}
    except Exception as exc:  # same containment as the dispatcher: the turn carries on
        return {"reply": f"The {name} skill failed: {type(exc).__name__}: {str(exc)[:140]}"}


def _parse_tool_call(text: str):
    """Extract {"tool", "input"} from a model reply; None when the reply is a plain answer.
    jsonio's parser survives fences, chatter and trailing text."""
    d = parse_json(text)
    if isinstance(d, dict) and isinstance(d.get("tool"), str) and d["tool"].strip():
        return {"tool": d["tool"].strip(), "input": str(d.get("input") or "")}
    return None


def run(request: str, session, skills: dict) -> dict:
    """The two-pass turn. session is the dispatcher's View (trace, history, pending...).
    Returns {"reply", "sources"} like any skill."""
    from ishti import llm  # openai import lives here; arch2 must load without it

    context = retrieval.assemble_context(request)
    # D3: the conversation-state summary (active topic / task / constraints) is pinned
    # into the context block - the brain never sees only the raw latest message.
    state_summary = session.get("state_summary") or ""
    if state_summary:
        context = f"CONVERSATION STATE:\n{state_summary}\n\n{context}"
    tool_block = _tool_manifests(skills, request)
    system1 = SYSTEM_PASS1.format(tools=tool_block, context=context or "(none)")
    msgs = session.get("history", [])[-8:] + [{"role": "user", "content": request}]

    tool_log, sources = [], []
    for round_no in range(MAX_TOOL_ROUNDS):
        trace(session, "model", f"thinking with {llm.model_for('main')} (pass 1)", status="start")
        reply = (llm.chat(msgs, role="main", system=system1, temperature=0.2, max_tokens=1024,
                          on_token=session.get("on_token")).content or "").strip()
        call = _parse_tool_call(reply)
        if not call:
            trace(session, "model", "answered directly", status="done")
            return {"reply": reply, "sources": sources, "mode": "ARCH2"}
        name, tool_input = call["tool"], call["input"]
        if name not in skills or skills[name].status == "broken":
            trace(session, "tool", f"model asked for unknown tool '{name}'", status="error")
            msgs += [{"role": "assistant", "content": reply},
                     {"role": "user", "content": f"'{name}' is not an available tool. Available: "
                                                 f"{', '.join(ranked_names(skills, request))[:600]}. "
                                                 "Call one of them or answer directly."}]
            continue
        trace(session, "tool", f"{name}: {tool_input[:80]}", status="start")

        # spec 24: high-impact action -> the standard T2 gate, memory never authorizes
        if _needs_confirmation(tool_input) and skills[name].manifest.get("tier", "T1") == "T2" \
                and session.get("route", {}).get("source") != "pending":
            from ishti.skills import ask_first
            out = ask_first(session, name, f"Run {name} for: {tool_input[:120]}",
                            lambda n=name, t=tool_input: _run_tool(skills, n, t, session, tool_log))
            trace(session, "tool", "waiting for the owner's yes", status="done")
            return {**out, "sources": sources, "mode": "ARCH2"}

        result = _run_tool(skills, name, tool_input, session, tool_log)
        sources += result.get("sources", []) if isinstance(result, dict) else []
        result_text = str(result.get("reply", result))[:1500] if isinstance(result, dict) else str(result)[:1500]
        trace(session, "tool", f"{name} done: {result_text[:80]}", status="done")

        # validation layer (JSON-constrained; safe default = trust the tool when JSON fails)
        trace(session, "verify", "checking the tool really did what was asked", status="start")
        v = parse_json(llm.ask(VALIDATE_PROMPT.format(request=request[:400], tool=name, result=result_text),
                               role="validation", system=None, temperature=0, max_tokens=120,
                               response_format={"type": "json_object"}))
        if v is None:
            v = {"ok": bool(result_text.strip()), "missing": ""}
        trace(session, "verify", "tool output verified" if v.get("ok") else f"gaps: {str(v.get('missing'))[:80]}",
              status="done" if v.get("ok") else "error")

        tool_log.append({"tool": name, "input": tool_input, "result": result_text, "ok": bool(v.get("ok"))})
        if v.get("ok") or round_no == MAX_TOOL_ROUNDS - 1:
            break
        # the tool fell short: show the model what is missing and let it try again
        msgs += [{"role": "assistant", "content": reply},
                 {"role": "user", "content": f"Tool {name} returned: {result_text}\nThis did NOT fully "
                                             f"accomplish the request ({v.get('missing')}). Call a tool again "
                                             f"differently, or answer with what you have."}]

    # pass 2: the final response, grounded in the validated tool evidence
    trace(session, "model", f"writing the final answer with {llm.model_for('main')} (pass 2)", status="start")
    evidence = "\n\n".join(f"Tool {t['tool']} ({'verified' if t['ok'] else 'unverified'}) returned:\n{t['result']}"
                           for t in tool_log) or "(no tools were needed)"
    state_line = session.get("state_summary") or ""
    user_block = f"User request: {request}" + (f"\n\nConversation context (the topic is fixed - do not drift):\n{state_line}" if state_line else "")
    final = (llm.chat([{"role": "user", "content": f"{user_block}\n\n{evidence}\n\nWrite the final answer."}],
                      role="main", system=FINAL_SYSTEM, temperature=0.3, max_tokens=1024,
                      on_token=session.get("on_token")).content or "").strip()

    # D5: is the answer about the resolved topic? A deterministic floor (no model) keeps a
    # drifting answer from ever reaching the owner.
    try:
        from ishti.core.tools import ValidationStage
        from ishti.core import state as cstate
        st = cstate.get_manager().get(session.get("id", "default"))
        verdict = ValidationStage().validate_answer(request, final, topic=st.active_topic)
        if not verdict.get("ok"):
            trace(session, "verify", f"answer was off-topic ({verdict.get('reason')}) - re-planning", status="error")
            final = (llm.chat([{"role": "user", "content":
                                f"{user_block}\n\n{evidence}\n\nYour previous draft ignored the topic. "
                                f"Write the final answer ABOUT: {st.active_topic}."}],
                              role="main", system=FINAL_SYSTEM, temperature=0.2, max_tokens=1024).content or final).strip()
    except Exception:
        pass
    return {"reply": final or "I could not produce an answer - try rephrasing.", "sources": sources,
            "mode": "ARCH2", "tools": tool_log}


if __name__ == "__main__":
    # offline parts only (the LLM passes need Ollama - PC checklist in MAC_CHANGES.md)
    assert _parse_tool_call('{"tool": "web_search", "input": "weather in Peshawar"}') == \
        {"tool": "web_search", "input": "weather in Peshawar"}
    assert _parse_tool_call('```json\n{"tool": "browser", "input": "open gmail"}\n```') == \
        {"tool": "browser", "input": "open gmail"}
    assert _parse_tool_call('The weather in Peshawar is sunny.') is None
    assert _parse_tool_call('{"tool": null, "input": "x"}') is None
    assert _parse_tool_call('chatter {"tool": "chat", "input": "hi"} trailing') == \
        {"tool": "chat", "input": "hi"}
    assert _needs_confirmation("send the report to Ahmed")
    assert _needs_confirmation("delete that file")
    assert not _needs_confirmation("what is the capital of Pakistan")
    assert not _needs_confirmation("summarize this document for me")

    class _FakeSkill:  # shortlist ordering with >SHORTLIST_MAX skills
        def __init__(self, desc):
            self.manifest = {"description": desc}
            self.status = "working"

    many = {f"skill{i}": _FakeSkill(f"tool {i}") for i in range(30)}
    many.update({"chat": _FakeSkill("conversation"), "memory": _FakeSkill("facts"),
                 "web_search": _FakeSkill("search"), "browser": _FakeSkill("browse"),
                 "broken_one": _FakeSkill("broken")})
    many["broken_one"].status = "broken"
    sl = shortlist(many, "search the web for weather")
    assert 5 <= len(sl) <= SHORTLIST_MAX, sl
    assert "chat" in sl and "memory" in sl and "web_search" in sl, "core tools always listed"
    assert "broken_one" not in sl and "browser" in sl, "broken excluded, laya-picked included"
    print("[self-check] arch2.pipeline OK - parse (fences/chatter), confirmation, shortlist window")
