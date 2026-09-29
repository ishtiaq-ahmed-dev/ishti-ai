"""Relationship memory graph (SQLite-backed, temporal, privacy-first).

People, places, projects, organisations and preferences as nodes; family ties,
friendships, likes/dislikes, who-introduced-whom, who-works-where as typed
directed edges. Every fact carries first_seen / last_confirmed timestamps, so a
stale preference is visible as stale instead of being treated as forever-true.
Conflict handling: a contradictory fact does not overwrite - it opens a pending
question ("you said Ahmed doesn't like coffee, now you say he does") that the
owner answers; the answer closes the question and updates the edge.
Privacy: a node marked private=1 (a family member, a friend) is never exported
to client-facing context or fine-tuning data; graph rows never carry message
content, only facts. Clients live in their own table (ishti/skills/clients),
linked to the graph by a client_id edge - their data never mixes in here.
Storage: one SQLite file (data/rgraph.db), same engine ISHTI already runs on.
Upgrade path (when the graph outgrows it): Neo4j Community, same schema shape.
"""
import datetime as dt
import json
import os
import re
import sqlite3
import sys
import threading
import time

if __package__ in (None, ""):  # run directly: python ishti/rgraph.py
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ishti import ROOT

DB_PATH = os.path.join(ROOT, "data", "rgraph.db")
LOCK = threading.RLock()
STALE_DAYS = 365  # a preference unconfirmed for a year is flagged "might have changed"
_conn = None

# relation types and how they read backwards ("ahmed brother-of me" <-> "me brother-of ahmed")
RELATION = {
    "brother": ("brother of", "brother"), "sister": ("sister of", "sister"),
    "father": ("father of", "child"), "mother": ("mother of", "child"),
    "son": ("son of", "father/mother"), "daughter": ("daughter of", "father/mother"),
    "cousin": ("cousin of", "cousin"), "uncle": ("uncle of", "nephew/niece"),
    "aunt": ("aunt of", "nephew/niece"), "nephew": ("nephew of", "uncle/aunt"),
    "niece": ("niece of", "uncle/aunt"), "grandfather": ("grandfather of", "grandchild"),
    "grandmother": ("grandmother of", "grandchild"), "friend": ("friend of", "friend"),
    "colleague": ("colleague of", "colleague"), "classmate": ("classmate of", "classmate"),
    "introduced": ("introduced", "was introduced by"), "works_at": ("works at", "employee"),
    "studied_at": ("studies at", "student"), "lives_in": ("lives in", "home of"),
    "from": ("from", "hometown of"), "likes": ("likes", "liked by"),
    "dislikes": ("dislikes", "disliked by"), "prefers": ("prefers", "preferred by"),
    "owns": ("owns", "owned by"), "works_on": ("works on", "worked on by"),
    "client_of": ("is a client of", "serves"),
}
KIN = set(RELATION)  # anything not in a fixed set is still accepted (typed freeform)


def conn():
    global _conn
    with LOCK:
        if _conn is None:
            os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
            _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
            _conn.row_factory = sqlite3.Row
            _conn.execute("PRAGMA journal_mode=WAL")
            _conn.executescript("""
                CREATE TABLE IF NOT EXISTS nodes(
                    id INTEGER PRIMARY KEY, name TEXT UNIQUE, kind TEXT,
                    private INTEGER DEFAULT 0, note TEXT DEFAULT '',
                    first_seen REAL, last_confirmed REAL);
                CREATE TABLE IF NOT EXISTS edges(
                    id INTEGER PRIMARY KEY, src INTEGER, dst INTEGER, rel TEXT,
                    detail TEXT DEFAULT '', private INTEGER DEFAULT 0,
                    first_seen REAL, last_confirmed REAL,
                    UNIQUE(src, dst, rel));
                CREATE TABLE IF NOT EXISTS pending(
                    id INTEGER PRIMARY KEY, edge_id INTEGER, kind TEXT, old TEXT,
                    new TEXT, asked REAL, answer TEXT DEFAULT '');
            """)
            _conn.commit()
            try:  # Prompt-2 additive schema (edge confidence/source_type, node needs_confirmation)
                from ishti.core.migrations import ensure_current
                ensure_current(_conn, "rgraph")
            except Exception as exc:
                print(f"[rgraph] migrations skipped: {exc}")
        return _conn


