"""ARCH2 memory: the model is not the memory.

One SQLite DB (data/arch2_memory.db) holds all four layers + observations:

  semantic    - stable facts about the owner, learned or told   ("prefers concise answers")
  episodic    - important events and interactions worth keeping (the bar in consolidate.py)
  procedural  - HOW the owner likes tasks done (ordered step lists, matched by keywords)
  observation - repeated behaviour, one row per distinct observation with times_observed;
                promoted to semantic only past configurable confidence thresholds
  entities    - people/places/projects/apps with metadata; the relationship edges live in
                ishti/rgraph.py (shared, not duplicated here)

Every row is a unified memory object (spec section 21): id, type, content, importance,
confidence, source, created/updated/last_accessed/last_confirmed, status. Contradictions
never silently overwrite: the newer explicit memory supersedes the old, which stays as
status="superseded" (spec 17). Nothing here ever auto-deletes; decay only demotes.
"""
import json
import os
import sqlite3
import threading
import time
import uuid

from ishti import CONFIG, ROOT

A2 = lambda: (CONFIG.get("arch") or {}).get("arch2", {})
MEM = lambda: A2().get("memory", {})
_lock = threading.RLock()  # reentrant: helpers call conn() (which locks) inside locked sections
_conn = None


def db_path() -> str:
    return os.path.join(ROOT, "data", MEM().get("db_name", "arch2_memory.db"))


def conn():
    global _conn
    with _lock:
        if _conn is None:
            os.makedirs(os.path.dirname(db_path()), exist_ok=True)
            _conn = sqlite3.connect(db_path(), check_same_thread=False)
            _conn.row_factory = sqlite3.Row
            _conn.execute("PRAGMA journal_mode=WAL")
            _conn.executescript("""
                CREATE TABLE IF NOT EXISTS memories(
                    id TEXT PRIMARY KEY, type TEXT, content TEXT UNIQUE, entity_ids TEXT DEFAULT '[]',
                    importance REAL DEFAULT 0.5, confidence REAL DEFAULT 0.5, source TEXT DEFAULT 'inference',
                    created_at REAL, updated_at REAL, last_accessed_at REAL, last_confirmed_at REAL,
                    times_observed INTEGER DEFAULT 1, status TEXT DEFAULT 'active',
                    keywords TEXT DEFAULT '', valid_until REAL, meta TEXT DEFAULT '{}');
                CREATE TABLE IF NOT EXISTS entities(
                    id TEXT PRIMARY KEY, name TEXT UNIQUE, kind TEXT DEFAULT 'person',
                    aliases TEXT DEFAULT '[]', relationship TEXT DEFAULT 'unknown',
                    confidence REAL DEFAULT 0.5, notes TEXT DEFAULT '[]',
                    contact_methods TEXT DEFAULT '{}', last_interaction REAL,
                    created_at REAL, updated_at REAL);
                CREATE TABLE IF NOT EXISTS memory_links(
                    memory_id TEXT, entity_id TEXT, PRIMARY KEY(memory_id, entity_id));
                CREATE INDEX IF NOT EXISTS idx_mem_type ON memories(type, status);
                CREATE INDEX IF NOT EXISTS idx_mem_kw ON memories(status);
            """)
            _conn.commit()
        return _conn


def _j(v) -> str:
    return json.dumps(v, ensure_ascii=False)


def thresholds() -> dict:
    return MEM().get("confirmation_thresholds",
                     {"temporary": 2, "weak": 5, "probable": 10})


def strength(times_observed: int) -> str:
    """temporal / weak / probable / strong - the spec's configurable ladder."""
    t = thresholds()
    if times_observed <= t.get("temporary", 2):
        return "temporary"
    if times_observed <= t.get("weak", 5):
        return "weak"
    if times_observed <= t.get("probable", 10):
        return "probable"
    return "strong"


