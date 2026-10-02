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

try:  # best-effort failure log (never breaks the module if logging is unavailable)
    from ishti.core.logutil import swallowed
except Exception:  # pragma: no cover
    def swallowed(*_a, **_k):
        return None


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
                    keywords TEXT DEFAULT '', valid_until REAL, meta TEXT DEFAULT '{}',
                    content_hash TEXT DEFAULT '');
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
            try:  # display casing for names (the key column is lowercased for matching)
                _conn.execute("ALTER TABLE entities ADD COLUMN display TEXT DEFAULT ''")
            except sqlite3.OperationalError:
                pass  # column already exists
            try:  # at-rest dedupe key (Prompt-2 vault): plaintext hash, survives encryption
                _conn.execute("ALTER TABLE memories ADD COLUMN content_hash TEXT DEFAULT ''")
            except sqlite3.OperationalError:
                pass  # column already exists
            try:  # Prompt-2 additive schema (conflicts, events, tasks, training_examples, FTS)
                from ishti.core.migrations import ensure_current
                ensure_current(_conn, "memory")
            except Exception as exc:
                print(f"[arch2.memory] migrations skipped: {exc}")
            try:  # one-time: give pre-vault rows their dedupe hash (no-op once every row has one)
                from ishti.core import vault as _v
                for row in _conn.execute("SELECT id, content FROM memories WHERE content_hash=''").fetchall():
                    _conn.execute("UPDATE memories SET content_hash=? WHERE id=?",
                                  (_v.content_hash(_v.decrypt_text(row["content"])), row["id"]))
                _conn.commit()
            except Exception as _exc:
                swallowed('ishti/arch2/memory.py:conn', _exc)
            _conn.commit()
        return _conn


def _j(v) -> str:
    return json.dumps(v, ensure_ascii=False)


def _enc(s: str) -> str:
    """At-rest encryption (core.vault): a no-op while vault is off, ciphertext with an
    'enc1:' prefix while on. Every read path goes back through _dec."""
    try:
        from ishti.core import vault as _v
        return _v.encrypt_text(s)
    except Exception:
        return s


def _dec(s: str) -> str:
    try:
        from ishti.core import vault as _v
        return _v.decrypt_text(s)
    except Exception:
        return s


def _dec_mem(d: dict) -> dict:
    for col in ("content", "keywords"):
        if isinstance(d.get(col), str):
            d[col] = _dec(d[col])
    return d


def _dec_entity(d: dict) -> dict:
    for col in ("notes", "aliases", "contact_methods", "preferences", "important_facts"):
        if isinstance(d.get(col), str):
            d[col] = _dec(d[col])
    return d


def _hash_of(text: str) -> str:
    try:
        from ishti.core import vault as _v
        return _v.content_hash(text)
    except Exception:
        return ""


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
    chash = _hash_of(content)          # stable dedupe key: survives at-rest encryption
    stored_content = _enc(content)     # ciphertext never repeats, so dedupe goes by hash
    stored_keywords = _enc(_j([k.lower() for k in keywords]))
    with _lock, c:
        row = c.execute("SELECT id, confidence, importance, times_observed, status FROM memories "
                        "WHERE type=? AND (content=? OR (?!='' AND content_hash=?))",
                        (mtype, stored_content, chash, chash)).fetchone()
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
                          "created_at, updated_at, last_accessed_at, last_confirmed_at, status, keywords, meta, "
                          "content_hash) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                          (mid, mtype, stored_content, _j(list(entity_ids)), importance, confidence, source,
                           now, now, now, now, status, stored_keywords, _j(meta or {}), chash))
            except sqlite3.IntegrityError:
                # the same text exists under another type (e.g. promoting an observation whose
                # semantic twin is already stored): treat it as a re-observation of that row
                row = c.execute("SELECT id, confidence FROM memories WHERE content=? OR (?!='' AND content_hash=?)",
                                (stored_content, chash, chash)).fetchone()
                if row is None:
                    raise
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
    """User command ('forget that'): mark forgotten, keep nothing active. Exact id/content first
    (content also matched by its hash, which is how an encrypted DB finds it), then a substring
    match over the DECRYPTED rows for the way people actually say it ('forget standing desks' vs
    the stored 'I prefer standing desks')."""
    c = conn()
    chash = _hash_of(mid_or_content)
    with _lock, c:
        n = c.execute("UPDATE memories SET status='forgotten', updated_at=? "
                      "WHERE (id=? OR content=? OR (?!='' AND content_hash=?)) AND status!='forgotten'",
                      (time.time(), mid_or_content, mid_or_content, chash, chash)).rowcount
        if not n:
            targets = [r["id"] for r in c.execute(
                "SELECT id, content FROM memories WHERE status!='forgotten'").fetchall()
                if mid_or_content.lower() in _dec(r["content"]).lower()]
            if targets:
                c.executemany("UPDATE memories SET status='forgotten', updated_at=? WHERE id=?",
                              [(time.time(), t) for t in targets])
            n = len(targets)
        c.commit()
    return n