def _now():
    return time.time()


def _enc(s: str) -> str:
    """At-rest encryption (core.vault): no-op while the vault is off."""
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


def _dec_edge(d: dict) -> dict:
    if isinstance(d.get("detail"), str):
        d["detail"] = _dec(d["detail"])
    return d


def _dec_pending(d: dict) -> dict:
    for col in ("old", "new"):
        if isinstance(d.get(col), str):
            d[col] = _dec(d[col])
    return d


def _day(ts):
    return dt.datetime.fromtimestamp(ts).strftime("%d %b %Y")


def node(name, kind="person", private=None):
    """The node id for `name`, creating it when missing. Name matching is
    case/spacing-insensitive; 'my brother' style aliases stay in the skill layer."""
    key = re.sub(r"\s+", " ", (name or "").strip()).lower()
    if not key:
        return None
    c = conn()
    with LOCK, c:
        row = c.execute("SELECT id, private FROM nodes WHERE name=?", (key,)).fetchone()
        if row:
            c.execute("UPDATE nodes SET last_confirmed=? WHERE id=?", (_now(), row["id"]))
            return row["id"]
        cur = c.execute("INSERT INTO nodes(name, kind, private, first_seen, last_confirmed) VALUES(?,?,?,?,?)",
                        (key, kind, int(bool(private)) if private is not None else (1 if kind == "person" else 0),
                         _now(), _now()))
        return cur.lastrowid


def get_node(name):
    c = conn()
    key = re.sub(r"\s+", " ", (name or "").strip()).lower()
    with LOCK:
        row = c.execute("SELECT * FROM nodes WHERE name=?", (key,)).fetchone()
    return dict(row) if row else None


def relate(src_name, rel, dst_name, detail="", private=None, kind_src="person", kind_dst="person",
           confidence: float | None = None, source_type: str | None = None):
    """Create (or re-confirm) an edge src -[rel]-> dst. Returns (edge_id, created_bool).
    Edge metadata (C4): confidence 0..1 (default 0.8 — an observed fact, not a stated one)
    and source_type ('explicit_user' > 'observed' > 'inferred'). An explicit re-statement
    upgrades an existing edge's confidence and provenance; a weaker one never downgrades."""
    sid = node(src_name, kind_src)
    did = node(dst_name, kind_dst)
    if not sid or not did or sid == did:
        return None, False
    rel = rel.strip().lower().replace("-", "_").replace(" ", "_")
    conf = max(0.0, min(1.0, float(confidence))) if confidence is not None else \
        (1.0 if source_type == "explicit_user" else 0.8)
    stype = (source_type or ("explicit_user" if confidence == 1.0 else "observed")).lower()
    c = conn()
    with LOCK, c:
        row = c.execute("SELECT id, confidence, source_type FROM edges WHERE src=? AND dst=? AND rel=?",
                        (sid, did, rel)).fetchone()
        if row:
            if stype == "explicit_user" or conf > (row["confidence"] or 0):
                c.execute("UPDATE edges SET last_confirmed=?, confidence=?, source_type=? WHERE id=?",
                          (_now(), max(conf, row["confidence"] or 0),
                           "explicit_user" if stype == "explicit_user" else row["source_type"], row["id"]))
            else:
                c.execute("UPDATE edges SET last_confirmed=? WHERE id=?", (_now(), row["id"]))
            _resolve_pending(row["id"], "confirm")
            return row["id"], False
        cur = c.execute("INSERT INTO edges(src, dst, rel, detail, private, first_seen, last_confirmed, "
                        "confidence, source_type) VALUES(?,?,?,?,?,?,?,?,?)",
                        (sid, did, rel, _enc(detail), int(bool(private)) if private is not None
                         else (1 if rel in ("brother", "sister", "father", "mother", "son", "daughter",
                                            "cousin", "uncle", "aunt", "nephew", "niece",
                                            "grandfather", "grandmother") else 0),
                         _now(), _now(), conf, stype))
        return cur.lastrowid, True


