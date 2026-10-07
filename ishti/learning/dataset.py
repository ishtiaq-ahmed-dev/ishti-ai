"""Offline training dataset curation and export (spec 20, 21, 22).

Stores and curates high-quality instruction-response pairs for offline LoRA/QLoRA jobs.
Includes deduplication, quality filtering, and contradiction avoidance.
"""
import json
import os
from typing import Any, Dict, List, Optional, Tuple
from ishti.learning import store

def add_example(instruction: str, correct: str = "", incorrect: str = "",
                correction: str = "", reason: str = "", quality: float = 0.8,
                skill: str = "", domain: str = "", source: str = "curated") -> Dict[str, Any]:
    """Add a supervised fine-tuning example to dataset."""
    ins = (instruction or "").strip()
    if not ins:
        return {}
    now = store.now()
    eid = store.add(
        "dataset",
        ts=now,
        instruction=ins,
        context="",
        incorrect=incorrect or "",
        correct=correct or "",
        correction=correction or "",
        reason=reason or "",
        quality=float(quality),
        skill=skill or "",
        domain=domain or "",
        source=source or "curated",
        tags="[]",
        exported=0
    )
    row = store.row("SELECT * FROM dataset WHERE id=?", (eid,))
    return dict(row) if row else {}

def from_feedback(fb: Dict[str, Any], interaction: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """Generate fine-tuning example from explicit user correction."""
    if not fb or fb.get("kind") != "explicit_correction":
        return None
    req = (interaction or {}).get("request") or fb.get("message") or ""
    cor = fb.get("correct") or ""
    inc = fb.get("incorrect") or ""
    if not req or not cor:
        return None
    return add_example(
        instruction=req,
        correct=cor,
        incorrect=inc,
        correction=fb.get("message") or "",
        reason=fb.get("error_type") or "correction",
        quality=0.9,
        skill=(interaction or {}).get("skill") or "",
        source="feedback_correction"
    )

def rows(limit: int = 200) -> List[Dict[str, Any]]:
    res = store.rows("SELECT * FROM dataset ORDER BY ts DESC LIMIT ?", (int(limit),))
    return [dict(r) for r in res]

def stats() -> Dict[str, Any]:
    total = store.row("SELECT count(*) as c FROM dataset")["c"]
    by_skill = {}
    for r in store.rows("SELECT skill, count(*) as c FROM dataset GROUP BY skill"):
        if r["skill"]:
            by_skill[r["skill"]] = r["c"]
    by_quality = {
        "high": store.row("SELECT count(*) as c FROM dataset WHERE quality >= 0.8")["c"],
        "medium": store.row("SELECT count(*) as c FROM dataset WHERE quality >= 0.5 AND quality < 0.8")["c"],
        "low": store.row("SELECT count(*) as c FROM dataset WHERE quality < 0.5")["c"]
    }
    exp = store.row("SELECT count(*) as c FROM dataset WHERE exported=1")["c"]
    return {
        "total": total,
        "by_quality": by_quality,
        "by_skill": by_skill,
        "exported": exp
    }

def delete(example_id: int) -> bool:
    return store.write("DELETE FROM dataset WHERE id=?", (int(example_id),)) > 0

def curate(example_rows: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    """Deduplicate, quality filter, and balance dataset examples (spec 21)."""
    min_q = float(store.cfg().get("dataset", {}).get("min_quality", 0.6))
    kept = []
    skipped = {"low_quality": 0, "duplicate": 0}
    seen = set()
    for r in example_rows:
        q = float(r.get("quality") or 0.0)
        ins = (r.get("instruction") or "").strip().lower()
        if q < min_q:
            skipped["low_quality"] += 1
            continue
        if ins in seen:
            skipped["duplicate"] += 1
            continue
        seen.add(ins)
        kept.append(r)
    return kept, skipped

def export(path: Optional[str] = None, min_quality: Optional[float] = None) -> Tuple[str, int]:
    """Export curated examples to JSONL file."""
    # An explicit test/operator path is authoritative; do not create the
    # Windows-configured default directory on another host.
    if path:
        out_path = os.path.abspath(path)
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
    else:
        exp_dir = store.cfg().get("dataset", {}).get("export_dir") or os.path.join(os.path.dirname(store.db_path()), "finetune")
        os.makedirs(exp_dir, exist_ok=True)
        out_path = os.path.join(exp_dir, "dataset.jsonl")
    
    all_rows = rows(limit=5000)
    kept, _ = curate(all_rows)
    
    with open(out_path, "w", encoding="utf-8") as f:
        for ex in kept:
            f.write(json.dumps(ex, ensure_ascii=False) + "\n")
            store.write("UPDATE dataset SET exported=1 WHERE id=?", (ex["id"],))
            
    return out_path, len(kept)

if __name__ == "__main__":
    import tempfile
    tmp = tempfile.mkdtemp()
    store.set_db_path(os.path.join(tmp, "ds.db"))
    
    e1 = add_example("What is my name?", correct="Ishtiaq Ahmed", quality=0.95, skill="memory")
    e2 = add_example("What is my name?", correct="Ishtiaq Ahmed", quality=0.95, skill="memory")
    e3 = add_example("garbage input", correct="something", quality=0.2, skill="misc")
    
    all_r = rows()
    kept, skipped = curate(all_r)
    assert len(kept) == 1 and skipped["duplicate"] == 1 and skipped["low_quality"] == 1
    
    out_file, n = export(path=os.path.join(tmp, "export.jsonl"))
    assert n == 1 and os.path.exists(out_file)
    
    st = stats()
    assert st["total"] == 3 and st["exported"] == 1
    
    print("[self-check] learning.dataset OK - curation, deduplication, quality filtering, JSONL export")