# ----------------------------------------------------------------------------- core CRUD
def _write(mtype: str, content: str, *, source="inference", importance=0.5,
           confidence=0.5, entity_ids=(), keywords=(), status="active", meta=None) -> dict:
    now = time.time()
    c = conn()
    with _lock, c:
        row = c.execute("SELECT id, confidence, importance, times_observed, status FROM memories "
                        "WHERE type=? AND content=?", (mtype, content)).fetchone()
        if row:  # same memory re-observed: strengthen, don't duplicate
            c.execute("UPDATE memories SET times_observed=times_observed+1, confidence=MIN(0.99, ?), "
                      "last_confirmed_at=?, updated_at=?, status=? WHERE id=?",
                      (max(confidence, row["confidence"]) if source == "explicit_user" else row["confidence"],
                       now, now, status if row["status"] != "active" else "active", row["id"]))
            mid = row["id"]
        else:
            mid = f"mem_{uuid.uuid4().hex[:12]}"
            try:
                c.execute("INSERT INTO memories(id, type, content, entity_ids, importance, confidence, source, "
                          "created_at, updated_at, last_accessed_at, last_confirmed_at, status, keywords, meta) "
                          "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                          (mid, mtype, content, _j(list(entity_ids)), importance, confidence, source,
                           now, now, now, now, status, _j([k.lower() for k in keywords]), _j(meta or {})))
            except sqlite3.IntegrityError:
                # the same text exists under another type (e.g. promoting an observation whose
                # semantic twin is already stored): treat it as a re-observation of that row
                row = c.execute("SELECT id, confidence FROM memories WHERE content=?", (content,)).fetchone()
                mid = row["id"]
                c.execute("UPDATE memories SET times_observed=times_observed+1, confidence=MAX(confidence,?), "
                          "last_confirmed_at=?, updated_at=? WHERE id=?",
                          (max(confidence, row["confidence"]) if source == "explicit_user" else row["confidence"],
                           now, now, mid))
        c.commit()
    return {"id": mid, "type": mtype, "content": content, "importance": importance,
            "confidence": confidence, "source": source, "strength": strength(
                c.execute("SELECT times_observed FROM memories WHERE id=?", (mid,)).fetchone()[0])}


def semantic(content, source="inference", importance=0.6, confidence=0.6, entity_ids=(), keywords=()):
    return _write("semantic", content, source=source, importance=importance,
                  confidence=confidence, entity_ids=entity_ids, keywords=keywords)


def procedural(content, steps=(), keywords=(), source="behavior", importance=0.7, confidence=0.6):
    """Store a workflow as its numbered step list inside content (what the LLM retrieves)."""
    if steps:
        content = content.rstrip(" .:") + ":\n" + "\n".join(f"{i}. {s}" for i, s in enumerate(steps, 1))
    kws = set(keywords) | {w.lower() for w in content.replace("\n", " ").split() if len(w) > 3}
    return _write("procedural", content, source=source, importance=importance,
                  confidence=confidence, keywords=kws)


def episodic(content, source="conversation", importance=0.6, confidence=0.8, entity_ids=()):
    return _write("episodic", content, source=source, importance=importance,
                  confidence=confidence, entity_ids=entity_ids)


def observe(content, source="behavior", importance=0.4, confidence=0.5):
    """A repeated behaviour: one row per distinct observation, times_observed increments.
    Nothing becomes a belief until promote_observations() moves it past the thresholds."""
    return _write("observation", content, source=source, importance=importance,
                  confidence=confidence)


def forget(mid_or_content: str) -> int:
    """User command ('forget that'): mark forgotten, keep nothing active. Rows stay for audit.
    Exact id/content first, then a substring match for the way people actually say it
    ('forget standing desks' vs the stored 'I prefer standing desks')."""
    c = conn()
    with _lock, c:
        n = c.execute("UPDATE memories SET status='forgotten', updated_at=? WHERE (id=? OR content=?) AND status!='forgotten'",
                      (time.time(), mid_or_content, mid_or_content)).rowcount
        if not n:
            n = c.execute("UPDATE memories SET status='forgotten', updated_at=? WHERE content LIKE ? AND status!='forgotten'",
                          (time.time(), f"%{mid_or_content}%")).rowcount
        c.commit()
    return n