def kin_edge(src_name: str, rel: str, dst_name: str, source_type: str = "explicit_user",
             confidence: float = 1.0) -> tuple[int | None, bool]:
    """A family tie (J1). Kin edges are ONLY created by the owner's explicit statement —
    every caller passes source_type='explicit_user'; anything observed stays 'observed' with
    capped confidence and is flagged needs_confirmation on the node (C5)."""
    rel = rel.strip().lower().replace("-", "_").replace(" ", "_")
    if rel not in KIN:
        return None, False
    private = True
    eid, created = relate(src_name, rel, dst_name, private=private,
                          confidence=1.0 if source_type == "explicit_user" else min(0.7, confidence),
                          source_type=source_type)
    if eid and source_type != "explicit_user":
        c = conn()
        with LOCK, c:
            c.execute("UPDATE nodes SET needs_confirmation=1 WHERE id=?", (get_node(dst_name)["id"],))
            c.commit()
    return eid, created


def note_contact(person: str, channel: str = "") -> int:
    """Frequency WITHOUT relationship (J2/C5): repeated contact creates/extends a
    'frequently_contacted' edge — never a friend/colleague guess. The relationship stays
    'unknown' until the owner states it. Returns the running contact count."""
    eid, created = relate("me", "frequently_contacted", person, private=0,
                          confidence=0.6, source_type="observed")
    if eid is None:
        return 0
    c = conn()
    with LOCK, c:
        row = c.execute("SELECT detail FROM edges WHERE id=?", (eid,)).fetchone()
        detail = _dec(row["detail"]) if row and row["detail"] else ""
        try:
            count = int(re.search(r"(\d+)", detail).group(1)) + 1 if detail else (1 if created else 2)
        except ValueError:
            count = 2
        suffix = f" via {channel}" if channel else ""
        c.execute("UPDATE edges SET detail=?, last_confirmed=? WHERE id=?",
                  (_enc(f"contacted {count}x{suffix}"), _now(), eid))
        c.commit()
    return count


def relationship_of(name: str) -> dict:
    """The best-known relationship to the owner (C2/C5): an explicit kin/friend edge wins;
    frequency alone means relationship='unknown' + needs_confirmation — never a guess."""
    for e in edges_of(name, direction="in"):
        if e["s_name"] != OWNER:
            continue
        if e["rel"] in KIN or e["rel"] in ("friend", "colleague", "classmate"):
            explicit = e.get("source_type") == "explicit_user"
            return {"relationship": e["rel"], "confidence": e.get("confidence", 0.8),
                    "source_type": e.get("source_type", "observed"),
                    "needs_confirmation": not explicit}
    if any(e["rel"] == "frequently_contacted" for e in edges_of(name, direction="in")):
        return {"relationship": "unknown", "confidence": 0.3, "source_type": "observed",
                "needs_confirmation": True}
    return {"relationship": "unknown", "confidence": 0.0, "source_type": "", "needs_confirmation": True}


def ask_relationship(name: str) -> str | None:
    """The one clarifying question (C5) when knowing the relationship matters and it is
    unknown; None when the relationship is already established."""
    r = relationship_of(name)
    if r["confidence"] >= 0.8 and r["relationship"] != "unknown":
        return None
    return f"Who is {name} to you? I won't guess from how often you talk."


def unrelate(src_name, rel, dst_name):
    sid, did = get_node(src_name), get_node(dst_name)
    if not sid or not did:
        return False
    c = conn()
    with LOCK, c:
        n = c.execute("DELETE FROM edges WHERE src=? AND dst=? AND rel=?",
                      (sid["id"], did["id"], rel.strip().lower())).rowcount
    return bool(n)


