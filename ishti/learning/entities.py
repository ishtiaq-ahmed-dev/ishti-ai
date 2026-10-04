"""Entity separation, disambiguation, and contact tracking (spec 25, 26).

Entities with the same name are never merged without confirmation. Ambiguities trigger
disambiguation questions rather than silent assumptions.
"""
import os
import re
from typing import Any, Dict, List, Optional
from ishti.learning import store

def _key(name: str, org: str = "") -> str:
    n = re.sub(r"[^a-z0-9]", "_", (name or "").strip().lower()).strip("_")
    o = re.sub(r"[^a-z0-9]", "_", (org or "").strip().lower()).strip("_")
    return f"{n}@{o}" if o else n

def observe(name: str, org: str = "", email: str = "", phone: str = "",
            relationship: str = "", source: str = "owner", note: str = "") -> Dict[str, Any]:
    """Observe or update an entity record."""
    nm = (name or "").strip()
    if not nm:
        return {}
    key = _key(nm, org)
    now = store.now()
    existing = store.row("SELECT * FROM entities WHERE key=?", (key,))
    if existing:
        mentions = int(existing["mentions"] or 1) + 1
        store.write(
            "UPDATE entities SET mentions=?, last_seen=?, email=COALESCE(NULLIF(?,''), email), "
            "phone=COALESCE(NULLIF(?,''), phone), relationship=COALESCE(NULLIF(?,''), relationship) WHERE key=?",
            (mentions, now, email, phone, relationship, key)
        )
    else:
        store.add("entities", key=key, name=nm, org=org or "", email=email or "",
                  phone=phone or "", relationship=relationship or "", aliases="[]",
                  confidence=0.9 if source == "owner" else 0.5, mentions=1,
                  last_seen=now, source=source, note=note or "")
    row = store.row("SELECT * FROM entities WHERE key=?", (key,))
    return dict(row) if row else {}

def note_contact(entity_key: str, channel: str = "", detail: str = "") -> Dict[str, Any]:
    """Record contact occurrence without inventing relationships."""
    if not entity_key:
        return {}
    now = store.now()
    mid = store.add("entity_mentions", ts=now, entity_key=entity_key, channel=channel,
                    detail=detail, source="contact")
    store.write("UPDATE entities SET last_seen=?, mentions=mentions+1 WHERE key=?", (now, entity_key))
    row = store.row("SELECT * FROM entities WHERE key=?", (entity_key,))
    return dict(row) if row else {"id": mid}

def candidates(name: str, context: str = "") -> List[Dict[str, Any]]:
    """Return all candidate entities matching name."""
    nm = (name or "").strip().lower()
    if not nm:
        return []
    rows = store.rows("SELECT * FROM entities WHERE lower(name)=? OR lower(key) LIKE ?",
                      (nm, f"%{nm}%"))
    ctx_tokens = set(re.findall(r"[a-z0-9]+", (context or "").lower()))
    res = []
    for r in rows:
        d = dict(r)
        score = 0.5
        why = "name match"
        # context scoring is token-based: "Karachi Corp" matches "email the karachi office"
        org_hits = [t for t in re.findall(r"[a-z0-9]+", (d.get("org") or "").lower()) if len(t) > 2 and t in ctx_tokens]
        rel_hits = [t for t in re.findall(r"[a-z0-9]+", (d.get("relationship") or "").lower()) if len(t) > 2 and t in ctx_tokens]
        if org_hits:
            score += 0.4
            why += f", org '{d['org']}' matches context"
        if rel_hits:
            score += 0.3
            why += f", relationship '{d['relationship']}' matches context"
        d["score"] = min(1.0, score)
        d["why"] = why
        d["aliases"] = store.js(d.get("aliases"), [])
        res.append(d)
    return sorted(res, key=lambda x: x["score"], reverse=True)

def resolve(name: str, context: str = "") -> Dict[str, Any]:
    """Resolve entity by name & context; disambiguate if ambiguous (spec 25, 26)."""
    cands = candidates(name, context)
    if not cands:
        return {"selected": None, "confidence": 0.0, "ambiguous": False, "question": None, "candidates": []}
    if len(cands) == 1:
        return {"selected": cands[0], "confidence": cands[0]["score"], "ambiguous": False, "question": None, "candidates": cands}
    
    top = cands[0]
    runner_up = cands[1]
    margin = float(store.cfg().get("entities", {}).get("ambiguity_margin", 0.2))
    if (top["score"] - runner_up["score"]) >= margin:
        return {"selected": top, "confidence": top["score"], "ambiguous": False, "question": None, "candidates": cands}
    
    labels = []
    for c in cands[:3]:
        lbl = c["name"]
        if c.get("org"):
            lbl += f" ({c['org']})"
        elif c.get("relationship"):
            lbl += f" ({c['relationship']})"
        labels.append(lbl)
    q = f"Which {name} do you mean: {', or '.join(labels)}?"
    return {
        "selected": None,
        "confidence": top["score"],
        "ambiguous": True,
        "question": q,
        "candidates": cands
    }

def list_people() -> List[Dict[str, Any]]:
    rows = store.rows("SELECT * FROM entities ORDER BY mentions DESC, last_seen DESC")
    out = []
    for r in rows:
        d = dict(r)
        d["aliases"] = store.js(d.get("aliases"), [])
        out.append(d)
    return out

def remove(key_or_name: str) -> bool:
    nm = (key_or_name or "").strip()
    if not nm:
        return False
    k = _key(nm)
    store.write("DELETE FROM entities WHERE key=? OR key=? OR lower(name)=lower(?)", (nm, k, nm))
    store.write("DELETE FROM entity_mentions WHERE entity_key=? OR entity_key=?", (nm, k))
    return True

forget = remove


def rename(old_name: str, new_name: str) -> bool:
    """Rename an entity across the learning entities table and mentions."""
    old_nm = (old_name or "").strip()
    new_nm = (new_name or "").strip()
    if not old_nm or not new_nm:
        return False
    row = store.row("SELECT * FROM entities WHERE lower(name)=lower(?) OR key=?", (old_nm, _key(old_nm)))
    if not row:
        return False
    new_k = _key(new_nm, row.get("org") or "")
    store.write("UPDATE entities SET key=?, name=?, last_seen=? WHERE id=?", (new_k, new_nm, store.now(), row["id"]))
    store.write("UPDATE entity_mentions SET entity_key=? WHERE entity_key=?", (new_k, row["key"]))
    return True


if __name__ == "__main__":
    import tempfile
    tmp = tempfile.mkdtemp()
    store.set_db_path(os.path.join(tmp, "ent.db"))
    
    p1 = observe("Ahmed", org="Work", email="ahmed@work.com", relationship="colleague")
    p2 = observe("Ahmed", org="Gym", relationship="trainer")
    
    res = resolve("Ahmed")
    assert res["ambiguous"] is True and res["question"] and "Work" in res["question"] and "Gym" in res["question"]
    
    res_work = resolve("Ahmed", context="send email to work project")
    assert res_work["ambiguous"] is False and res_work["selected"]["org"] == "Work"
    
    print("[self-check] learning.entities OK - separation, disambiguation questions, context scoring")

