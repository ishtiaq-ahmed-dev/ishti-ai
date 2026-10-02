"""Person records (C2) + identity disambiguation (C5, J3).

Two jobs:

1. record() — the full person entity the spec asks for: entity row + relationship (from
   rgraph, with confidence and needs_confirmation) + contact methods + preferences +
   important facts + edges + related memories + last interaction.

2. Identity: names are NOT identities. "Ahmed — friend", "Ahmed — university" and
   "Ahmed — work" stay separate people until the OWNER merges them; resolve() picks the
   right one from context (email, phone, organisation, relationship, channel) or asks.
   Disambiguators score candidates; only a unique, clearly-best match is auto-chosen —
   otherwise the owner gets one question naming the candidates.

Same-named people are stored as qualified entity rows ("ahmed (work)") carrying the
plain name in aliases, so "who is ahmed" finds all of them and the graph keeps their
edges apart. Nothing is ever merged automatically.
"""
from __future__ import annotations

import json
import re
import time

from ishti.arch2 import memory

try:  # best-effort failure log (never breaks the module if logging is unavailable)
    from ishti.core.logutil import swallowed
except Exception:  # pragma: no cover
    def swallowed(*_a, **_k):
        return None


CHANNEL_ALIASES = {"whatsapp": "messaging", "telegram": "messaging", "sms": "messaging",
                   "message": "messaging", "email": "email", "mail": "email"}
_STRONG = ("email", "phone", "org")     # disambiguators strong enough to pick alone
_WEAK = ("relation", "channel")         # corroborate a strong hit, never decide alone


# --------------------------------------------------------------------- the record
def record(name: str) -> dict:
    """The C2 person record for `name` ({} when unknown). Everything decrypted."""
    e = memory.find_entity(name)
    if not e:
        return {}
    from ishti import rgraph
    display = e.get("display") or e["name"]
    rel = rgraph.relationship_of(e["name"])
    # something to confirm = an unconfirmed IDENTITY, or a WEAK relationship claim (observed
    # kin/friend). An honestly-unknown relationship on a confirmed identity asks for nothing.
    weak_rel_claim = rel["relationship"] not in ("unknown", "") and rel["source_type"] != "explicit_user"
    needs_confirm = bool(e.get("needs_confirmation")) or weak_rel_claim
    edges = [rgraph.describe_edge(x) for x in rgraph.edges_of(e["name"], direction="both")[:8]]
    mems = [r["content"] for r in memory.search(e["name"], limit=6)]
    try:
        contacts = json.loads(e.get("contact_methods") or "{}")
        prefs = json.loads(e.get("preferences") or "[]")
        facts = json.loads(e.get("important_facts") or "[]")
        aliases = json.loads(e.get("aliases") or "[]")
        notes = json.loads(e.get("notes") or "[]")
    except (TypeError, ValueError):
        contacts, prefs, facts, aliases, notes = {}, [], [], [], []
    return {
        "entity_id": e["id"], "entity_type": e.get("kind") or "person", "name": display,
        "aliases": aliases, "relationship_to_user": rel["relationship"], "confidence": rel["confidence"],
        "source_type": rel["source_type"], "needs_confirmation": needs_confirm,
        "contact_methods": contacts if isinstance(contacts, dict) else {},
        "preferences": prefs if isinstance(prefs, list) else [],
        "important_facts": facts if isinstance(facts, list) else [],
        "notes": notes if isinstance(notes, list) else [],
        "edges": edges, "memories": mems,
        "last_interaction": e.get("last_interaction"),
    }


def set_contact(name: str, channel: str, value: str) -> dict:
    """Add a contact method ('email' | 'phone' | 'messaging' or a channel name)."""
    e = memory.upsert_entity(name)
    bucket = CHANNEL_ALIASES.get((channel or "").lower(), "messaging")
    try:
        contacts = json.loads(e.get("contact_methods") or "{}")
    except (TypeError, ValueError):
        contacts = {}
    contacts.setdefault(bucket, [])
    if value and value not in contacts[bucket]:
        contacts[bucket].append(value)
    c = memory.conn()
    with memory._lock, c:
        c.execute("UPDATE entities SET contact_methods=?, updated_at=? WHERE id=?",
                  (memory._enc(memory._j(contacts)), time.time(), e["id"]))
        c.commit()
    return contacts