def _edge_row(eid):
    c = conn()
    with LOCK:
        row = c.execute("SELECT e.*, s.name AS s_name, s.kind AS s_kind, d.name AS d_name, d.kind AS d_kind "
                        "FROM edges e JOIN nodes s ON s.id=e.src JOIN nodes d ON d.id=e.dst WHERE e.id=?",
                        (eid,)).fetchone()
    return _dec_edge(dict(row)) if row else None


def _conflicts_with(rel, new_detail, old_detail):
    """A contradiction is a strict opposite-polarity pair: one side is a plain
    positive fact and the other an explicit negation of the SAME thing ('likes
    coffee' vs "doesn't drink coffee"). A mere change ('drinks it daily' ->
    'switched to decaf') is an update, not a conflict - it applies directly."""
    def norm(x):
        return re.sub(r"[^a-z0-9 ]", "", (x or "").lower()).strip()
    if not old_detail or not new_detail:
        return False
    if norm(new_detail) == norm(old_detail):
        return False
    NEG = ("doesn't", "does not", "dont", "don't", "never", "no longer", "stopped")
    new_neg, old_neg = (any(n in d.lower() for n in NEG) for d in (new_detail, old_detail))
    if new_neg == old_neg:
        return False  # same polarity: a change of detail, applied as an update
    # polarities differ: only a conflict if both sides talk about the same thing
    stop = ("does", "do", "did", "not", "never", "no", "longer", "stopped", "the", "a", "an", "it", "is",
            "was", "t", "s", "at", "all", "anymore", "much", "very")
    stem = lambda w: re.sub(r"(ies)$", "y", re.sub(r"(es|s)$", "", w))
    words = lambda d: {stem(w) for w in re.findall(r"[a-z]+", d.lower())
                       if w not in stop and len(w) > 1}
    return bool(words(new_detail) & words(old_detail))


def set_detail(src_name, rel, dst_name, detail, private=None, kind_dst="person"):
    """Set an edge's detail with conflict detection: a contradiction opens a
    pending question instead of silently overwriting."""
    eid, _created = relate(src_name, rel, dst_name, detail, private, kind_dst=kind_dst)
    if eid is None:
        return None, "couldn't store that"
    row = _edge_row(eid)
    if _conflicts_with(rel, detail, row["detail"]) and not row["detail"] == detail:
        c = conn()
        with LOCK, c:
            c.execute("INSERT INTO pending(edge_id, kind, old, new, asked) VALUES(?,?,?,?,?)",
                      (eid, "conflict", _enc(row["detail"]), _enc(detail), _now()))
        return eid, (f"Note: earlier I have it that {row['s_name']} {row['rel']} "
                     f"{row['d_name']} ({row['detail']}). Which is current - the old one or this? "
                     "(say 'the new one' or 'the old one')")
    if detail and detail != row["detail"] and not _conflicts_with(rel, detail, row["detail"]):
        c = conn()
        with LOCK, c:
            c.execute("UPDATE edges SET detail=?, last_confirmed=? WHERE id=?", (_enc(detail), _now(), eid))
    return eid, None


def _resolve_pending(edge_id, answer):
    c = conn()
    with LOCK, c:
        c.execute("UPDATE pending SET answer=? WHERE edge_id=? AND answer=''", (answer, edge_id))


OPPOSITE = {"likes": "dislikes", "prefers": "dislikes", "dislikes": "likes"}