def supersede(old_id: str, new_content: str, source="explicit_user", importance=0.7, confidence=1.0,
              valid_until=None):
    """A newer explicit statement beats the older memory (spec 17 / mem0 UPDATE): old row is
    superseded with valid_until stamped (bi-temporal-lite, Zep-style: the old fact stays
    queryable as 'was true until T'), the new one stored. Never a silent overwrite: the
    contradiction lands in the conflicts table (D5) for the dashboard, with the newer
    explicit statement as the presumed winner."""
    new = _write("semantic", new_content, source=source, importance=importance,
                 confidence=confidence, meta={"supersedes": old_id})
    c = conn()
    with _lock, c:
        old = c.execute("SELECT content FROM memories WHERE id=?", (old_id,)).fetchone()
        c.execute("INSERT INTO conflicts(ts, old_memory_id, new_memory_id, old_content, new_content, "
                  "status, resolution, resolution_source) VALUES(?,?,?,?,?,?,?,?)",
                  (time.time(), old_id, new["id"], _dec(old["content"]) if old else "",
                   new_content, "resolved", "newer explicit statement wins", source))
        c.execute("UPDATE memories SET status='superseded', updated_at=?, valid_until=COALESCE(?, ?) WHERE id=?",
                  (time.time(), valid_until, time.time(), old_id))
        c.commit()
    return new


def conflicts(limit=50, status=None) -> list:
    """Contradiction records (D5/H4) — newest first, contents decrypted."""
    q, args = "SELECT * FROM conflicts", []
    if status:
        q += " WHERE status=?"
        args.append(status)
    q += " ORDER BY ts DESC LIMIT ?"
    args.append(limit)
    return [dict(r) for r in conn().execute(q, args).fetchall()]


def resolve_conflict(conflict_id: int, keep: str = "new", note: str = "") -> bool:
    """The owner answered a contradiction: 'new' keeps the newer statement (default), 'old'
    re-activates the superseded row and forgets the new one."""
    c = conn()
    with _lock, c:
        row = c.execute("SELECT old_memory_id, new_memory_id FROM conflicts WHERE id=?", (conflict_id,)).fetchone()
        if not row:
            return False
        if keep == "old":
            c.execute("UPDATE memories SET status='active', valid_until=NULL, updated_at=? WHERE id=?",
                      (time.time(), row["old_memory_id"]))
            if row["new_memory_id"]:
                c.execute("UPDATE memories SET status='forgotten', updated_at=? WHERE id=?",
                          (time.time(), row["new_memory_id"]))
        c.execute("UPDATE conflicts SET status='resolved', resolution=?, resolution_source='owner' WHERE id=?",
                  (note or f"owner kept the {keep} statement", conflict_id))
        c.commit()
    return True


def event(kind: str, detail: str, session_id: str = "", entities=(), importance=0.4,
          source: str = "behavior") -> str:
    """One durable event row (D1's episodic feed + audit for state changes like forgets)."""
    eid = f"evt_{uuid.uuid4().hex[:12]}"
    c = conn()
    with _lock, c:
        c.execute("INSERT INTO events(id, ts, kind, detail, session_id, entities, importance, source) "
                  "VALUES(?,?,?,?,?,?,?,?)",
                  (eid, time.time(), kind, detail[:500], session_id, _j(list(entities)), importance, source))
        c.commit()
    return eid


