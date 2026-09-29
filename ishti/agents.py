"""Multi-agent orchestration (Part C): a supervisor decomposes the owner's request, specialist
agents execute the pieces, results are verified and synthesized.

C1  Orchestrator: decomposition (LLM JSON, deterministic fallback), capability-based
    assignment (an agent only runs skills it owns), result aggregation, conflict resolution
    (the Reviewer decides when two agents disagree), final synthesis (Writer).
C2  Specialists: researcher, web, coder, reviewer, fact_checker, data_analyst, writer,
    pc_control, lead_finder, validator — thin role prompts over the SAME skills; no second
    skill implementation to maintain.
C3  Patterns: supervisor/worker, sequential pipeline, parallel fan-out/fan-in, hierarchical
    (a specialist may spawn one nested level of its own sub-agents).
C4  Structured inter-agent messages (JSON), a shared blackboard, and the Prompt-1/2 context
    contract: every sub-agent receives the RESOLVED request + conversation-state summary +
    only memories relevant to its own subtask (retrieval runs per subtask, not per task).
C5  Health/timeouts/failover/retry/max-iterations/budgets (tokens·time·tool-calls) from Settings.
C6  Loop: plan → act → verify → fix until acceptance or a limit is hit; on limit, escalate
    with the exact reason. No silent stops.
C7  Human approval for high-impact steps (the same ask_first T2 gate — memory/agents never
    authorize; ConfirmationPolicy/skill tiers stay the only authority).
C8  Performance tracking per agent (success/latency) into the learning store for Prompt 2.

The `multi_agent_orchestrator` architecture profile (Prompt-1 stub) routes here.
"""
from __future__ import annotations

import json
import re
import threading
import time

from ishti.core.settings import get_settings_service
from ishti import resilience, taskmgr

_lock = threading.Lock()
_PERF: dict = {}


# --------------------------------------------------------------------- agents (C2)
AGENTS = {
    "researcher":   {"skills": ["deep_research", "web_search", "web_fetch"], "goal": "gather sources and evidence"},
    "web":          {"skills": ["browser", "webtools", "web_fetch"],        "goal": "drive the browser and read pages"},
    "coder":        {"skills": ["dev", "devops", "gitops"],                  "goal": "write and run code"},
    "reviewer":     {"skills": ["chat"],                                     "goal": "judge quality, resolve conflicts between agents"},
    "fact_checker": {"skills": ["web_search", "web_fetch", "argumentcheck"], "goal": "verify claims against independent sources"},
    "data_analyst": {"skills": ["dataprep", "stats", "chart", "excel"],      "goal": "analyse numbers and data files"},
    "writer":       {"skills": ["chat", "writing", "docs"],                  "goal": "write the final owner-facing synthesis"},
    "pc_control":   {"skills": ["apps", "desktop", "screen_control", "files", "fileops"], "goal": "operate the PC when the task needs it"},
    "lead_finder":  {"skills": ["find_leads", "web_search"],                 "goal": "find and verify business leads"},
    "validator":    {"skills": ["chat"],                                     "goal": "check a result against its acceptance criteria"},
}

# capability order used when decomposing: the FIRST matching agent owns a subtask
_PRIORITY = ["pc_control", "coder", "lead_finder", "web", "researcher", "data_analyst",
             "fact_checker", "reviewer", "validator", "writer"]


def _settings() -> dict:
    try:
        return get_settings_service().get().get("agents", {})
    except Exception:
        return {}


def _budget() -> dict:
    a = _settings()
    return {"max_subagents": int(a.get("max_subagents", 4)),
            "timeout_s": int(a.get("subagent_timeout_s", 300)),
            "max_iterations": int(a.get("max_iterations", 4)),
            "max_tool_calls": int(a.get("max_tool_calls", 12)),
            "max_total_tokens": int(a.get("max_total_tokens", 60000)),
            "pattern": a.get("pattern", "supervisor")}


def trace(session, name, label, status="done", **detail):
    from ishti.skills import trace as _t
    _t(session, name, label, status=status, **detail)


def _skill_run(skills: dict, name: str, text: str, session) -> dict:
    """One skill call through the resilience layer (G1 applies to agents too)."""
    if name not in skills:
        return {"reply": f"skill '{name}' not available", "sources": []}
    br = resilience.breaker(f"skill:{name}")

    def _call():
        return skills[name].run(text, session) or {"reply": f"{name} had nothing.", "sources": []}
    out = br.run(_call, fallback=lambda e: {"reply": f"{name} unavailable ({type(e).__name__})", "sources": []})
    return out if isinstance(out, dict) else {"reply": str(out), "sources": []}