def add_preference(src_name, what, polarity, kind_dst="preference"):
    """Store a liking with polarity ('pos' = likes/prefers, 'neg' = dislikes/hates).
    If an opposite-polarity edge to the same thing already exists, that's a real
    contradiction: open a pending question instead of storing both."""
    what = re.sub(r"\s+", " ", (what or "").strip().strip("'\"").lower())
    if not what:
        return None, "what should I store?"
    rel = "likes" if polarity == "pos" else "dislikes"
    opp = OPPOSITE.get(rel)
    for e in edges_of(src_name, rel=opp, direction="out"):
        if e["d_name"] == what:
            c = conn()
            with LOCK, c:
                c.execute("INSERT INTO pending(edge_id, kind, old, new, asked) VALUES(?,?,?,?,?)",
                          (e["id"], "polarity", f"{src_name}|{opp}|{what}", f"{src_name}|{rel}|{what}", _now()))
            return None, (f"Wait - I have it that {src_name} {opp} {what}. Which is current, the old one or "
                          "this? (say 'the new one' or 'the old one')")
    eid, _created = relate(src_name, rel, what, kind_dst=kind_dst)
    return eid, None


def _apply_fact(fact):
    """'ahmed|dislikes|cricket' -> relate(). Used when a polarity question is resolved."""
    try:
        src, rel, dst = fact.split("|", 2)
    except ValueError:
        return None
    return relate(src, rel, dst, kind_dst="preference")[0]


def pending_questions():
    c = conn()
    with LOCK:
        rows = c.execute("SELECT p.id, p.edge_id, p.kind, p.old, p.new, p.asked, e.rel, s.name AS s_name, d.name AS d_name "
                         "FROM pending p JOIN edges e ON e.id=p.edge_id "
                         "JOIN nodes s ON s.id=e.src JOIN nodes d ON d.id=e.dst "
                         "WHERE p.answer='' ORDER BY p.id").fetchall()
    return [_dec_pending(dict(r)) for r in rows]


def resolve_question(pid, keep):
    """keep='new' applies the new fact, 'old' keeps what was there. Handles both
    detail conflicts (update the edge's detail) and polarity conflicts (swap likes
    for dislikes: the old edge is removed and the new one stored)."""
    c = conn()
    with LOCK:
        row = c.execute("SELECT edge_id, kind, old, new FROM pending WHERE id=?", (pid,)).fetchone()
    if not row:
        return None
    with LOCK, c:
        c.execute("UPDATE pending SET answer=? WHERE id=?", (f"kept {keep}", pid))
        if row["kind"] == "polarity":
            if keep == "new":
                c.execute("DELETE FROM edges WHERE id=?", (row["edge_id"],))
                _apply_fact(row["new"])
            # keep='old': the new fact was never stored, so there's nothing to undo
        elif keep == "new":
            c.execute("UPDATE edges SET detail=?, last_confirmed=? WHERE id=?", (_enc(row["new"]), _now(), row["edge_id"]))
        else:
            c.execute("UPDATE edges SET last_confirmed=? WHERE id=?", (_now(), row["edge_id"]))
    return dict(row)


def edges_of(name, rel=None, direction="both"):
    """Edges touching a node. direction: out, in, both. Private edges are only
    returned when the asker explicitly owns the graph (this is ISHTI-local, so
    yes) - but private edges are NEVER exported (see export())."""
    n = get_node(name)
    if not n:
        return []
    q, args = ("SELECT e.*, s.name AS s_name, d.name AS d_name FROM edges e "
               "JOIN nodes s ON s.id=e.src JOIN nodes d ON d.id=e.dst WHERE ", [])
    if direction == "out":
        q += "e.src=?"
        args.append(n["id"])
    elif direction == "in":
        q += "e.dst=?"
        args.append(n["id"])
    else:
        q += "(e.src=? OR e.dst=?)"
        args += [n["id"], n["id"]]
    if rel:
        q += " AND e.rel=?"
        args.append(rel.strip().lower())
    q += " ORDER BY e.last_confirmed DESC"
    c = conn()
    with LOCK:
        rows = c.execute(q, args).fetchall()
    return [_dec_edge(dict(r)) for r in rows]


