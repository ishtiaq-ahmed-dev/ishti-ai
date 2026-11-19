"""Dashboard aggregation and HTTP endpoint support (docs/LEARNING_INTERFACE.md section 2 & 4).

Supplies data shapes, queries, and mutators consumed by interface/web_ui/server.py
and interface/app/src/sections/Learning.tsx. Fail-soft by design.
"""
import os
import shutil
from typing import Any, Dict, List, Optional, Tuple, Union

from ishti.learning import dataset as ds_module
from ishti.learning import entities as ent_module
from ishti.learning import errors as err_module
from ishti.learning import lessons as ls_module
from ishti.learning import observe as obs_module
from ishti.learning import store
from ishti.learning import workflows as wf_module

def _memory_count() -> int:
    """Active long-term memories (0 when the store isn't there yet)."""
    import sqlite3
    from ishti.memstore import memory
    if not os.path.isfile(memory.db_path()):
        return 0
    try:
        with sqlite3.connect(memory.db_path()) as c:
            return c.execute("SELECT COUNT(*) FROM memories WHERE status='active'").fetchone()[0]
    except sqlite3.Error as exc:
        from ishti.core.logutil import swallowed
        swallowed("ishti/learning/dashboard.py:_memory_count", exc)
        return 0


def _skill_count() -> int:
    from ishti.dispatcher import SKILLS
    return len(SKILLS)


def overview() -> Dict[str, Any]:
    """Overview payload: counts, confidence buckets, loops, thresholds, settings, recent activity."""
    st = ls_module.stats()
    cnts = {
        "lessons": store.row("SELECT count(*) as c FROM lessons")["c"],
        "active_lessons": store.row("SELECT count(*) as c FROM lessons WHERE status='active'")["c"],
        "candidates": store.row("SELECT count(*) as c FROM candidates WHERE status='open'")["c"],
        "feedback": store.row("SELECT count(*) as c FROM feedback")["c"],
        "mistakes": store.row("SELECT count(*) as c FROM mistakes")["c"],
        "interactions": store.row("SELECT count(*) as c FROM interactions")["c"],
        "tasks": store.row("SELECT count(*) as c FROM tasks")["c"],
        "workflows": store.row("SELECT count(*) as c FROM workflows WHERE status='active'")["c"],
        "people": store.row("SELECT count(*) as c FROM entities")["c"],
        "dataset": store.row("SELECT count(*) as c FROM dataset")["c"],
        "confirmed": store.row("SELECT count(*) as c FROM lessons WHERE confirmed=1")["c"],
    }
    buckets = st.get("buckets") or {
        "weak": 0, "possible": 0, "probable": 0, "strong": 0, "confirmed": cnts["confirmed"]
    }
    recent_activity = obs_module.recent(limit=20)
    # real counts for the three loop cards (they showed "-": the values were descriptions, and "61 skills" was stale)
    loops = {"memory": {"count": cnts["people"] + _memory_count()}, "skills": {"count": _skill_count()},
             "learning": {"count": cnts["lessons"] + cnts["feedback"] + cnts["mistakes"]}}
    return {
        "enabled": store.enabled(),
        "db": store.db_path(),
        "counts": cnts,
        "loops": loops,
        "confidence_buckets": buckets,
        "thresholds": ls_module.thresholds(),
        "settings": settings().get("settings", {}),
        "recent": recent_activity
    }

def lessons(status: Optional[str] = None, kind: Optional[str] = None,
            q: Optional[str] = None, limit: int = 200) -> List[Dict[str, Any]]:
    return ls_module.list_lessons(status=status, kind=kind, q=q, limit=limit)

def update_lesson(lesson_id: str, **fields: Any) -> Dict[str, Any]:
    return ls_module.edit(lesson_id, **fields)

def confirm_lesson(lesson_id: str) -> Dict[str, Any]:
    return ls_module.confirm(lesson_id)

def reject_lesson(lesson_id: str) -> Dict[str, Any]:
    return ls_module.reject(lesson_id)

def delete_lesson(lesson_id: str, forget: bool = False) -> Dict[str, Any]:
    if forget:
        res = ls_module.forget(lesson_id)
        undone = bool(res.get("memory_undone"))
        ls_module.delete(lesson_id)
        return {"deleted": True, "memory_forgotten": undone}
    ls_module.delete(lesson_id)