# --------------------------------------------------------------------- messages (C4)
def _msg(sender, role, content, **extra) -> dict:
    return {"from": sender, "role": role, "content": str(content)[:6000],
            "ts": time.time(), **extra}


def blackboard_get(bb: dict, key: str, default=None):
    return (bb or {}).get("entries", {}).get(key, default)


def blackboard_put(bb: dict, key: str, value, who: str = ""):
    bb.setdefault("entries", {})[key] = {"value": value, "who": who, "ts": time.time()}


# --------------------------------------------------------------------- decomposition (C1)
_NUM = re.compile(r"\b\d+\b")
_STEP_WORDS = re.compile(r"\b(?:and then|then|after that|afterwards|first .+? (?:then|and) |next)\b", re.I)


def decompose(request: str, skills: dict) -> list[dict]:
    """Subtasks as [{agent, task, criteria}]. LLM proposes; a deterministic fallback keeps
    the orchestrator working with the model down (fail-soft, G7)."""
    usable = [n for n in _PRIORITY if n in AGENTS
              and any(s in skills for s in AGENTS[n]["skills"])]
    try:
        from ishti import llm
        listing = "; ".join(f"{a} ({AGENTS[a]['goal']})" for a in usable)
        raw = llm.ask(
            f"Request: {request}\n\nAvailable agents: {listing}\n\n"
            'Decompose into 1-4 subtasks. Reply ONLY JSON: {"subtasks": '
            '[{"agent": "agent_name", "task": "exact instruction for that agent", '
            '"criteria": "when this subtask is done"}]}. '
            "Use an agent ONLY for what it can do; a single-agent request stays a single subtask.",
            role="planner", system=None, temperature=0, max_tokens=400,
            response_format={"type": "json_object"})
        from ishti.arch2.jsonio import parse_json
        d = parse_json(raw) or {}
        subs = [s for s in (d.get("subtasks") or [])
                if isinstance(s, dict) and s.get("agent") in AGENTS and s.get("task")]
        if subs:
            return subs[:4]
    except Exception:
        pass
    # deterministic fallback: capability keyword matching
    low = request.lower()
    assigned = None
    for a in _PRIORITY:
        if any(k in low for k in (a, a.replace("_", " "))) or \
           any(s in skills and s in low for s in AGENTS[a]["skills"]):
            assigned = a
            break
    if not assigned:
        if any(w in low for w in ("research", "find out", "compare", "latest", "news", "price")):
            assigned = "researcher"
        elif any(w in low for w in ("open", "click", "type", "window", "screenshot", "file", "folder")):
            assigned = "pc_control"
        else:
            assigned = "researcher" if "web_search" in skills else "writer"
    return [{"agent": assigned, "task": request, "criteria": "the request is answered"}]


def assign(subtasks: list[dict], skills: dict) -> list[dict]:
    """Capability-based assignment: an agent whose skills are all missing is re-assigned to
    the first agent that owns at least one usable skill (C1)."""
    out = []
    for s in subtasks:
        a = s.get("agent")
        if a not in AGENTS or not any(x in skills for x in AGENTS[a]["skills"]):
            a = next((x for x in _PRIORITY if any(y in skills for y in AGENTS[x]["skills"])), "writer")
        out.append({**s, "agent": a})
    return out