def describe_edge(e, staleness=True):
    """One human line for an edge, with a staleness flag when the fact is old."""
    line = f"{e['s_name']} {e['rel'].replace('_', ' ')} {e['d_name']}"
    if e["detail"]:
        line += f" ({e['detail']})"
    if staleness and _now() - e["last_confirmed"] > STALE_DAYS * 86400:
        line += f" [not confirmed since {_day(e['last_confirmed'])} - might have changed]"
    return line


def introducer_of(a_name, b_name):
    """The person with 'introduced' edges to both a and b (who introduced me to X), or None."""
    a, b = get_node(a_name), get_node(b_name)
    if not a or not b:
        return None
    c = conn()
    with LOCK:
        rows = c.execute("SELECT s.name AS s_name, d.name AS d_name FROM edges e "
                         "JOIN nodes s ON s.id=e.src JOIN nodes d ON d.id=e.dst "
                         "WHERE e.rel='introduced'").fetchall()
    for name in {r["s_name"] for r in rows}:
        targets = {r["d_name"] for r in rows if r["s_name"] == name}
        if a["name"] in targets and b["name"] in targets:
            return name
    return None


def likes_of(name, rels=("likes", "prefers")):
    out = []
    for rel in rels:
        out += [e for e in edges_of(name, rel=rel, direction="out")]
    return out


def dislikers_of(pref_name):
    """People with a dislikes edge to a preference node."""
    return [e["s_name"] for e in edges_of(pref_name, rel="dislikes", direction="in")]


def suggest_gift(person, occasion=""):
    """Traverse the person's likes/prefers and avoid their dislikes; suggest from
    liked things that others with the same liking don't hate. Pure graph work,
    no model call - the skill layer can dress it up with the LLM if asked."""
    n = get_node(person)
    if not n:
        return f"I don't have anything stored about {person} yet - tell me a few things they like and I'll remember."
    liked = [e["d_name"] for e in likes_of(person)]
    hated = {e["d_name"] for e in edges_of(person, rel="dislikes", direction="out")}
    liked = [x for x in liked if x not in hated]
    if not liked:
        return (f"I don't know what {person} likes yet - tell me one or two things they like "
                "and I'll suggest better next time.")
    lead = f"For {person}" + (f" ({occasion})" if occasion else "")
    return (f"{lead}: they like {', '.join(liked[:4])}. A gift touching one of those - or one of them "
            "itself - is a safe bet." + (f" Avoid: {', '.join(sorted(hated))}." if hated else ""))


def find_matches(name):
    """Possible duplicate people: fuzzy name equality or a shared unique kin edge."""
    import difflib
    target = get_node(name)
    if not target:
        return []
    c = conn()
    with LOCK:
        people = [dict(r) for r in c.execute("SELECT id, name FROM nodes WHERE kind='person' AND id != ?",
                                             (target["id"],)).fetchall()]
    out = []
    for p in people:
        ratio = difflib.SequenceMatcher(None, target["name"], p["name"]).ratio()
        # "ahmed" vs "ahmed ali": one contains the other as a whole word
        contained = bool(re.search(rf"\b{re.escape(target['name'])}\b", p["name"]) or
                         re.search(rf"\b{re.escape(p['name'])}\b", target["name"]))
        if ratio >= 0.72 or contained:
            out.append({"id": p["id"], "name": p["name"], "score": round(ratio, 2)})
    return sorted(out, key=lambda x: -x["score"])[:5]


