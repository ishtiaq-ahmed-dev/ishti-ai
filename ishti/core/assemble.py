"""Context assembly (Parts E3/E4): one ordered template with per-section token budgets.

    SYSTEM INSTRUCTIONS / CURRENT USER REQUEST (resolved) / CONVERSATION STATE /
    RELEVANT MEMORIES / RELEVANT PEOPLE / RELEVANT RELATIONSHIPS / RELEVANT PROCEDURES /
    RELEVANT LESSONS / RELEVANT SKILLS / CURRENT TASK STATE / AVAILABLE TOOLS

Every section gets a character budget from Settings (section 'memory', assembly budgets;
characters not tokens — one config knob, no tokenizer dependency). Filling is greedy in
template order; a section that would blow its own budget is trimmed line-wise. `budgets()`
reads the pydantic settings service so the UI edits them live; a failure falls back to
built-in defaults so assembly NEVER breaks a turn.

Retrieval itself stays where it lives (arch2.retrieval / learning.lessons / core.state) —
this module only ASSEMBLES what the retrievers return. Sections with nothing relevant are
omitted entirely: only relevant memories are ever injected (E2).
"""
from __future__ import annotations

import threading

try:  # best-effort failure log (never breaks the module if logging is unavailable)
    from ishti.core.logutil import swallowed
except Exception:  # pragma: no cover
    def swallowed(*_a, **_k):
        return None


_lock = threading.Lock()
_svc_cache = None

DEFAULT_BUDGETS = {  # characters per section
    "conversation_state": 400, "relevant_memories": 900, "relevant_people": 500,
    "relevant_relationships": 500, "relevant_procedures": 700, "relevant_lessons": 600,
    "relevant_skills": 300, "current_task_state": 300, "available_tools": 800,
    "total": 4500,
}


def _service():
    global _svc_cache
    with _lock:
        if _svc_cache is None:
            try:
                from ishti.core.settings import get_settings_service
                _svc_cache = get_settings_service()
            except Exception:
                _svc_cache = False
        return _svc_cache or None


def budgets() -> dict:
    """Per-section budgets: Settings -> Memory (assembly_*), merged over defaults."""
    out = dict(DEFAULT_BUDGETS)
    try:
        mem = _service().get().get("memory", {}) if _service() else {}
        for k, v in mem.items():
            if k.startswith("assembly_") and k[len("assembly_"):] in out and isinstance(v, int) and v > 0:
                out[k[len("assembly_"):]] = v
    except Exception as _exc:
        swallowed('ishti/core/assemble.py:budgets', _exc)
    return out


def _trim(block: str, cap: int) -> str:
    if len(block) <= cap:
        return block
    lines, used = block.splitlines(), 0
    keep = []
    for ln in lines:
        if used + len(ln) + 1 > cap and keep:
            break
        keep.append(ln)
        used += len(ln) + 1
    return "\n".join(keep)


def _add(parts: list, used: list, title: str, items, cap: int):
    items = [str(i).strip() for i in (items or []) if str(i).strip()]
    if not items or cap <= 0:
        return
    block = f"{title}:\n" + "\n".join(f"- {i}" for i in items)
    if len(block) > cap:
        block = _trim(block, cap)
    if len(block) < 8:
        return
    parts.append(block)
    used[0] += len(block)


def assemble(request: str, *, state_summary: str = "", memories: list | None = None,
             people: list | None = None, relationships: list | None = None,
             procedures: list | None = None, lessons: list | None = None,
             skills: list | None = None, task_state: str = "",
             tools: list | None = None, system_instructions: str = "",
             include_request: bool = True) -> str:
    """The E3 template filled with what the retrievers found. Omitted sections cost nothing."""
    b = budgets()
    parts: list[str] = []
    used = [0]
    if system_instructions:
        parts.append(system_instructions)
    if include_request:
        parts.append(f"CURRENT USER REQUEST: {request}")
    _add(parts, used, "CONVERSATION STATE", [state_summary] if state_summary else [], b["conversation_state"])
    _add(parts, used, "RELEVANT MEMORIES (dated: a newer line overrides an older one)", memories or [],
         b["relevant_memories"])
    _add(parts, used, "RELEVANT PEOPLE", people or [], b["relevant_people"])
    _add(parts, used, "RELEVANT RELATIONSHIPS", relationships or [], b["relevant_relationships"])
    _add(parts, used, "RELEVANT PROCEDURES", procedures or [], b["relevant_procedures"])
    _add(parts, used, "RELEVANT LESSONS", lessons or [], b["relevant_lessons"])
    _add(parts, used, "RELEVANT SKILLS", skills or [], b["relevant_skills"])
    _add(parts, used, "CURRENT TASK STATE", [task_state] if task_state else [], b["current_task_state"])
    _add(parts, used, "AVAILABLE TOOLS", tools or [], b["available_tools"])
    return "\n\n".join(parts)