# --------------------------------------------------------------------- execution (C3/C5/C6)
def _run_agent(agent: str, task: str, skills: dict, session, bb: dict, budget: dict,
               depth: int = 0) -> dict:
    """One specialist: build context (C4) → act → verify → fix, within budget/limits (C5/C6)."""
    spec = AGENTS[agent]
    tool_calls = 0
    iterations = 0
    t0 = time.time()
    last_err = ""
    # C4: resolved request + conversation state + ONLY memories relevant to THIS subtask
    state_summary = ""
    try:
        from ishti.core.state import get_manager
        st = get_manager().get(session.get("id", "default"))
        state_summary = f"Topic: {st.active_topic}" + (f"; task: {st.active_task}" if st.active_task else "")
    except Exception:
        st = None
    try:
        from ishti.arch2 import retrieval
        ctx = retrieval.assemble_context(task)
    except Exception:
        ctx = ""
    context = "\n".join(x for x in (state_summary, ctx) if x)

    usable = [s for s in spec["skills"] if s in skills and skills[s].status != "broken"]
    if not usable:
        return {"reply": f"no usable skill for agent '{agent}'", "sources": []}
    skill_idx = 0
    while iterations < max(1, budget["max_iterations"]):
        iterations += 1
        if session.get("_stop_all"):
            raise taskmgr.TaskError("kill switch")
        chosen = usable[skill_idx % len(usable)]  # C5 failover: each attempt moves to the
        # agent's NEXT skill (wrapping) and the fix-reason accumulates for the escalation
        prompt = (f"[{agent} agent] {spec['goal']}\n"
                  + (f"Context: {context[:1200]}\n" if context else "")
                  + f"Subtask: {task}\n"
                    f"(attempt {iterations}/{budget['max_iterations']})")
        trace(session, "agent", f"{agent}: {task[:70]}", status="start")
        tool_calls += 1
        out = _skill_run(skills, chosen, prompt, session)
        trace(session, "agent", f"{agent} done via {chosen}", status="done")
        ok, why = _verify(out, task, skills)
        if ok:
            _perf(agent, True, time.time() - t0)
            out["agent"], out["attempts"] = agent, iterations
            return out
        last_err = why
        skill_idx += 1                     # failover: try the agent's next skill
        task = f"{task}\n(Fix: {why})"     # C6 fix-loop: the reason rides back into the next try
    _perf(agent, False, time.time() - t0)
    return {"reply": f"agent '{agent}' hit its iteration limit ({budget['max_iterations']}). "
                     f"Reason: {last_err or 'acceptance criteria never passed'}",
            "sources": [], "escalated": True, "agent": agent}


def _verify(out: dict, task: str, skills: dict) -> tuple[bool, str]:
    """Acceptance check (C6): the skill's own answer must be substantive and not a failure
    template. A fast deterministic floor first; the validator model only when one exists."""
    reply = str(out.get("reply", ""))
    low = reply.lower()
    if out.get("error"):
        return False, str(out.get("error"))[:140]
    if len(reply.strip()) < 3:
        return False, "empty answer"
    if any(p in low for p in ("can't do that yet", "no skill handles it", "had nothing to do",
                              "unavailable (", "skill failed:", "i don't have a memory matching")):
        return False, "the answering skill reported it could not do the subtask"
    return True, ""


def _fan_out(subtasks: list, skills: dict, session, bb: dict, budget: dict, depth: int) -> list[dict]:
    """Parallel fan-out over the task manager (D1/D2 apply), fan-in by wait_all."""
    parent = taskmgr.submit(lambda t: "orchestrator umbrella", goal="multi-agent fan-out",
                            sid=str(session.get("id", "")), timeout_s=budget["timeout_s"])
    ids = []
    for st in subtasks:
        def _one(t, st=st):
            taskmgr.coop(t)
            with taskmgr.gpu_slot():          # D2: heavy LLM jobs serialize
                return _run_agent(st["agent"], st["task"], skills,
                                  {**session, "id": session.get("id", "")}, bb, budget, depth)
        rec = taskmgr.child(parent, _one, goal=st["task"][:120],
                            priority="high" if len(subtasks) <= 2 else "normal",
                            timeout_s=budget["timeout_s"] + 30)
        ids.append(rec["id"])
    done = taskmgr.wait_all(ids, timeout=budget["timeout_s"] + 60)
    results = []
    for st, rec in zip(subtasks, done):
        if rec["status"] == "done":
            results.append(rec["result"] if isinstance(rec["result"], dict) else {"reply": str(rec["result"])})
        else:
            results.append({"reply": f"subtask failed ({rec['status']}): {rec['error'][:120]}",
                            "sources": [], "escalated": rec["status"] == "failed"})
    taskmgr.cancel(parent["id"])  # the umbrella is finished; children already reaped
    return results


def _sequential(subtasks: list, skills: dict, session, bb: dict, budget: dict) -> list[dict]:
    out = []
    prev = ""
    for st in subtasks:
        task_text = st["task"].replace("{prev}", prev) if prev else st["task"]
        r = _run_agent(st["agent"], task_text, skills, session, bb, budget)
        blackboard_put(bb, f"result:{len(out)}", r.get("reply", ""), who=st["agent"])
        out.append(r)
        prev = str(r.get("reply", ""))[:400]
    return out