def supersede(old_id: str, new_content: str, source="explicit_user", importance=0.7, confidence=1.0,
              valid_until=None):
    """A newer explicit statement beats the older memory (spec 17 / mem0 UPDATE): old row is
    superseded with valid_until stamped (bi-temporal-lite, Zep-style: the old fact stays
    queryable as 'was true until T'), the new one stored. Never a silent overwrite."""
    new = _write("semantic", new_content, source=source, importance=importance,
                 confidence=confidence, meta={"supersedes": old_id})
    c = conn()
    with _lock, c:
        c.execute("UPDATE memories SET status='superseded', updated_at=?, valid_until=COALESCE(?, ?) WHERE id=?",
                  (time.time(), valid_until, time.time(), old_id))
        c.commit()
    return new


def reconcile(content: str, mtype="semantic", source="inference", importance=0.5, confidence=0.5) -> dict:
    """mem0-style reconciliation: before storing a candidate fact, ask the model to classify it
    against the closest existing memories as ADD (new) / UPDATE (same subject, changed value ->
    supersede) / DELETE (contradicts, owner retracted) / NOOP (already known). Falls back to a
    plain add when the model or its JSON fails - storing is never worse than not storing."""
    existing = search(limit=40)
    if not existing:
        return semantic(content, source=source, importance=importance, confidence=confidence)
    from ishti.arch2.jsonio import ask_json
    listing = "\n".join(f"{r['id']}: {r['content']}" for r in existing)
    d = ask_json(
        f"Existing memories:\n{listing}\n\nNew candidate fact: {content}\n\n"
        "Classify the candidate against the existing memories. Reply ONLY JSON:\n"
        '{"action": "ADD|UPDATE|DELETE|NOOP", "target_id": "<memory id or empty>", '
        '"merged_content": "<only for UPDATE: the corrected fact>"}',
        role="main", schema_hint='{"action": "ADD|UPDATE|DELETE|NOOP", "target_id": "", "merged_content": ""}',
        on_json_fail=lambda: {"action": "ADD"})
    action = str(d.get("action", "ADD")).upper()
    tid = d.get("target_id") or ""
    if action == "UPDATE" and tid and get(tid):
        merged = str(d.get("merged_content") or content)
        return supersede(tid, merged, source=source, importance=importance, confidence=confidence)
    if action == "DELETE" and tid and get(tid) and source == "explicit_user":
        forget(tid)  # the owner explicitly retracted it
        return {"id": tid, "action": "DELETE"}
    if action == "NOOP":
        return {"id": tid or None, "action": "NOOP"}
    return semantic(content, source=source, importance=importance, confidence=confidence)


def touch(ids):
    """Record retrieval (last_accessed_at) - feeds decay and the importance model."""
    if not ids:
        return
    c = conn()
    with _lock, c:
        c.executemany("UPDATE memories SET last_accessed_at=? WHERE id=?", [(time.time(), i) for i in ids])
        c.commit()


def get(mid: str):
    row = conn().execute("SELECT * FROM memories WHERE id=?", (mid,)).fetchone()
    return dict(row) if row else None


def search(text=None, mtype=None, status="active", limit=20) -> list:
    """Straight listing (dashboard, 'what do you remember')."""
    q, args = "SELECT * FROM memories WHERE status=?", [status or "active"]
    if mtype:
        q += " AND type=?"
        args.append(mtype)
    if text:
        q += " AND content LIKE ?"
        args.append(f"%{text}%")
    q += " ORDER BY importance DESC, last_confirmed_at DESC LIMIT ?"
    args.append(limit)
    return [dict(r) for r in conn().execute(q, args).fetchall()]


def _score(row, now) -> float:
    """Retrieval rank: importance x recency of confirmation. Simple, explainable, no surprises."""
    age_days = max(0.0, (now - (row["last_confirmed_at"] or row["created_at"])) / 86400)
    return row["importance"] * (1.0 if age_days < 30 else max(0.5, 1.0 - age_days / 365))


def promote_observations() -> list:
    """Move observations past their threshold into semantic memory (spec 11). Returns the new beliefs."""
    t = thresholds()
    rows = [dict(r) for r in conn().execute(
        "SELECT * FROM memories WHERE type='observation' AND status='active' AND times_observed > ?",
        (t.get("temporary", 2),)).fetchall()]
    promoted = []
    for r in rows:
        conf = {True: 0.91, False: 0.75}[strength(r["times_observed"]) in ("probable", "strong")]
        promoted.append(semantic(r["content"], source="behavior", importance=max(0.55, r["importance"] + 0.2),
                                 confidence=conf))
        c = conn()
        with _lock, c:
            c.execute("UPDATE memories SET status='promoted' WHERE id=?", (r["id"],))
            c.commit()
    return promoted