def arch2_block(request: str, state_summary: str = "", session_id=None, capture=None) -> str:
    """The RELEVANT-* middle of the template from arch2's own retrievers (pipeline.py's
    context block). Returns the assembled memory context, '' when nothing is relevant."""
    from ishti.memstore import retrieval, people
    mems = retrieval.memory_hits(request)
    from ishti.memstore.memory import stated_on
    memories = [stated_on(m["row"]) + m["row"]["content"] for m in mems if m["row"]["type"] != "procedural"]
    procedures = [m["row"]["content"] for m in mems if m["row"]["type"] == "procedural"]
    graph = retrieval.graph_hits(request)
    people_lines, rel_lines = [], []
    for h in graph:
        first = h["text"].splitlines()[0] if h["text"] else ""
        (people_lines if " - " in first else rel_lines).append(h["text"].replace("\n", " / ")[:300])
    for m in mems:
        for ent in retrieval._entities_of_row(m["row"]):
            rec = people.record(ent.get("name", ""))
            if rec and rec["name"] not in {p.split(" - ")[0] for p in people_lines}:
                people_lines.append(f"{rec['name']} - {rec['relationship_to_user']} "
                                    f"(confidence {rec['confidence']:.2f})")
    lessons = []
    try:
        from ishti.learning import lessons as L
        got = L.guidance_for(request, limit=4, session_id=session_id)
        if isinstance(capture, list):
            capture.extend(str(l["lesson_id"]) for l in got if l.get("lesson_id"))
        lessons = [f"{l['statement']} (confidence {l['confidence']:.2f})" for l in got]
    except Exception as _exc:
        swallowed('ishti/core/assemble.py:arch2_block', _exc)
    convo = [h["text"][:280] for h in retrieval.vector_hits(request)]
    blocks = [assemble(request,
                       state_summary=state_summary,
                       memories=memories, people=people_lines, relationships=rel_lines,
                       procedures=procedures, lessons=lessons, task_state="",
                       tools=None, include_request=False)]  # the pipeline adds the request itself
    # Verified glossary: a definition question about a local-AI term gets the sourced definition in the
    # context, so the small model answers from it instead of inventing a meaning (see ishti/glossary.py).
    try:
        from ishti import glossary
        if line := glossary.context_line(request):
            blocks.append(line)
    except Exception as _exc:
        swallowed('ishti/core/assemble.py:arch2_block.glossary', _exc)
    if convo:
        blocks.append("POSSIBLY RELEVANT PAST CONTEXT (dated: newer overrides older):\n" + "\n".join(f"- {c}" for c in convo))
    out = "\n\n".join(x for x in blocks if x)
    if mems:
        retrieval.touch_hits(mems)
    return out


if __name__ == "__main__":
    out = assemble("message Ahmed that I'll be late",
                   state_summary="Topic: messaging Ahmed; constraint: casual tone",
                   memories=["Ahmed is my brother (explicit, confidence 1.0)"],
                   people=["Ahmed - brother (confidence 1.0)"],
                   relationships=["me brother of ahmed"],
                   procedures=["WhatsApp messages: casual tone, no formalities"],
                   lessons=["When messaging family, use a casual tone (confidence 0.7)"],
                   task_state="awaiting draft approval",
                   tools=["whatsapp: send a message"])
    for sec in ("CONVERSATION STATE", "RELEVANT MEMORIES", "RELEVANT PEOPLE",
                "RELEVANT RELATIONSHIPS", "RELEVANT PROCEDURES", "RELEVANT LESSONS",
                "CURRENT TASK STATE", "AVAILABLE TOOLS"):
        assert sec in out, sec
    # budgets are honored
    b = budgets()
    tiny = assemble("x", memories=[f"fact {i} " + "y" * 80 for i in range(50)])
    assert len(tiny) < 6000
    # empty sections are omitted, noise pulls nothing
    assert "RELEVANT PEOPLE" not in assemble("hello", memories=["fact one"])
    print(f"[self-check] core.assemble OK — E3 template, per-section budgets {b['relevant_memories']}/{b['total']} chars")
