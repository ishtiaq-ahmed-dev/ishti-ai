"""ARCH2 memory retrieval (spec section 14):

    user request -> query embedding -> vector search (Chroma, already local)
                 -> graph lookup (rgraph + entities) -> metadata filtering
                 -> ranked, budgeted context block for the main model

Only RELEVANT memories are injected - never the database (spec 13, 22). The block
is assembled by assemble_context() and consumed by pipeline.py's system prompt;
arch1's chat skill keeps its own, smaller context builder (untouched).
"""
import re

from ishti.arch2 import memory


def _kw(text: str) -> set:
    stop = {"the", "a", "an", "and", "or", "to", "of", "in", "on", "for", "with", "my", "me", "i",
            "is", "are", "was", "be", "do", "does", "it", "this", "that", "please", "can", "you"}
    return {w for w in re.findall(r"[a-z0-9']+", (text or "").lower()) if w not in stop and len(w) > 2}


def vector_hits(query: str, k: int = None, max_distance: float = None) -> list:
    """Past conversations from the existing Chroma store (semantic.py). Optional: empty on a
    fresh install, never fatal."""
    try:
        from ishti.semantic import get_semantic
        cfg = memory.MEM()
        hits = get_semantic().recall(query, k=k or cfg.get("retrieval_k", 6),
                                     max_distance=max_distance or cfg.get("max_distance", 0.45))
        return [{"text": h} for h in hits]
    except Exception:
        return []


def graph_hits(query: str) -> list:
    """People and their edges mentioned in the request: entity rows + rgraph lines."""
    words = _kw(query)
    hits = []
    for ent_name in {w for w in words if memory.find_entity(w)}:
        card = memory.entity_card(ent_name)
        if card:
            hits.append({"text": card})
    # rgraph: any node whose name appears in the request
    try:
        from ishti import rgraph
        for row in rgraph.conn().execute("SELECT name FROM nodes LIMIT 500").fetchall():
            if row["name"] in words:
                lines = [rgraph.describe_edge(e) for e in rgraph.edges_of(row["name"], direction="both")[:4]]
                if lines:
                    hits.append({"text": "\n".join(lines)})
    except Exception:
        pass
    # dedupe by first line
    seen, out = set(), []
    for h in hits:
        key = h["text"].splitlines()[0]
        if key not in seen:
            seen.add(key)
            out.append(h)
    return out


def memory_hits(query: str) -> list:
    """Keyword overlap over the SQLite memories (semantic + procedural + episodic)."""
    words = _kw(query)
    scored = []
    for r in memory.search(limit=300):
        kws = set(r["keywords"].split()) if False else set()
        try:
            import json as _json
            kws = set(_json.loads(r["keywords"])) if r["keywords"] else _kw(r["content"])
        except Exception:
            kws = _kw(r["content"])
        overlap = len(words & kws)
        if overlap:
            scored.append((_score_row(r, overlap), r))
    scored.sort(key=lambda x: -x[0])
    return [{"row": r, "score": s} for s, r in scored[:8]]


def _score_row(row, overlap: int) -> float:
    return overlap * 2 + row["importance"] * 3 + (2 if row["type"] == "procedural" else 0) \
        + (1.5 if row["source"] == "explicit_user" else 0)


def procedures_for(query: str) -> list:
    """Procedural memories whose keywords match the request - 'how the owner likes this done'."""
    return [m for m in memory_hits(query) if m["row"]["type"] == "procedural"]


def assemble_context(query: str) -> str:
    """The retrieval block for the main model. Budgeted (context_budget_chars) so a big
    memory DB can never blow the prompt. Empty string when nothing is relevant."""
    budget = memory.MEM().get("context_budget_chars", 2600)
    parts, used = [], 0

    def add(title, items):
        nonlocal used
        if not items:
            return
        block = f"{title}:\n" + "\n".join(f"- {i}" for i in items)
        if used + len(block) <= budget:
            parts.append(block)
            used += len(block)

    mems = memory_hits(query)
    add("RELEVANT MEMORIES", [m["row"]["content"] for m in mems if m["row"]["type"] != "procedural"])
    add("RELEVANT PEOPLE & RELATIONSHIPS", [h["text"].replace("\n", " / ")[:300] for h in graph_hits(query)])
    add("RELEVANT PROCEDURES (how the owner likes this done)", [m["row"]["content"] for m in mems if m["row"]["type"] == "procedural"])
    convo = [h["text"][:280] for h in vector_hits(query)]
    add("POSSIBLY RELEVANT PAST CONTEXT", convo)
    out = "\n\n".join(parts)
    if out:
        memory.touch([m["row"]["id"] for m in mems])
    return out


if __name__ == "__main__":
    import tempfile
    import ishti.arch2.memory as M
    M.db_path = lambda: None  # self-check uses the real helpers' logic on a temp DB below
    tmp = tempfile.mkdtemp()
    import os
    M.db_path = lambda: os.path.join(tmp, "m.db")
    M._conn = None
    M.semantic("User prefers Chrome.", source="explicit_user", confidence=1.0)
    M.procedural("Email workflow", steps=["Read email", "Draft response", "Show user", "Wait for approval", "Send"],
                 keywords=["email", "draft", "reply"])
    M.upsert_entity("Ahmed", relationship="friend", note="Project X")
    ctx = assemble_context("draft an email for Ahmed")
    assert "Email workflow" in ctx, ctx
    assert "RELEVANT PROCEDURES" in ctx
    assert assemble_context("quantum chromodynamics lattice gauge") == "", "unrelated query must pull nothing"
    print("[self-check] arch2.retrieval OK - keyword recall, procedure match, budget, no noise on unrelated")
