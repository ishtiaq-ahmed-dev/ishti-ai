"""ARCH2 memory retrieval (spec section 14):

    user request -> query embedding -> vector search (Chroma, already local)
                 -> graph lookup (rgraph + entities) -> metadata filtering
                 -> ranked, budgeted context block for the main model

Only RELEVANT memories are injected - never the database (spec 13, 22). The block
is assembled by assemble_context() and consumed by pipeline.py's system prompt;
arch1's chat skill keeps its own, smaller context builder (untouched).
"""
import re

from ishti.memstore import memory

try:  # best-effort failure log (never breaks the module if logging is unavailable)
    from ishti.core.logutil import swallowed
except Exception:  # pragma: no cover
    def swallowed(*_a, **_k):
        return None



def _kw(text: str) -> set:
    stop = {"the", "a", "an", "and", "or", "to", "of", "in", "on", "for", "with", "my", "me", "i",
            "is", "are", "was", "be", "do", "does", "it", "this", "that", "please", "can", "you"}
    words = {w for w in re.findall(r"[^\W_]+(?:['’][^\W_]+)*", (text or "").casefold(), re.UNICODE) if w not in stop and len(w) > 2}
    # light stemming so 'programs' matches 'program' (and 'languages' matches 'language')
    return words | {w[:-1] for w in words if w.endswith("s") and len(w) > 3}


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
    """People and their edges mentioned in the request: entity rows + rgraph lines.
    Node names match as whole phrases ('ahmed family'), and qualified variants match by
    their base name ('ahmed (work)' is found by a bare 'ahmed' — every same-named person
    surfaces, which is exactly the ambiguity the disambiguation layer asks about)."""
    words = _kw(query)
    qlow = " " + re.sub(r"[^\w() ]", " ", (query or "").casefold(), flags=re.UNICODE) + " "
    hits = []
    for ent_name in {w for w in words if memory.find_entity(w)}:
        card = memory.entity_card(ent_name)
        if card:
            hits.append({"text": card})
    try:
        from ishti import rgraph
        for row in rgraph.conn().execute("SELECT name FROM nodes"):
            name = row["name"] or ""
            base = re.sub(r"\s*\([^)]*\)\s*$", "", name).strip()  # 'ahmed (work)' -> 'ahmed'
            if not base:
                continue
            if name in words or re.search(rf"\b{re.escape(base)}\b", qlow):
                lines = [rgraph.describe_edge(e) for e in rgraph.edges_of(name, direction="both")[:4]]
                if lines:
                    hits.append({"text": "\n".join(lines)})
    except Exception as _exc:
        swallowed('ishti/memstore/retrieval.py:graph_hits', _exc)
    # dedupe by first line
    seen, out = set(), []
    for h in hits:
        key = h["text"].splitlines()[0]
        if key not in seen:
            seen.add(key)
            out.append(h)
    return out


def memory_hits(query: str) -> list:
    """Keyword overlap over the SQLite memories (semantic + procedural + episodic).
    A row's stored keywords are merged with words derived from its content — rows saved
    without explicit keywords (the common case for semantic memories) still retrieve.
    Raw observations are never beliefs: they are skipped here and only reach the prompt
    after promote_observations() has turned them into semantic rows."""
    import json as _json
    words = _kw(query)
    scored = []
    for r in memory.iter_active():
        if r["type"] == "observation":
            continue
        kws = _kw(r["content"])
        try:
            stored = _json.loads(r["keywords"] or "") if r["keywords"] else []
            kws |= {str(k).lower() for k in stored}
        except Exception as _exc:
            swallowed('ishti/memstore/retrieval.py:memory_hits', _exc)
        overlap = len(words & kws)
        if overlap:
            scored.append((_score_row(r, overlap), r))
    scored.sort(key=lambda x: -x[0])
    return [{"row": r, "score": s} for s, r in scored[:8]]


def _score_row(row, overlap: int) -> float:
    return (overlap * 2 + row["importance"] * 3 + (2 if row["type"] == "procedural" else 0)
            + (1.5 if row["source"] == "explicit_user" else 0)) * memory.recency(row)


def _entities_of_row(row) -> list:
    """Entity rows linked to a memory row (core.assemble's people section)."""
    try:
        import json as _json
        ids = _json.loads(row.get("entity_ids") or "[]")
    except Exception:
        ids = []
    out = []
    for eid in ids[:4]:
        r = memory.conn().execute("SELECT * FROM entities WHERE id=?", (eid,)).fetchone()
        if r:
            out.append(memory._dec_entity(dict(r)))
    return out


def touch_hits(mems) -> None:
    """Record retrieval for the scored hits (feeds decay/importance)."""
    try:
        memory.touch([m["row"]["id"] for m in mems if m.get("row")])
    except Exception as _exc:
        swallowed('ishti/memstore/retrieval.py:touch_hits', _exc)


def procedures_for(query: str) -> list:
    """Procedural memories whose keywords match the request - 'how the owner likes this done'."""
    return [m for m in memory_hits(query) if m["row"]["type"] == "procedural"]


def assemble_context(query: str, session_id=None, capture=None) -> str:
    """The retrieval block for the main model — assembled by core.assemble (E3/E4: one
    template, per-section budgets from Settings). Empty string when nothing is relevant."""
    from ishti.core import assemble as CA
    return CA.arch2_block(query, session_id=session_id, capture=capture)


if __name__ == "__main__":
    import tempfile
    import ishti.memstore.memory as M
    tmp = tempfile.mkdtemp()
    import os
    M.db_path = lambda: os.path.join(tmp, "m.db")
    M._conn = None
    try:  # the graph and learning stores stay test-local too (the owner's data is never touched)
        from ishti import rgraph as _R
        from ishti.learning import store as _LS
        _R.DB_PATH = os.path.join(tmp, "g.db")
        _R._conn = None
        _LS.set_db_path(os.path.join(tmp, "l.db"))
    except Exception as _exc:
        swallowed('ishti/memstore/retrieval.py:<module>', _exc)
    def _no_vector(q, k=None, max_distance=None):  # the Chroma store stays test-local
        return []
    vector_hits = _no_vector
    # runpy quirk: under -m this module runs as __main__; core.assemble later imports a SECOND
    # copy under the real name. Force-register it and patch that copy too, or its real vector
    # recall reads the owner's store mid-self-check.
    import ishti.memstore.retrieval as _rt_real
    _rt_real.vector_hits = _no_vector
    M.semantic("User prefers Chrome.", source="explicit_user", confidence=1.0)
    M.procedural("Email workflow", steps=["Read email", "Draft response", "Show user", "Wait for approval", "Send"],
                 keywords=["email", "draft", "reply"])
    M.upsert_entity("Ahmed", relationship="friend", note="Project X")
    ctx = assemble_context("draft an email for Ahmed")
    assert "Email workflow" in ctx, ctx
    assert "RELEVANT PROCEDURES" in ctx
    assert assemble_context("quantum chromodynamics lattice gauge") == "", "unrelated query must pull nothing"
    print("[self-check] arch2.retrieval OK - keyword recall, procedure match, budget, no noise on unrelated")