def add_preference(name: str, text: str, important: bool = False) -> list:
    """A preference ('prefers short emails') or important fact ('birthday in March')."""
    e = memory.upsert_entity(name)
    col = "important_facts" if important else "preferences"
    try:
        items = json.loads(e.get(col) or "[]")
    except (TypeError, ValueError):
        items = []
    if text and text not in items:
        items.append(text[:200])
    c = memory.conn()
    with memory._lock, c:
        c.execute(f"UPDATE entities SET {col}=?, updated_at=? WHERE id=?",
                  (memory._enc(memory._j(items)), time.time(), e["id"]))
        c.commit()
    return items


def confirm(name: str):
    """The owner confirmed who this person is: entity + graph node both marked confirmed."""
    e = memory.upsert_entity(name, confidence=1.0)
    from ishti import rgraph
    c = memory.conn()
    with memory._lock, c:
        c.execute("UPDATE entities SET needs_confirmation=0, confidence=1.0, updated_at=? WHERE id=?",
                  (time.time(), e["id"]))
        c.commit()
    n = rgraph.get_node(e["name"])
    if n:
        rc = rgraph.conn()
        with rgraph.LOCK, rc:
            rc.execute("UPDATE nodes SET needs_confirmation=0 WHERE id=?", (n["id"],))
            rc.commit()
    return e


# --------------------------------------------------------------------- disambiguation
def find_all(name: str) -> list[dict]:
    """Every entity this name could mean: exact name, alias, or qualified variant."""
    key = (name or "").strip().lower()
    if not key:
        return []
    out = []
    seen = set()
    for e in memory.entities(limit=500):
        if e["id"] in seen:
            continue
        try:
            aliases = {a.lower() for a in json.loads(memory._dec(e.get("aliases") or "[]") or "[]")}
        except (TypeError, ValueError):
            aliases = set()
        base = e["name"].lower()
        # 'ahmed (work)' exposes its base name 'ahmed' as an implicit alias
        base_name = re.sub(r"\s*\([^)]*\)\s*$", "", base).strip()
        if key == base or key in aliases or key == base_name:
            out.append(e)
            seen.add(e["id"])
    return out


def _context_keys(context: dict) -> dict:
    """Normalize the disambiguating context we got for this mention."""
    ctx = context or {}
    org = str(ctx.get("org") or ctx.get("organization") or ctx.get("company") or "").strip().lower()
    email = str(ctx.get("email") or "").strip().lower()
    phone = re.sub(r"\D", "", str(ctx.get("phone") or ""))[-10:]
    relation = str(ctx.get("relation") or ctx.get("relationship") or "").strip().lower()
    channel = str(ctx.get("channel") or "").strip().lower()
    return {"org": org, "email": email, "phone": phone, "relation": relation, "channel": channel}


def _entity_keys(e: dict) -> dict:
    """What the entity row knows for matching: org (from works_at edge), contacts, relation."""
    contacts = {}
    try:
        contacts = json.loads(memory._dec(e.get("contact_methods") or "{}") or "{}")
    except (TypeError, ValueError):
        pass
    emails = {x.lower() for x in (contacts.get("email") or [])}
    phones = {re.sub(r"\D", "", x)[-10:] for x in (contacts.get("phone") or [])}
    rel = (e.get("relationship") or "").lower()
    orgs = set()
    try:
        from ishti import rgraph
        for edge in rgraph.edges_of(e["name"], rel="works_at", direction="out"):
            orgs.add(edge["d_name"].lower())
    except Exception as _exc:
        swallowed('ishti/arch2/people.py:_entity_keys', _exc)
    return {"email": emails, "phone": phones, "org": orgs, "relation": {rel} if rel and rel != "unknown" else set()}


def _score(e: dict, ctx: dict) -> tuple[float, list]:
    """(score, which disambiguators hit). email/phone/org are strong (1.0 each),
    relation/channel weak (0.35)."""
    keys = _entity_keys(e)
    hits = []
    score = 0.0
    if ctx["email"] and ctx["email"] in keys["email"]:
        score += 1.0; hits.append("email")
    if ctx["phone"] and ctx["phone"] in keys["phone"]:
        score += 1.0; hits.append("phone")
    if ctx["org"] and ctx["org"] in keys["org"]:
        score += 1.0; hits.append("org")
    if ctx["relation"] and ctx["relation"] in keys["relation"]:
        score += 0.35; hits.append("relation")
    if ctx["channel"]:
        hits.append("channel")  # recorded, weighted 0: 'via WhatsApp' alone decides nothing
    return score, hits