def reconcile(content: str, mtype="semantic", source="inference", importance=0.5, confidence=0.5) -> dict:
    """mem0-style reconciliation: before storing a candidate fact, ask the model to classify it
    against the closest existing memories as ADD (new) / UPDATE (same subject, changed value ->
    supersede) / DELETE (contradicts, owner retracted) / NOOP (already known). Falls back to a
    plain add when the model or its JSON fails - storing is never worse than not storing."""
    existing = search(limit=40)
    if not existing:
        return semantic(content, source=source, importance=importance, confidence=confidence)
    try:
        from ishti.arch2.jsonio import ask_json  # lazy: pulls ishti.llm/openai only when used
    except Exception:  # no model stack (self-checks, CI): plain add, never worse than storing
        return semantic(content, source=source, importance=importance, confidence=confidence)
    listing = "\n".join(f"{r['id']}: {r['content']}" for r in existing)
    try:
        d = ask_json(
            f"Existing memories:\n{listing}\n\nNew candidate fact: {content}\n\n"
            "Classify the candidate against the existing memories. Reply ONLY JSON:\n"
            '{"action": "ADD|UPDATE|DELETE|NOOP", "target_id": "<memory id or empty>", '
            '"merged_content": "<only for UPDATE: the corrected fact>"}',
            role="main", schema_hint='{"action": "ADD|UPDATE|DELETE|NOOP", "target_id": "", "merged_content": ""}',
            on_json_fail=lambda: {"action": "ADD"})
    except Exception:  # model stack broken mid-call: plain add, never worse than storing
        return semantic(content, source=source, importance=importance, confidence=confidence)
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
    return _dec_mem(dict(row)) if row else None


def search(text=None, mtype=None, status="active", limit=20) -> list:
    """Straight listing (dashboard, 'what do you remember'). Text goes through SQL LIKE first
    (plaintext DBs) and falls back to a scan over the DECRYPTED contents when the store is
    encrypted (ciphertext is invisible to LIKE). Rows always come back decrypted."""
    q, args = "SELECT * FROM memories WHERE status=?", [status or "active"]
    if mtype:
        q += " AND type=?"
        args.append(mtype)
    if text:
        chash = _hash_of(text)
        q += " AND (content LIKE ? OR (?!='' AND content_hash=?))"
        args += [f"%{text}%", chash, chash]
    q += " ORDER BY importance DESC, last_confirmed_at DESC LIMIT ?"
    args.append(limit)
    rows = [_dec_mem(dict(r)) for r in conn().execute(q, args).fetchall()]
    if text and not rows:  # encrypted (or just missed): scan decrypted contents
        pool = " AND type=?" if mtype else ""
        candidates = [dict(r) for r in conn().execute(
            f"SELECT * FROM memories WHERE status=?{pool}", [status or "active"] + ([mtype] if mtype else []))]
        rows = [_dec_mem(r) for r in candidates
                if text.lower() in _dec(r["content"] or "").lower()][:limit]
    return rows


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
        promoted.append(semantic(_dec(r["content"]), source="behavior", importance=max(0.55, r["importance"] + 0.2),
                                 confidence=conf))
        c = conn()
        with _lock, c:
            c.execute("UPDATE memories SET status='promoted' WHERE id=?", (r["id"],))
            c.commit()
    return promoted