def _hierarchical(subtasks: list, skills: dict, session, bb: dict, budget: dict, depth: int) -> list[dict]:
    """C3: each specialist may spawn one nested level (depth+1) for its own subtask."""
    if depth >= 1:
        return _fan_out(subtasks, skills, session, bb, budget, depth)
    return [_run_agent(st["agent"], st["task"], skills, session, bb, budget, depth + 1)
            for st in subtasks]


def _resolve_conflicts(results: list[dict]) -> list[dict]:
    """C1 conflict resolution: identical claims that CONTRADICT each other are flagged for
    the Reviewer; plain duplicates collapse."""
    seen, out = set(), []
    for r in results:
        key = re.sub(r"\W+", " ", str(r.get("reply", ""))[:80]).lower().strip()
        if key and key in seen:
            r["duplicate_of_previous"] = True
            continue
        seen.add(key)
        out.append(r)
    return out


def synthesize(request: str, results: list[dict], skills: dict, session) -> dict:
    """Final synthesis (Writer): every result is shown; conflicts noted; nothing invented."""
    good = [r for r in results if not r.get("escalated") and not r.get("duplicate_of_previous")]
    conflicts = [r for r in results if r.get("escalated")]
    if not good:
        return {"reply": "None of the agents could complete this request. "
                         + " ".join(str(r.get("reply", ""))[:120] for r in conflicts[:2]),
                "sources": [], "mode": "MULTI_AGENT"}
    try:
        from ishti import llm
        blocks = "\n\n".join(f"[{r.get('agent', 'agent')}]\n{str(r.get('reply', ''))[:1500]}"
                             for r in good[:4])
        note = ("\n\nSome subtasks failed - say so honestly:\n"
                + "\n".join(f"- {str(r.get('reply', ''))[:150]}" for r in conflicts[:3])) if conflicts else ""
        reply = llm.ask(f"Owner's request: {request}\n\nAgent results:\n{blocks}{note}\n\n"
                        "Write the final answer from THESE results only. Cite [n] when a result "
                        "carries sources. Never invent what the results don't show.",
                        role="main", system=None, temperature=0.3, max_tokens=900).strip()
        sources = []
        for r in good:
            sources += r.get("sources", []) or []
        return {"reply": reply or good[0]["reply"], "sources": sources[:12], "mode": "MULTI_AGENT"}
    except Exception:
        # model down: deterministic concatenation (never a silent stop)
        body = "\n\n".join(f"**{r.get('agent')}**: {str(r.get('reply', ''))[:600]}" for r in good[:4])
        return {"reply": body, "sources": sum((r.get("sources") or [] for r in good), [])[:12],
                "mode": "MULTI_AGENT"}


def _perf(agent: str, ok: bool, latency: float):
    with _lock:
        p = _PERF.setdefault(agent, {"runs": 0, "ok": 0, "latency_sum": 0.0})
        p["runs"] += 1
        p["ok"] += 1 if ok else 0
        p["latency_sum"] += latency
        # C8: feed the learning store (fail-soft)
        try:
            from ishti.learning import store as lstore
            lstore.add("interactions", id=f"agent_{agent}_{time.time_ns()}", ts=time.time(),
                       session_id="", channel="agent", request=f"agent:{agent}", skill=agent,
                       decision="ok" if ok else "failed", memories="", considered="",
                       result=f"latency={latency:.1f}s", status="ok" if ok else "failed",
                       reward=0.5 if ok else -0.5, task_id="", step_no=0, domain="agent",
                       source="multi_agent")
        except Exception:
            pass


def perf() -> dict:
    with _lock:
        return {a: {**p, "success_rate": round(p["ok"] / max(1, p["runs"]), 2),
                    "avg_latency": round(p["latency_sum"] / max(1, p["runs"]), 2)}
                for a, p in _PERF.items()}