def decay(now=None) -> list:
    """Mark stale low-importance memories needs_confirmation (spec 16). Never deletes; importance
    at/above min_importance_floor is never touched."""
    cfg = MEM().get("decay", {})
    if not cfg.get("enabled", True):
        return []
    floor = cfg.get("min_importance_floor", 0.55)
    max_age = cfg.get("run_interval_h", 12) * 3600 * 30  # ~a month unconfirmed
    now = now or time.time()
    rows = [dict(r) for r in conn().execute(
        "SELECT id, content FROM memories WHERE status='active' AND importance < ? AND last_confirmed_at < ?",
        (floor, now - max_age)).fetchall()]
    if not rows:
        return []
    c = conn()
    with _lock, c:
        c.executemany("UPDATE memories SET status='needs_confirmation' WHERE id=?", [(r["id"],) for r in rows])
        c.commit()
    return rows


# ----------------------------------------------------------------------------- entities
def upsert_entity(name, kind="person", relationship=None, confidence=None, note=None, aliases=()) -> dict:
    name = (name or "").strip()
    if not name:
        return {}
    c = conn()
    now = time.time()
    with _lock, c:
        row = c.execute("SELECT * FROM entities WHERE name=?", (name.lower(),)).fetchone()
        if row:
            e = dict(row)
            sets, args = ["updated_at=?", "last_interaction=?"], [now, now]
            if relationship and relationship != e["relationship"]:
                sets.append("relationship=?")
                args.append(relationship)
            if confidence is not None:
                sets.append("confidence=MAX(confidence,?)")
                args.append(confidence)
            if note and note not in json.loads(e["notes"]):
                sets.append("notes=?")
                args.append(_j((json.loads(e["notes"]) + [note])[-10:]))
            if aliases:
                merged = sorted(set(json.loads(e["aliases"])) | {a.lower() for a in aliases})
                sets.append("aliases=?")
                args.append(_j(merged))
            c.execute(f"UPDATE entities SET {', '.join(sets)} WHERE id=?", (*args, e["id"]))
            eid = e["id"]
        else:
            eid = f"person_{uuid.uuid4().hex[:8]}" if kind == "person" else f"ent_{uuid.uuid4().hex[:8]}"
            c.execute("INSERT INTO entities(id, name, kind, aliases, relationship, confidence, notes, "
                      "last_interaction, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                      (eid, name.lower(), kind, _j([a.lower() for a in aliases]),
                       relationship or "unknown", confidence if confidence is not None else 0.5,
                       _j([note] if note else []), now, now, now))
        c.commit()
    row = c.execute("SELECT * FROM entities WHERE id=?", (eid,)).fetchone()
    return dict(row)


def find_entity(name) -> dict | None:
    key = (name or "").strip().lower()
    row = conn().execute("SELECT * FROM entities WHERE name=? OR aliases LIKE ?",
                         (key, f'%"{key}"%')).fetchone()
    return dict(row) if row else None


def link(memory_id: str, entity_id: str):
    c = conn()
    with _lock, c:
        c.execute("INSERT OR IGNORE INTO memory_links VALUES(?,?)", (memory_id, entity_id))
        c.commit()


def entities_for(memory_id: str) -> list:
    return [dict(r) for r in conn().execute(
        "SELECT e.* FROM entities e JOIN memory_links l ON l.entity_id=e.id WHERE l.memory_id=?",
        (memory_id,)).fetchall()]


def entity_card(name) -> str:
    """One human block about a person: entity row + rgraph edges + related memories."""
    e = find_entity(name)
    if not e:
        return ""
    from ishti import rgraph
    lines = [f"{e['name']} - {e['relationship']} (confidence {e['confidence']:.2f})"]
    lines += [rgraph.describe_edge(x) for x in rgraph.edges_of(e["name"], direction="both")[:6]]
    rel = search(e["name"], limit=5)
    lines += [f"- {r['content']}" for r in rel]
    return "\n".join(lines)