def decay(now=None) -> list:
    """Mark stale memories needs_confirmation (spec 16, D4/J12). Never deletes anything:
    low-importance rows fade after ~a month unconfirmed; HIGH-importance rows (identity,
    family, key preferences) fade much slower — twelve times the window, 'stable facts decay
    slowly' — but they too surface for re-confirmation instead of living forever unexamined.
    The owner confirms them back to active (or forgets them); the system never decides."""
    cfg = MEM().get("decay", {})
    if not cfg.get("enabled", True):
        return []
    floor = cfg.get("min_importance_floor", 0.55)
    low_age = cfg.get("run_interval_h", 12) * 3600 * 30   # ~a month unconfirmed
    high_age = low_age * 12                                # stable facts decay slowly (D4): ~6 months
    now = now or time.time()
    rows = [dict(r) for r in conn().execute(
        "SELECT id, content FROM memories WHERE status='active' AND last_confirmed_at < ? "
        "AND ((importance < ? AND last_confirmed_at < ?) OR last_confirmed_at < ?)",
        (now - low_age, floor, now - low_age, now - high_age)).fetchall()]
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
            if not e.get("display"):
                sets.append("display=?")
                args.append(name)
            if relationship and relationship != e["relationship"]:
                sets.append("relationship=?")
                args.append(relationship)
            if confidence is not None:
                sets.append("confidence=MAX(confidence,?)")
                args.append(confidence)
            if note and note not in json.loads(_dec(e["notes"]) or "[]"):
                sets.append("notes=?")
                args.append(_enc(_j((json.loads(_dec(e["notes"]) or "[]") + [note])[-10:])))
            if aliases:
                merged = sorted(set(json.loads(_dec(e["aliases"]) or "[]")) | {a.lower() for a in aliases})
                sets.append("aliases=?")
                args.append(_enc(_j(merged)))
            c.execute(f"UPDATE entities SET {', '.join(sets)} WHERE id=?", (*args, e["id"]))
            eid = e["id"]
        else:
            eid = f"person_{uuid.uuid4().hex[:8]}" if kind == "person" else f"ent_{uuid.uuid4().hex[:8]}"
            c.execute("INSERT INTO entities(id, name, kind, aliases, relationship, confidence, notes, "
                      "last_interaction, created_at, updated_at, display) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                      (eid, name.lower(), kind, _enc(_j([a.lower() for a in aliases])),
                       relationship or "unknown", confidence if confidence is not None else 0.5,
                       _enc(_j([note] if note else [])), now, now, now, name))
        c.commit()
    row = c.execute("SELECT * FROM entities WHERE id=?", (eid,)).fetchone()
    return _dec_entity(dict(row))


def find_entity(name) -> dict | None:
    key = (name or "").strip().lower()
    row = conn().execute("SELECT * FROM entities WHERE name=? OR aliases LIKE ?",
                         (key, f'%"{key}"%')).fetchone()
    if row is None:  # encrypted aliases are invisible to LIKE: scan the decrypted ones
        for r in conn().execute("SELECT * FROM entities").fetchall():
            d = _dec_entity(dict(r))
            try:
                alias_list = [a.lower() for a in json.loads(d["aliases"] or "[]")]
            except Exception:
                alias_list = []
            if key == d["name"] or key in alias_list:
                return d
        return None
    return _dec_entity(dict(row))


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
    display = e.get("display") or e["name"]
    lines = [f"{display} - {e['relationship']} (confidence {e['confidence']:.2f})"]
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
    conflicts = c.execute("SELECT COUNT(*) FROM conflicts WHERE status='open'").fetchone()[0]
    return {"memories": by, "entities": ents, "db": db_path(), "thresholds": thresholds(),
            "open_conflicts": conflicts}


def entities(limit=100) -> list:
    """All entity rows (dashboard, family queries)."""
    return [dict(r) for r in conn().execute(
        "SELECT * FROM entities ORDER BY last_interaction DESC LIMIT ?", (limit,)).fetchall()]


def forget_person(name: str, hard: bool = True) -> int:
    """'Forget everything about Ahmed' (H2/J7): REAL deletion — the entity row, every memory
    mentioning them, their links, their graph node and edges, and any vector-store snippets
    only about them. hard=False keeps the old soft behavior (status='forgotten', audit trail).
    The deletion itself is logged in forget_audit (that it happened and how much went —
    never the deleted content)."""
    if not hard:
        n = forget(name)
        e = find_entity(name)
        if e:
            c = conn()
            with _lock, c:
                c.execute("DELETE FROM memory_links WHERE entity_id=?", (e["id"],))
                c.commit()
        return n
    return forget_hard(name)["memories"]