def resolve(name: str, context: dict | None = None, ask: bool = True) -> dict:
    """Which Ahmed? Returns {'entity': row|None, 'ambiguous': bool, 'question': str|''}.
    Auto-picks only a UNIQUE best with a strong disambiguator; otherwise asks (one
    question naming the candidates) — never guesses (C5)."""
    cands = find_all(name)
    if not cands:
        return {"entity": None, "ambiguous": False, "question": ""}
    if len(cands) == 1:
        return {"entity": cands[0], "ambiguous": False, "question": ""}
    ctx = _context_keys(context)
    scored = sorted(((_score(e, ctx), e) for e in cands), key=lambda t: -t[0][0])
    (top_score, top_hits), top_e = scored[0]
    second_score = scored[1][0][0] if len(scored) > 1 else 0.0
    # a stated KIN relation ('the brother one') is decisive: kin ties only come from the owner
    top_rel = (top_e.get("relationship") or "").lower()
    try:
        from ishti import rgraph as _rg
        kin = _rg.KIN
    except Exception:
        kin = set()
    strong = [h for h in top_hits if h in _STRONG or (h == "relation" and top_rel in kin)]
    kin_decisive = any(h == "relation" and top_rel in kin for h in top_hits) and second_score == 0.0
    if top_score > 0 and ((top_score - second_score >= 1.0 and strong) or kin_decisive):
        return {"entity": top_e, "ambiguous": False, "question": "", "matched": top_hits}
    if not ask:
        return {"entity": None, "ambiguous": True, "question": ""}
    names = ", ".join(_display(e) for e in cands)
    return {"entity": None, "ambiguous": True, "question":
            f"Which {name.strip()} — {names}? (say 'the work one', the org, or their email)"}


def _display(e: dict) -> str:
    d = e.get("display") or e["name"]
    rel = (e.get("relationship") or "").strip()
    return f"{d} ({rel})" if rel and rel != "unknown" else str(d)


def mention(name: str, context: dict | None = None) -> dict:
    """A mention of a person in a live request (J3's engine):
      - unique/decided match -> that entity (last_interaction touched)
      - strong context (email/phone/org/relation) matching nobody -> a NEW distinct
        same-named person ('ahmed (work)') — separate rows, separate graph nodes
      - weak/no context with several candidates -> ambiguous + the question
    Returns {'entity': row|None, 'ambiguous': bool, 'question': str, 'created': bool}."""
    r = resolve(name, context)
    ctx = _context_keys(context)
    strong_ctx = [k for k in _STRONG if ctx.get(k)] + (["relation"] if ctx.get("relation") else [])
    cands = find_all(name)
    if r["entity"] is not None:
        # one candidate — but a provided strong context that matches NOTHING on them means
        # the speaker means a DIFFERENT person with the same name (a second Ahmed), never
        # a silent re-attach (C5)
        if strong_ctx and not r.get("matched") \
                and len(cands) == 1 and _score(r["entity"], ctx)[0] == 0.0:
            return _create_variant(name, ctx)
        e = memory.upsert_entity(r["entity"]["name"])  # refreshes last_interaction
        return {"entity": e, "ambiguous": False, "question": "", "created": False}
    best = max((_score(e, ctx)[0] for e in cands), default=0.0)
    # a strong context that matches NOBODY decides for a new, distinct person: an email/org
    # nobody carries, or a KIN relation stated in the owner's own words — never invented
    try:
        from ishti import rgraph as _rg
        kin_ctx = bool(ctx.get("relation")) and ctx["relation"] in _rg.KIN
    except Exception:
        kin_ctx = False
    if best == 0.0 and strong_ctx and (kin_ctx or any(k in ("email", "phone", "org") for k in strong_ctx)):
        return _create_variant(name, ctx)
    if cands:  # several candidates, context not decisive: ask (C5)
        return {"entity": None, "ambiguous": True, "question": r["question"], "created": False}
    # a brand-new person
    e = memory.upsert_entity(name, relationship=ctx.get("relation") or None, confidence=0.6)
    if ctx.get("org"):
        from ishti import rgraph
        rgraph.relate(e["name"], "works_at", ctx["org"], kind_dst="organisation",
                      confidence=0.7, source_type="observed")
    return {"entity": e, "ambiguous": False, "question": "", "created": True}


