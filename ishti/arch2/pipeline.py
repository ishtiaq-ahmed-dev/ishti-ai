"""ARCH2 pipeline: the user's two-pass architecture.

    request -> intent router (rules + Laya, reused - already excellent)
            -> Qwen3-14B pass 1: THINK / PLAN / call a tool
            -> tool execution (arch1's real skills run it, same scopes/T2 gates/audit)
            -> validation pass: did the tool actually do what was asked?
            -> Qwen3-14B pass 2: final answer, grounded in tool evidence + memory
            -> memory consolidation (consolidate.py, background thread in dispatcher)

Spec's routing insight implemented: the model plans best with a SHORTLIST of 5-20
relevant tools, not all ~50 (long tool lists dilute choice quality). The shortlist
comes from Laya's own scores for this request (it already ranks every skill), plus
the always-on core (chat, memory, web_search).

Safety rule (spec 24): memory NEVER authorizes an action. High-impact verbs go through
ask_first's yes/no gate exactly like arch1; the skills' configured T2 tiers are the
only authority.
"""
import json
import re

from ishti.skills import trace
from ishti.arch2 import memory, retrieval
from ishti.arch2.jsonio import parse_json

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


def _tool_manifests(skills: dict, request: str) -> str:
    """The shortlist: Laya's scores for this request (via the router) rank the usable skills;
    top SHORTLIST_MAX win. Cheap (Laya is 0.2 ms) and exactly the spec's 5-20 window."""
    usable = {n: s for n, s in sorted(skills.items()) if s.status != "broken"}
    if len(usable) <= SHORTLIST_MAX:
        ranked = list(usable)
    else:
        try:
            from ishti.router import route as _route
            scores = {d.get("skill"): c for d in
                      [_route(request, skills)] if d.get("skill")}
            # _route returns one pick; use Laya's full score list when it is present
            dec = _route(request, skills)
            ranked = [n for n, _ in (dec.get("scores") or [])] if dec.get("scores") else \
                ([dec["skill"]] if dec.get("skill") else [])
        except Exception:
            ranked = []
        # always-available core first, then Laya's ranking, then manifest order
        core = [n for n in ("chat", "memory", "web_search") if n in usable]
        ranked = list(dict.fromkeys(core + [n for n in ranked if n in usable] + list(usable)))
        ranked = ranked[:SHORTLIST_MAX]
    lines = [f"- {n}: {usable[n].manifest.get('description', '')[:110]}" for n in ranked]
    return "\n".join(lines)


def _needs_confirmation(text: str) -> bool:
    """Spec 24: high-impact verbs always confirm, no matter what memory 'knows'."""
    return bool(CONFIRM_RE.search(text or ""))


def _run_tool(skills, name, text, session, tool_log):
    try:
        return skills[name].run(text, session) or {"reply": f"The {name} skill had nothing to do for this."}
    except Exception as exc:  # same containment as the dispatcher: the turn carries on
        return {"reply": f"The {name} skill failed: {type(exc).__name__}: {str(exc)[:140]}"}


def run(request: str, session, skills: dict) -> dict:
    """The two-pass turn. session is the dispatcher's View (trace, history, pending...).
    Returns {"reply", "sources"} like any skill."""
    from ishti import llm  # openai import lives here; arch2 must load without it

    context = retrieval.assemble_context(request)
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

        # validation pass (the architecture's middle layer) - JSON-constrained, degrades open
        trace(session, "verify", "checking the tool really did what was asked", status="start")
        v = parse_json(llm.ask(VALIDATE_PROMPT.format(request=request[:400], tool=name, result=result_text),
                               role="validation", system=None, temperature=0, max_tokens=120,
                               response_format={"type": "json_object"}))
        if v is None:  # model/JSON failed: trust the tool's own output rather than blocking
            v = {"ok": bool(result_text.strip()), "missing": ""}
        trace(session, "verify", "tool output verified" if v.get("ok") else f"gaps: {str(v.get('missing'))[:80]}",
              status="done" if v.get("ok") else "error")

        tool_log.append({"tool": name, "input": tool_input, "result": result_text, "ok": bool(v.get("ok"))})
        if v.get("ok") or round_no == MAX_TOOL_ROUNDS - 1:
            break
        # the tool fell short: show the model what is missing and let it try once more
        msgs += [{"role": "assistant", "content": reply},
                 {"role": "user", "content": f"Tool {name} returned: {result_text}\nThis did NOT fully "
                                             f"accomplish the request ({v.get('missing')}). Call a tool again "
                                             f"differently, or answer with what you have."}]

    # pass 2: the final answer, grounded in the tool results
    trace(session, "model", f"writing the final answer with {llm.model_for('main')} (pass 2)", status="start")
    evidence = "\n\n".join(f"Tool {t['tool']} ({'verified' if t['ok'] else 'unverified'}) returned:\n{t['result']}"
                           for t in tool_log) or "(no tools were needed)"
    final = (llm.chat([{"role": "user", "content": f"User request: {request}\n\n{evidence}\n\nWrite the final answer."}],
                      role="main", system=FINAL_SYSTEM, temperature=0.3, max_tokens=1024,
                      on_token=session.get("on_token")).content or "").strip()
    return {"reply": final or "I could not produce an answer - try rephrasing.", "sources": sources, "mode": "ARCH2",
            "tools": tool_log}


def _parse_tool_call(text: str):
    """Extract {"tool", "input"} from a model reply; None when the reply is a plain answer.
    Uses jsonio's defensive parser (fences, chatter, balanced braces)."""
    d = parse_json(text)
    if isinstance(d, dict) and isinstance(d.get("tool"), str) and d["tool"].strip():
        return {"tool": d["tool"].strip(), "input": str(d.get("input") or "")}
    return None


def ranked_names(skills: dict, request: str) -> list:
    """The shortlist as names (used in the unknown-tool recovery message)."""
    return [line[2:line.index(":")] for line in
            _tool_manifests(skills, request).splitlines() if line.startswith("- ")]


if __name__ == "__main__":
    # offline parts only (LLM passes need Ollama - see MAC_CHANGES.md)
    assert _parse_tool_call('{"tool": "web_search", "input": "weather in Peshawar"}') == \
        {"tool": "web_search", "input": "weather in Peshawar"}
    assert _parse_tool_call('```json\n{"tool": "browser", "input": "open gmail"}\n```') == \
        {"tool": "browser", "input": "open gmail"}
    assert _parse_tool_call('The weather in Peshawar is sunny.') is None
    assert _parse_tool_call('{"tool": null, "input": "x"}') is None
    assert _parse_tool_call('chatter {"tool": "chat", "input": "hi"} trailing') == \
        {"tool": "chat", "input": "hi"}
    assert _needs_confirmation("send the report to Ahmed")
    assert not _needs_confirmation("what is the capital of Pakistan")
    print("[self-check] arch2.pipeline OK - tool-call parse (fences/chatter), confirmation triggers")