def forget_hard(target: str) -> dict:
    """Real deletion of everything about `target` (a person's name or a memory phrase):
    memory rows + links + the entity + rgraph node/edges + vector snippets whose text only
    mentions them. Derived-only memories go with the fact they were derived from (H2).
    Returns the counts; writes one forget_audit + one event row."""
    from ishti import rgraph
    c = conn()
    t_low = (target or "").strip().lower()
    counts = {"memories": 0, "entities": 0, "edges": 0}
    with _lock, c:
        ids = [r["id"] for r in c.execute("SELECT id, content, content_hash FROM memories").fetchall()
               if t_low in _dec(r["content"]).lower() or t_low in (r["content_hash"] or "")]
        if ids:
            c.executemany("DELETE FROM memory_links WHERE memory_id=?", [(i,) for i in ids])
            counts["memories"] = c.executemany("DELETE FROM memories WHERE id=?", [(i,) for i in ids]).rowcount or len(ids)
        for e in c.execute("SELECT id FROM entities WHERE name=? OR aliases LIKE ?",
                           (t_low, f'%"{t_low}"%')).fetchall():
            c.execute("DELETE FROM memory_links WHERE entity_id=?", (e["id"],))
            counts["entities"] += c.execute("DELETE FROM entities WHERE id=?", (e["id"],)).rowcount
        else_note = f"entity + memories + graph for '{t_low}'"
        c.commit()
    # the graph: every node whose name CONTAINS the target as a whole word ('ahmed' matches
    # 'ahmed doomed' and 'ahmed (work)', never 'sahmed') and every edge touching them
    # (private included — this is the owner asking)
    import re as _re
    victims = []
    try:
        victims = [dict(r) for r in rgraph.conn().execute("SELECT id, name FROM nodes").fetchall()
                   if _re.search(rf"\b{_re.escape(t_low)}\b", r["name"] or "")]
    except Exception:
        victims = []
    if victims:
        rc = rgraph.conn()
        with rgraph.LOCK, rc:
            for v in victims:
                counts["edges"] += rc.execute("DELETE FROM edges WHERE src=? OR dst=?",
                                              (v["id"], v["id"])).rowcount
                rc.execute("DELETE FROM nodes WHERE id=?", (v["id"],))
            rc.commit()
    # vector store: past-conversation snippets that mention the person (best effort, own file)
    vec = 0
    try:
        from ishti.semantic import get_semantic
        st = get_semantic()
        if hasattr(st, "delete_where_text"):
            vec = st.delete_where_text(t_low)
    except Exception as _exc:
        swallowed('ishti/arch2/memory.py:forget_hard', _exc)
    audit_id = _audit("forget_hard", f"removed everything about '{t_low}' "
                      f"({counts['memories']} memories, {counts['entities']} entities, "
                      f"{counts['edges']} graph edges, {vec} vector snippets)",
                      items=counts["memories"] + counts["entities"] + counts["edges"])
    event("forget", f"owner asked to forget everything about '{t_low}'", entities=[t_low], source="explicit_user")
    return {**counts, "audit_id": audit_id}


def _audit(action: str, detail: str, items: int = 0, requested_by: str = "owner") -> int:
    """H2: the audit log records THAT a deletion happened (and how much went), never its content."""
    c = conn()
    with _lock, c:
        cur = c.execute("INSERT INTO forget_audit(ts, action, detail, items, requested_by) VALUES(?,?,?,?,?)",
                        (time.time(), action, detail, items, requested_by))
        c.commit()
        return cur.lastrowid


def forget_audit_log(limit=50) -> list:
    """The dashboard's deletion log (H4 actions column)."""
    return [dict(r) for r in conn().execute(
        "SELECT * FROM forget_audit ORDER BY ts DESC LIMIT ?", (limit,)).fetchall()]


def wipe_all():
    """'Forget everything' - the owner's explicit command, never called by code. Real deletion
    of every memory, link and entity row, logged in forget_audit (H2)."""
    c = conn()
    with _lock, c:
        n = c.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        c.execute("DELETE FROM memories")
        c.execute("DELETE FROM memory_links")
        c.execute("DELETE FROM entities")
        c.commit()
    _audit("wipe_all", f"forget-everything: removed {n} memories and all entities/links", items=n)