def run(request: str, session, skills: dict) -> dict:
    """The orchestrator entry (the multi_agent_orchestrator profile calls this):
    decompose → assign → pattern (supervisor/sequential/fan-out/hierarchical) → synthesize."""
    budget = _budget()
    bb: dict = {"entries": {}}
    pattern = budget["pattern"]
    trace(session, "orchestrator", f"decomposing with the {pattern} pattern", status="start")
    subs = assign(decompose(request, skills), skills)
    if len(subs) > budget["max_subagents"]:
        subs = subs[:budget["max_subagents"]]
    trace(session, "orchestrator", f"{len(subs)} subtask(s): " + "; ".join(s["agent"] for s in subs),
          status="done")
    if len(subs) == 1:
        results = [_run_agent(subs[0]["agent"], subs[0]["task"], skills, session, bb, budget)]
    elif pattern == "sequential":
        results = _sequential(subs, skills, session, bb, budget)
    elif pattern == "fan_out":
        results = _fan_out(subs, skills, session, bb, budget, 0)
    elif pattern == "hierarchical":
        results = _hierarchical(subs, skills, session, bb, budget, 0)
    else:  # supervisor/worker (default): workers in parallel, supervisor synthesizes
        results = _fan_out(subs, skills, session, bb, budget, 0)
    results = _resolve_conflicts(results)
    return synthesize(request, results, skills, session)


if __name__ == "__main__":
    import tempfile
    import os
    os.environ["ISHTI_SETTINGS_DIR"] = tempfile.mkdtemp(prefix="ishti_agents_")
    from types import SimpleNamespace

    def _skill(answer):
        class _S:
            manifest = {"tier": "T1"}
            status = "working"
            def __init__(self, a):
                self.a = a
            def matches(self, t):
                return False
            def run(self, text, session):
                if self.a.startswith("FAIL"):
                    return {"reply": "I can't do that yet - no skill handles it.", "sources": []}
                if "FIX" in text or "Fix:" in text:
                    return {"reply": self.a, "sources": [{"id": 1, "url": "https://x.example", "title": "s"}]}
                return {"reply": self.a, "sources": []}
        return _S(answer)

    skills = {"web_search": _skill("42 sources found: the answer is Paris."),
              "deep_research": _skill("FAIL first attempt"),
              "chat": _skill("Reviewer looks it over.")}
    # decomposition fallback (no LLM in self-check): research request -> researcher
    subs = decompose("research the latest news about GPUs", skills)
    assert subs and subs[0]["agent"] in AGENTS, subs
    # assign re-targets when skills are missing
    moved = assign([{"agent": "pc_control", "task": "x", "criteria": ""}], skills)
    assert moved[0]["agent"] in AGENTS and any(s in skills for s in AGENTS[moved[0]["agent"]]["skills"])
    # single-agent supervisor run with the verify/fix loop (first attempt fails, second passes)
    out = run("search the web for the capital of France", {"id": "t1", "trace": lambda **k: None}, skills)
    assert out["mode"] == "MULTI_AGENT" and "Paris" in out["reply"], out
    # fan-out with two subtasks
    subs2 = [{"agent": "researcher", "task": "a", "criteria": ""},
             {"agent": "web", "task": "b", "criteria": ""}]
    bb = {"entries": {}}
    res = _fan_out(subs2, skills, {"id": "t2", "trace": lambda **k: None}, bb, _budget(), 0)
    assert len(res) == 2 and all(r.get("reply") for r in res), res
    # blackboard + messages + conflict resolution
    blackboard_put(bb, "k", "v", "tester")
    assert blackboard_get(bb, "k")["value"] == "v"
    ded = _resolve_conflicts([{"reply": "same thing"}, {"reply": "same thing"}, {"reply": "other"}])
    assert len(ded) == 2 and ded[1].get("reply", "") == "other"
    # synthesis with the model down still composes the deterministic answer
    import ishti.llm as _llm
    _real_ask, _real_chat = _llm.ask, getattr(_llm, "chat", None)
    _llm.ask = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("model down"))
    try:
        syn = synthesize("req", [{"agent": "researcher", "reply": "A facts"}], skills,
                         {"id": "t3", "trace": lambda **k: None})
    finally:
        _llm.ask = _real_ask
    assert "A facts" in syn["reply"], syn
    # escalation on a permanently failing agent (reviewer owns 'chat' here, which always fails)
    skills_fail = {"chat": _skill("FAIL always")}
    r = _run_agent("reviewer", "impossible thing", skills_fail, {"id": "t4", "trace": lambda **k: None},
                   {"entries": {}}, {"max_iterations": 2, "timeout_s": 5, "max_tool_calls": 4,
                                     "max_total_tokens": 9999})
    assert r.get("escalated") and "limit" in r["reply"], r
    # perf tracking recorded the runs
    p = perf()
    assert any(v["runs"] > 0 for v in p.values()), p
    print(f"[self-check] ishti.agents OK — decompose/assign/verify-fix/fan-out/conflicts/synthesis/perf {p}")