def candidates(status: Optional[str] = None) -> List[Dict[str, Any]]:
    st = status or "open"
    rows = store.rows("SELECT * FROM candidates WHERE status=? ORDER BY ts DESC", (st,))
    out = []
    for r in rows:
        d = dict(r)
        d["payload"] = store.js(d.get("payload"), {})
        out.append(d)
    return out

def answer_candidate(candidate_id: Union[int, str], keep: bool,
                     edits: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return ls_module.accept_candidate(int(candidate_id), keep=keep, edits=edits)

def feedback_list(limit: int = 100) -> List[Dict[str, Any]]:
    rows = store.rows("SELECT * FROM feedback ORDER BY ts DESC LIMIT ?", (int(limit),))
    return [dict(r) for r in rows]


def reflections(limit: int = 100) -> Dict[str, Any]:
    """The Reflections panel payload (spec: reflect/insight APIs).

    Two halves: what ISHTI has already written down (stored reflections, kept at the weak band so
    they are retrievable but cannot out-shout an owner-confirmed lesson), and what it can still
    mine from the interaction log. Fail-soft - a missing reflect module must not blank the panel.
    """
    rows = store.rows("SELECT * FROM lessons WHERE source='monitor' ORDER BY updated DESC LIMIT ?",
                      (int(limit),))
    stored = []
    for r in rows:
        d = dict(r)
        d["bucket"] = ls_module.bucket(d.get("confidence"))
        d["action"] = store.js(d.get("action"), {}) if d.get("action") else {}
        d["kind_of"] = ("insight" if str(d.get("note") or "").startswith("mined insight")
                        else "reflection")
        stored.append(d)
    try:
        from ishti.learning import reflect as rf_module
        mined = rf_module.mine_insights(limit=int(limit))
    except Exception:
        mined = []
    return {
        "reflections": stored,
        "mined": mined,
        "counts": {
            "reflections": sum(1 for d in stored if d["kind_of"] == "reflection"),
            "insights": sum(1 for d in stored if d["kind_of"] == "insight"),
            "pending": len(mined),
        },
    }


def promote_insights(limit: int = 50) -> Dict[str, Any]:
    """Mine now and turn what reads as a rule into a low-confidence lesson (ExpeL)."""
    try:
        from ishti.learning import reflect as rf_module
        return {"promoted": rf_module.promote_insights(limit=int(limit))}
    except Exception as exc:
        return {"promoted": [], "error": str(exc)}

def mistakes(limit: int = 200) -> List[Dict[str, Any]]:
    return err_module.repeated(min_count=1)

def interactions(limit: int = 50) -> List[Dict[str, Any]]:
    return obs_module.recent(limit=limit)

def tasks(limit: int = 50) -> List[Dict[str, Any]]:
    rows = store.rows("SELECT * FROM tasks ORDER BY ts DESC LIMIT ?", (int(limit),))
    out = []
    for r in rows:
        d = dict(r)
        d["steps"] = store.js(d.get("steps"), [])
        d["lessons"] = store.js(d.get("lessons"), [])
        out.append(d)
    return out

def workflows() -> List[Dict[str, Any]]:
    return wf_module.list_workflows()

def people() -> List[Dict[str, Any]]:
    return ent_module.list_people()

def sources() -> List[Dict[str, Any]]:
    rows = store.rows(
        "SELECT source, count(*) as lessons, max(updated) as last_ts "
        "FROM lessons GROUP BY source ORDER BY lessons DESC"
    )
    return [dict(r) for r in rows]

def dataset(limit: int = 200) -> Dict[str, Any]:
    return {
        "dataset": ds_module.rows(limit=limit),
        "stats": ds_module.stats()
    }

def add_dataset(payload: Dict[str, Any]) -> Dict[str, Any]:
    return ds_module.add_example(
        instruction=payload.get("instruction", ""),
        correct=payload.get("correct", ""),
        incorrect=payload.get("incorrect", ""),
        reason=payload.get("reason", ""),
        quality=float(payload.get("quality", 0.8)),
        skill=payload.get("skill", ""),
        source="dashboard"
    )

def delete_dataset(example_id: int) -> Dict[str, Any]:
    return {"deleted": ds_module.delete(example_id)}

def export_dataset() -> Tuple[str, int]:
    return ds_module.export()

    return {"deleted": True, "memory_forgotten": False}


def settings() -> Dict[str, Any]:
    cfg = store.cfg()
    return {
        "settings": {
            "enabled": store.enabled(),
            "proactive": store.proactive(),
        },
        "thresholds": ls_module.thresholds(),
        "evidence": store.setting("evidence", cfg.get("evidence", {}))
    }

def update_settings(**kwargs: Any) -> Dict[str, Any]:
    if "enabled" in kwargs:
        store.set_setting("enabled", bool(kwargs["enabled"]))
    if "proactive" in kwargs:
        store.set_setting("proactive", bool(kwargs["proactive"]))
    if "thresholds" in kwargs and isinstance(kwargs["thresholds"], dict):
        store.set_setting("thresholds", kwargs["thresholds"])
    if "evidence" in kwargs and isinstance(kwargs["evidence"], dict):
        store.set_setting("evidence", kwargs["evidence"])
    return settings()

def ack_and_ask() -> Dict[str, Any]:
    """Human-in-the-loop candidate question slot."""
    cfg = settings()
    if not cfg.get("settings", {}).get("proactive"):
        return {"question": None, "candidate_id": None}
    cands = candidates(status="open")
    if cands:
        return {"question": cands[0]["question"], "candidate_id": cands[0]["id"]}
    return {"question": None, "candidate_id": None}

def export(what: str = "all") -> Tuple[str, int]:
    w = (what or "all").lower()
    exp_dir = store.cfg().get("export_dir") or os.path.join(os.path.dirname(store.db_path()), "learning", "export")
    os.makedirs(exp_dir, exist_ok=True)
    
    if w == "dataset":
        return ds_module.export()
    if w == "lessons":
        return ls_module.export(os.path.join(exp_dir, "lessons.json"))
        
    out_file = os.path.join(exp_dir, f"{w}_export.json")
    n = 0
    import json
    data = {}
    if w in ("lessons", "all"):
        data["lessons"] = [dict(r) for r in store.rows("SELECT * FROM lessons")]
        n += len(data["lessons"])
    if w in ("feedback", "all"):
        data["feedback"] = [dict(r) for r in store.rows("SELECT * FROM feedback")]
        n += len(data["feedback"])
    if w in ("mistakes", "all"):
        data["mistakes"] = [dict(r) for r in store.rows("SELECT * FROM mistakes")]
        n += len(data["mistakes"])
    if w in ("dataset", "all"):
        data["dataset"] = [dict(r) for r in store.rows("SELECT * FROM dataset")]
        n += len(data["dataset"])
        
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        
    return out_file, n

if __name__ == "__main__":
    import tempfile
    tmp = tempfile.mkdtemp()
    store.set_db_path(os.path.join(tmp, "dash.db"))
    
    ov = overview()
    assert ov["enabled"] is True and "counts" in ov
    
    st = settings()
    assert "thresholds" in st and "evidence" in st
    
    up = update_settings(proactive=True)
    assert up["settings"]["proactive"] is True

    # A stored setting that nothing reads is a bug, not a feature: the Settings tab used to
    # write `enabled`/`thresholds`/`evidence` into the settings table while `enabled()` and
    # `thresholds()` kept reading config.json, so the toggle and every threshold edit were
    # decorative. Assert the *effect*, not the round-trip.
    assert store.enabled() is True
    update_settings(enabled=False)
    assert store.enabled() is False, "enabled must be honoured by store.enabled()"
    update_settings(enabled=True)
    assert store.enabled() is True

    update_settings(thresholds={"weak": 0.7})
    assert ls_module.thresholds()["weak"] == 0.7, ls_module.thresholds()
    assert ls_module.thresholds()["strong"] == 0.95, "a partial override must keep the other bands"
    store.write("DELETE FROM settings WHERE key=?", ("thresholds",))

    update_settings(evidence={"candidate_rule": 9})
    assert store.evidence()["candidate_rule"] == 9, store.evidence()
    assert store.evidence()["workflow"] == 3, "a partial override must keep the other minimums"
    store.write("DELETE FROM settings WHERE key=?", ("evidence",))

    # and `enabled=False` must actually stop a write
    update_settings(enabled=False)
    blocked = ls_module.add("preference", "should not be written while learning is off",
                            scope="session", context="selfcheck", source="repeated_observation",
                            confidence=0.5)
    assert not blocked, "learning must not write while enabled is false"
    update_settings(enabled=True)

    # reflections/promote_insights: the payloads /api/learning/reflections* serves
    rp = reflections()
    assert {"reflections", "mined", "counts"} <= set(rp), rp.keys()
    assert isinstance(rp["reflections"], list) and isinstance(rp["mined"], list)
    pr = promote_insights()
    assert isinstance(pr.get("promoted"), list), pr

    print("[self-check] learning.dashboard OK - overview, endpoints support, settings take effect, "
          "reflections payload, export")