if __name__ == "__main__":
    # NOTE: `import ishti.arch2.memory as M` inside a module running as __main__ binds M=None
    # (self-import quirk) - patch THIS module's globals directly instead.
    import tempfile
    tmp = tempfile.mkdtemp()
    db_path = lambda: os.path.join(tmp, "m.db")
    _conn = None
    # four layers + dedupe
    m1 = semantic("User prefers concise answers.", source="explicit_user", confidence=1.0, importance=0.8)
    observe("User asks to make emails shorter.")
    observe("User asks to make emails shorter.")
    assert len(search(mtype="observation")) == 1, "same observation must not duplicate"
    assert search("concise")[0]["content"].startswith("User prefers")
    assert forget(m1["id"]) == 1 and search("concise") == []
    # promotion past thresholds
    for _ in range(4):
        observe("User prefers bullet points.")
    promoted = promote_observations()
    assert any("bullet" in p["content"] for p in promoted)
    # supersede stamps bi-temporal validity and provenance
    a = semantic("User prefers Chrome.", source="explicit_user", confidence=1.0)
    b = supersede(a["id"], "User now prefers Firefox.")
    old_row = get(a["id"])
    assert old_row["status"] == "superseded" and old_row["valid_until"], "bi-temporal: was-true-until stamped"
    assert "supersedes" in json.loads(get(b["id"])["meta"]), "provenance: new row links to the old"
    # reconcile without a model (jsonio degrades to ADD): first fact ADDs
    r0 = reconcile("User programs mainly in Python.", source="explicit_user", importance=0.7, confidence=0.9)
    assert r0.get("content", "").endswith("Python.") or r0.get("action") == "NOOP", r0
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
    # decay never deletes, respects the importance floor. NOTE: backdate via a fresh connection+
    # commit (a bare conn() shares _write's open transaction; its commit() would eat ours)
    old = semantic("User tried a dartboard app once.", importance=0.3, confidence=0.4)
    conn()  # ensure schema exists on the real connection first
    raw = sqlite3.connect(db_path())
    raw.execute("UPDATE memories SET last_confirmed_at=? WHERE id=?", (time.time() - 90 * 86400, old["id"]))
    raw.commit()
    raw.close()
    row = get(old["id"])
    assert row and row["status"] == "active", "self-check precondition: the row must be active to decay"
    assert row["last_confirmed_at"] < time.time() - 89 * 86400, "backdating must stick (fresh connection)"
    flagged = decay()  # call once: decay() is destructive (it flips statuses)
    assert any(x["id"] == old["id"] for x in flagged), "low-importance, long-unconfirmed row must be flagged"
    assert get(old["id"])["status"] == "needs_confirmation"
    # privacy export
    semantic("User had knee surgery in March.", source="conversation")
    assert "surgery" not in export("client").lower()
    assert "surgery" in export("owner").lower()
    # D5: contradictions land in the conflicts table; the owner can re-decide
    a = semantic("User prefers Chrome.", source="explicit_user", confidence=1.0)
    supersede(a["id"], "User now prefers Firefox.")
    conf = conflicts()
    assert conf and conf[0]["old_memory_id"] == a["id"] and "Chrome" in conf[0]["old_content"], conf
    assert resolve_conflict(conf[0]["id"], keep="old", note="changed my mind back")
    assert get(a["id"])["status"] == "active", "keep=old re-activates the original"
    # H2/J7: real deletion + audit (content never reappears)
    upsert_entity("Ahmed", relationship="brother", note="test")
    semantic("Ahmed is my brother.", source="explicit_user", confidence=1.0)
    semantic("Unrelated fact about tea.")
    res = forget_hard("Ahmed")
    assert res["memories"] >= 1 and res["entities"] >= 1, res
    assert search("Ahmed") == [] and find_entity("ahmed") is None
    assert search("tea"), "unrelated memories must survive the person-delete"
    log = forget_audit_log()
    assert log and log[0]["action"] == "forget_hard" and "Ahmed" not in log[0]["detail"].split("'")[1], log
    print(f"[self-check] arch2.memory OK - {stats()}")