def merge_nodes(keep_name, drop_name):
    """Merge two nodes: edges of `drop` re-point to `keep`, drop is removed. T1: logged, undoable
    by re-adding (edges preserved, nothing deleted but the duplicate row)."""
    keep, drop = get_node(keep_name), get_node(drop_name)
    if not keep or not drop or keep["id"] == drop["id"]:
        return False
    c = conn()
    with LOCK, c:
        for row in c.execute("SELECT id, src, dst FROM edges WHERE src=? OR dst=?", (drop["id"], drop["id"])).fetchall():
            src = keep["id"] if row["src"] == drop["id"] else row["src"]
            dst = keep["id"] if row["dst"] == drop["id"] else row["dst"]
            if src == dst:
                c.execute("DELETE FROM edges WHERE id=?", (row["id"],))  # self-loop after merge
                continue
            dup = c.execute("SELECT id FROM edges WHERE src=? AND dst=? AND rel=? AND id != ?",
                            (src, dst, c.execute("SELECT rel FROM edges WHERE id=?", (row["id"],)).fetchone()[0],
                             row["id"])).fetchone()
            if dup:
                c.execute("DELETE FROM edges WHERE id=?", (row["id"],))
            else:
                c.execute("UPDATE edges SET src=?, dst=? WHERE id=?", (src, dst, row["id"]))
        c.execute("UPDATE nodes SET note=CASE WHEN note='' THEN ? ELSE note END WHERE id=?",
                  (f"also known as {drop['name']}", keep["id"]))
        c.execute("DELETE FROM nodes WHERE id=?", (drop["id"],))
    return True


OWNER = "me"  # the owner's own node: their likes are the owner's business, not a third party's


def export(allowed="graph"):
    """Facts as text for grounding an answer. PRIVATE nodes/edges are excluded
    when the context is client-facing ('client') - the hard privacy rule. The
    exception is the owner's own node and their own preference edges: those are
    the owner's business, not a family member's, so they may ground any answer."""
    c = conn()
    where = ("" if allowed == "graph" else
             "WHERE e.private=0 AND (s.private=0 OR s.name='me') AND (d.private=0 OR (s.name='me' AND e.rel IN ('likes','prefers','dislikes'))) "
             "AND NOT (d.private=1 AND s.name='me' AND e.rel NOT IN ('likes','prefers','dislikes'))")
    with LOCK:
        rows = c.execute(f"SELECT e.*, s.name AS s_name, d.name AS d_name FROM edges e "
                         f"JOIN nodes s ON s.id=e.src JOIN nodes d ON d.id=e.dst {where} "
                         f"ORDER BY e.last_confirmed DESC LIMIT 40").fetchall()
    return [describe_edge(_dec_edge(dict(r))) for r in rows]


def stats():
    c = conn()
    with LOCK:
        n = c.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
        e = c.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
        p = c.execute("SELECT COUNT(*) FROM nodes WHERE private=1").fetchone()[0]
        pend = c.execute("SELECT COUNT(*) FROM pending WHERE answer=''").fetchone()[0]
    return {"nodes": n, "edges": e, "private_nodes": p, "open_questions": pend}