def _create_variant(name: str, ctx: dict) -> dict:
    """The second (third, ...) person named `name`: a qualified row + their own edges."""
    from ishti import rgraph
    base = (name or "").strip().lower()
    label = ctx.get("org") or ctx.get("relation") or (f"contact {time.strftime('%b %Y')}")
    qual = f"{base} ({label})"
    n = 2
    while memory.find_entity(qual):
        n += 1
        qual = f"{base} ({label} {n})"
    e = memory.upsert_entity(qual, relationship=ctx.get("relation") or "unknown",
                             confidence=0.6, aliases=[base])
    # set the display casing once
    c = memory.conn()
    with memory._lock, c:
        c.execute("UPDATE entities SET display=?, contact_methods=COALESCE(contact_methods, '{}') WHERE id=?",
                  (qual.title(), e["id"]))
        c.commit()
    if ctx.get("org"):
        rgraph.relate(e["name"], "works_at", ctx["org"], kind_dst="organisation",
                      confidence=0.8, source_type="observed")
    if ctx.get("email"):
        set_contact(qual, "email", ctx["email"])
    if ctx.get("phone"):
        set_contact(qual, "phone", ctx["phone"])
    if ctx.get("relation") and ctx["relation"] in rgraph.KIN:
        # a kin relation arrives here ONLY from the owner's own words (consolidate.py gates
        # that) — so the graph edge is explicit, exactly like 'Ahmed is my brother' (C5/J1)
        rgraph.kin_edge("me", ctx["relation"], qual, source_type="explicit_user")
    return {"entity": memory.find_entity(qual), "ambiguous": False, "question": "", "created": True}


def list_people(limit=200) -> list[dict]:
    """All person records (the dashboard's People/Relationship tabs)."""
    from ishti import rgraph
    out = []
    for e in memory.entities(limit=limit):
        if (e.get("kind") or "person") not in ("person", ""):
            continue
        rel = rgraph.relationship_of(e["name"])
        weak_rel_claim = rel["relationship"] not in ("unknown", "") and rel["source_type"] != "explicit_user"
        out.append({"id": e["id"], "name": e.get("display") or e["name"],
                    "relationship": rel["relationship"], "confidence": rel["confidence"],
                    "needs_confirmation": bool(e.get("needs_confirmation")) or weak_rel_claim,
                    "source_type": rel["source_type"],
                    "last_interaction": e.get("last_interaction")})
    return out


if __name__ == "__main__":
    import os
    import tempfile
    import ishti.arch2.memory as M
    import ishti.rgraph as R
    tmp = tempfile.mkdtemp(prefix="ishti_people_")
    M.db_path = lambda: os.path.join(tmp, "m.db")
    M._conn = None
    R.DB_PATH = os.path.join(tmp, "g.db")
    R._conn = None
    # three different Ahmeds
    r1 = mention("ahmed", {"org": "netsole", "email": "a@netsole.com"})
    assert r1["created"], r1
    r2 = mention("ahmed", {"org": "university"})
    assert r2["created"] and r2["entity"]["name"] != r1["entity"]["name"], r2
    r3 = mention("ahmed", {"relation": "brother"})
    assert r3["created"] and len({r1["entity"]["id"], r2["entity"]["id"], r3["entity"]["id"]}) == 3, r3
    # resolution by context
    got = resolve("ahmed", {"email": "a@netsole.com"})
    assert got["entity"] and got["entity"]["id"] == r1["entity"]["id"], got
    got2 = resolve("ahmed", {"org": "netsole"})
    assert got2["entity"] and got2["entity"]["id"] == r1["entity"]["id"], got2
    # no context -> ambiguity question naming the candidates, never a guess
    amb = resolve("ahmed")
    assert amb["ambiguous"] and amb["question"].lower().count("ahmed") >= 2, amb
    assert mention("ahmed")["question"], "bare mention must ask"
    # J1: explicit kin sticks to the family Ahmed
    R.kin_edge("me", "brother", r3["entity"]["name"])
    got3 = resolve("ahmed", {"relation": "brother"})
    assert got3["entity"] and got3["entity"]["id"] == r3["entity"]["id"], got3
    # contact methods + preferences on the record
    set_contact("ahmed (netsole)", "email", "a@netsole.com")
    add_preference("ahmed (netsole)", "prefers short emails")
    rec = record("ahmed (netsole)")
    assert rec["contact_methods"]["email"] == ["a@netsole.com"] and rec["preferences"], rec
    assert rec["relationship_to_user"] == "unknown"  # frequency/absence never invents one
    # confirm clears the flag
    R.note_contact("ahmed (university)")
    confirm("ahmed (university)")
    assert record("ahmed (university)")["needs_confirmation"] is False
    print("[self-check] arch2.people OK — person records, 3 same-named people, context resolution, ambiguity asks")