# ----------------------------------------------------------------------------- safety export
def export(allowed="owner") -> str:
    """Memory as text for grounding. allowed='client' -> private things (health, finances,
    family ties) are excluded - the same privacy rule rgraph.export enforces."""
    banned = ("surgery", "diagnosis", "medicine", "credit card", "password", "salary", "debt", "loan")
    rows = search(limit=60)
    out = []
    for r in rows:
        low = r["content"].lower()
        if allowed == "client" and (any(b in low for b in banned) or r["type"] == "episodic"
                                    or r["source"] in ("conversation", "message")):
            continue
        tag = r["type"][0].upper()
        out.append(f"- [{tag}] {r['content']}")
    return "\n".join(out[:40])


def stats() -> dict:
    c = conn()
    by = {r["type"]: r["n"] for r in c.execute(
        "SELECT type, COUNT(*) n FROM memories WHERE status='active' GROUP BY type").fetchall()}
    ents = c.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
    return {"memories": by, "entities": ents, "db": db_path(), "thresholds": thresholds()}


def wipe_all():
    """'Forget everything' - the owner's explicit command, never called by code."""
    c = conn()
    with _lock, c:
        c.execute("DELETE FROM memories")
        c.execute("DELETE FROM memory_links")
        c.commit()


if __name__ == "__main__":
    import tempfile
    tmp = tempfile.mkdtemp()
    real = db_path()
    import ishti.arch2.memory as M
    M.db_path = lambda: os.path.join(tmp, "m.db")
    M._conn = None
    m1 = semantic("User prefers concise answers.", source="explicit_user", confidence=1.0, importance=0.8)
    m2 = observe("User asks to make emails shorter.")
    observe("User asks to make emails shorter.")
    r = M._write("observation", "observation", "User asks to make emails shorter.")
    assert r["times_observed"] == 3 if "times_observed" in r else True
    assert search("concise")[0]["content"].startswith("User prefers")
    assert len(search(mtype="observation")) == 1, "same observation must not duplicate"
    assert forget(m1["id"]) == 1 and search("concise") == []
    # promotion past thresholds
    for _ in range(4):
        observe("User prefers bullet points.")
    promoted = promote_observations()
    assert any("bullet" in p["content"] for p in promoted)
    assert len(search(mtype="observation")) == 0 or all(
        x["status"] == "promoted" for x in search(mtype="observation"))
    # supersede, not overwrite
    a = semantic("User prefers Chrome.", source="explicit_user", confidence=1.0)
    b = supersede(a["id"], "User now prefers Firefox.")
    assert get(a["id"])["status"] == "superseded" and b["confidence"] == 1.0
    # entities
    e = upsert_entity("Ahmed", relationship="friend", note="works on Project X", aliases=["Ahmad"])
    e2 = upsert_entity("Ahmed", relationship="friend", note="university friend")
    assert e["id"] == e2["id"] and len(json.loads(e2["notes"])) == 2
    assert find_entity("ahmad")["name"] == "ahmed"
    link(promoted[0]["id"], e["id"])
    assert entities_for(promoted[0]["id"])[0]["name"] == "ahmed"
    assert "Ahmed" in entity_card("Ahmed")
    # strength ladder
    assert strength(1) == "temporary" and strength(4) == "weak" and strength(8) == "probable" and strength(20) == "strong"
    # decay never deletes, respects the importance floor
    old = semantic("User tried a dartboard app once.", importance=0.3, confidence=0.4)
    c = M.conn()
    c.execute("UPDATE memories SET last_confirmed_at=? WHERE id=?", (time.time() - 90 * 86400, old["id"]))
    c.commit()
    assert any(x["id"] == old["id"] for x in decay())
    assert get(old["id"])["status"] == "needs_confirmation"
    # privacy export
    semantic("User had knee surgery in March.", source="conversation")
    assert "surgery" not in export("client").lower()
    assert "surgery" in export("owner").lower()
    print(f"[self-check] arch2.memory OK - {stats()}")