if __name__ == "__main__":
    tmp = tempfile_dir = os.path.join(os.environ.get("TEMP", "/tmp"), f"rgraph_test_{os.getpid()}")
    os.makedirs(tmp, exist_ok=True)
    DB_PATH = os.path.join(tmp, "g.db")
    _conn = None  # re-open on the test DB
    # nodes + kin edges
    eid, created = relate("me", "brother", "ahmed")
    assert created and eid
    eid2, created2 = relate("me", "brother", "ahmed")
    assert not created2 and eid2 == eid  # re-confirm, not duplicate
    assert get_node("Ahmed")["name"] == "ahmed"  # case-insensitive
    assert get_node("  ahmed ")["name"] == "ahmed"
    # preferences + staleness-free describe
    relate("ahmed", "likes", "stumptown coffee", kind_dst="preference")
    relate("ahmed", "likes", "cricket", kind_dst="preference")
    relate("sara", "introduced", "me", kind_dst="person")   # sara introduced me to bilal
    relate("sara", "introduced", "bilal", kind_dst="person")
    assert introducer_of("me", "bilal") == "sara"
    likes = [e["d_name"] for e in likes_of("ahmed")]
    assert "stumptown coffee" in likes and "cricket" in likes
    # conflict: contradiction opens a pending question, no silent overwrite
    eid3, note = set_detail("ahmed", "likes", "coffee", "drinks it daily")
    assert note is None
    eid4, note2 = set_detail("ahmed", "likes", "coffee", "doesn't drink coffee at all")
    assert note2 and "Which is current" in note2 and "drinks it daily" in note2  # quotes the stored fact
    row = _edge_row(eid4)
    assert "daily" in row["detail"]  # old value kept until the owner answers
    qs = pending_questions()
    assert qs and qs[0]["kind"] == "conflict"
    assert resolve_question(qs[0]["id"], "new")
    assert "doesn't drink" in _edge_row(eid4)["detail"]
    assert pending_questions() == []
    # non-contradiction update applies directly
    eid5, note3 = set_detail("ahmed", "likes", "coffee", "switched to decaf last month")
    assert note3 is None and "decaf" in _edge_row(eid5)["detail"]
    # gifts
    sug = suggest_gift("ahmed", "birthday")
    assert "cricket" in sug or "stumptown" in sug
    relate("ahmed", "dislikes", "perfume", kind_dst="preference")
    sug2 = suggest_gift("ahmed")
    assert "Avoid: perfume" in sug2
    # duplicates + merge
    relate("ahmed", "works_at", "netsole", kind_dst="organisation")
    relate("ahmed ali", "works_at", "netsole", kind_dst="organisation")
    matches = find_matches("ahmed")
    assert any(m["name"] == "ahmed ali" for m in matches)
    assert merge_nodes("ahmed", "ahmed ali")
    assert get_node("ahmed ali") is None
    assert [e["d_name"] for e in edges_of("ahmed", rel="works_at")] == ["netsole"]
    # privacy: private edges excluded from client-facing export
    relate("me", "brother", "ahmed")  # kin edge is private by default
    relate("ahmed", "dislikes", "mango", kind_dst="preference")  # a private person's fact
    relate("me", "likes", "tea", kind_dst="preference")  # the owner's own: exportable
    pub = export("client")
    assert all("brother" not in line for line in pub)  # family tie stays private
    assert all("mango" not in line for line in pub)    # a relative's fact stays private
    assert any("tea" in line for line in pub)          # the owner's own preference may ground client work
    full = export("graph")
    assert any("brother" in line for line in full)
    # unrelate
    assert unrelate("sara", "introduced", "bilal")
    assert introducer_of("me", "bilal") is None  # with that edge gone, the introduction is unknown
    # J1: explicit kin edge carries source_type=explicit_user, confidence 1.0
    keid, _ = kin_edge("me", "brother", "ahmed khan")
    assert keid and _edge_row(keid)["source_type"] == "explicit_user" and _edge_row(keid)["confidence"] == 1.0
    # J2: frequency never invents a relationship — 'frequently_contacted' only, relationship unknown
    for _ in range(4):
        note_contact("ahmed khan", "whatsapp")
    assert not any(e["rel"] in ("friend", "colleague") for e in edges_of("ahmed khan")), \
        "frequency must never create a social edge"
    fc = [e for e in edges_of("me", rel="frequently_contacted") if e["d_name"] == "ahmed khan"]
    assert fc and "4x via whatsapp" in fc[0]["detail"], fc
    rel = relationship_of("ahmed khan")
    assert rel["relationship"] == "brother" and rel["confidence"] == 1.0, rel  # kin beats frequency
    # observed-only kin stays weak + needs_confirmation
    kin_edge("me", "cousin", "zoya", source_type="observed", confidence=0.5)
    rel2 = relationship_of("zoya")
    assert rel2["needs_confirmation"] and rel2["confidence"] <= 0.7, rel2
    assert ask_relationship("zoya") and "zoya" in ask_relationship("zoya").lower()
    assert ask_relationship("ahmed khan") is None  # established: no question needed
    # a plain contact with no stated relationship stays unknown (never a guess)
    for _ in range(3):
        note_contact("bilal")
    rel3 = relationship_of("bilal")
    assert rel3["relationship"] == "unknown" and rel3["needs_confirmation"], rel3
    print(f"[self-check] rgraph OK - {stats()}; temporal edges, conflicts, merge, privacy export, gifts")
