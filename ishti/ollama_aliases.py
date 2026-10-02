"""Stale context-pin aliases that ISHTI's VRAM governor created in Ollama (P6-4, Issue 20).

The governor runs a model at a context other than its own default through a same-weights alias named
`<base>-c<N>k:<tag>` (created with /api/create: FROM <base>, PARAMETER num_ctx N*1024). Aliases cost almost nothing
(no weights are copied), but old ones pile up in `ollama list` after the ladder or the contexts change.

This module removes ONLY aliases that are provably ISHTI's own and no longer needed:
  * the name has exactly the governor's alias shape and Ollama reports the matching parent model
    (details.parent_model == the base name), and its num_ctx parameter equals the N in the name -
    a model the owner or another tool made by hand never matches all three;
  * it is not a rung of any role's ladder (the variant names the governor will ask for), not the owner's chosen
    model, and not loaded right now;
  * it is at least `min_age_h` hours old (a freshly created alias may be about to be loaded by a concurrent job).
Deleting an alias removes a manifest only: Ollama keeps the shared weights blobs while any other model uses them, and
the governor re-creates an alias in seconds if a context step ever needs it again.

    venv\\Scripts\\python -m ishti.ollama_aliases            # list what would be removed (dry run)
    venv\\Scripts\\python -m ishti.ollama_aliases --apply    # remove it
"""
import datetime as dt
import re
import sys

import requests

from ishti.core.logutil import swallowed

ALIAS = re.compile(r"^(?P<stem>.+)-c(?P<k>\d+)k:(?P<tag>.+)$")
MIN_AGE_H = 24


def _base(name):
    m = ALIAS.match(name)
    return f"{m['stem']}:{m['tag']}" if m else None


def needed_names(vram_mod=None):
    """Variant names the governor can ask for: every rung of every role (installed or not) plus the owner's pick."""
    v = vram_mod
    if v is None:
        from ishti import vram as v
    names = set()
    for role, spec in (v.cfg().get("roles") or {}).items():
        for r in spec.get("rungs", []):
            names.add(r["model"])
            if not r.get("cpu"):
                names.add(v.alias_name(r["model"], v.rung_num_ctx(r)))
        try:
            names.update(r["name"] for r in v.rungs(role))
        except Exception as exc:  # an unreadable role must not make us delete more: log it, keep what we have
            swallowed(f"ishti/ollama_aliases.py:needed_names({role})", exc)
    return names


def candidates(base_url=None, get=None, post=None, needed=None, now=None):
    """-> [{alias, base, ctx, age_h, stale: bool, why}] for every governor-shaped alias in Ollama."""
    from ishti import vram
    base_url = base_url or vram.OLLAMA
    get = get or (lambda p: requests.get(base_url + p, timeout=10).json())
    post = post or (lambda p, body: requests.post(base_url + p, json=body, timeout=10).json())
    needed = needed_names() if needed is None else needed
    loaded = {m["name"] for m in get("/api/ps").get("models", [])}
    now = now or dt.datetime.now(dt.timezone.utc)
    out = []
    for m in get("/api/tags").get("models", []):
        name, k = m["name"], ALIAS.match(m["name"])
        if not k:
            continue
        base = _base(name)
        if (m.get("details") or {}).get("parent_model") != base:
            continue  # not made FROM that base: not the governor's alias
        try:
            shown = post("/api/show", {"model": name})
            ctx = int((shown.get("parameters") or "").split("num_ctx")[1].split()[0])
        except (IndexError, ValueError, requests.RequestException, AttributeError):
            continue
        if ctx != int(k["k"]) * 1024:
            continue
        try:
            age_h = (now - dt.datetime.fromisoformat(m["modified_at"])).total_seconds() / 3600
        except (KeyError, ValueError):
            age_h = 0.0
        why = ("a rung of a ladder" if name in needed else "loaded right now" if name in loaded
               else f"younger than {MIN_AGE_H} h" if age_h < MIN_AGE_H else "")
        out.append({"alias": name, "base": base, "ctx": ctx, "age_h": round(age_h, 1), "stale": not why,
                    "why": why or "not a rung of any ladder, not loaded"})
    return out


def cleanup(apply=False, base_url=None, get=None, post=None, delete=None, needed=None, now=None):
    """Remove (apply=True) or just list (apply=False) the stale aliases -> {'removed': [...], 'kept': [...], 'failed': [...]}."""
    from ishti import vram
    base_url = base_url or vram.OLLAMA
    delete = delete or (lambda name: requests.delete(base_url + "/api/delete", json={"model": name}, timeout=30).status_code)
    rows = candidates(base_url, get, post, needed, now)
    res = {"removed": [], "kept": [r["alias"] for r in rows if not r["stale"]], "failed": [],
           "stale": [r["alias"] for r in rows if r["stale"]]}
    if apply:
        for r in rows:
            if r["stale"]:
                code = delete(r["alias"])
                (res["removed"] if code == 200 else res["failed"]).append(r["alias"])
    return res


if __name__ == "__main__":
    apply = "--apply" in sys.argv
    out = cleanup(apply=apply)
    print(("removed: " if apply else "would remove: ") + (", ".join(out["removed"] if apply else out["stale"]) or "nothing"))
    print("kept: " + (", ".join(out["kept"]) or "nothing"))
    if out["failed"]:
        print("could not remove: " + ", ".join(out["failed"]))
