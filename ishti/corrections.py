"""Learning from corrections (D4). "That should have been the browser" re-runs the request with that skill (the
dispatcher) - and is recorded here: what was asked, what ISHTI did, what it should have done.

Short-term, the router consults the log first:
  - the very same request again  -> the corrected skill (one correction is enough for the exact request)
  - a similar request (>= 85% alike) -> the corrected skill only when at least 2 corrections agree and they
    outnumber any that disagree: repeated patterns count, a single correction doesn't overwrite established routing
Longer-term: export() writes data/corrections.jsonl (request, wrong, right, count) for the periodic Laya
fine-tune (NEEDS #1) - not live learning."""
import difflib
import json
import os
import re
import time

from ishti import CONFIG

EXPORT = os.path.join(os.path.dirname(CONFIG["memory"]["db_path"]), "corrections.jsonl")


def _db():
    from ishti.facts import get_facts
    f = get_facts()
    with f.lock, f.db:
        f.db.execute("CREATE TABLE IF NOT EXISTS corrections(ts REAL, request TEXT, norm TEXT, wrong TEXT, right TEXT)")
    return f


def norm(text):
    return " ".join(re.findall(r"[a-z0-9']+", (text or "").lower()))


def record(request, wrong, right):
    f = _db()
    with f.lock, f.db:
        f.db.execute("INSERT INTO corrections VALUES(?,?,?,?,?)", (time.time(), request[:500], norm(request), wrong or "", right))


def learned(text):
    """The skill past corrections point to for this request, or None."""
    f = _db()
    with f.lock:
        rows = f.db.execute("SELECT norm, right FROM corrections ORDER BY ts DESC LIMIT 2000").fetchall()
    if not rows:
        return None
    n = norm(text)
    exact = [r for q, r in rows if q == n]
    if exact:
        return max(set(exact), key=exact.count)  # the most given answer for exactly this request
    votes = {}
    for q, r in rows:
        if difflib.SequenceMatcher(None, q, n).ratio() >= 0.85:
            votes[r] = votes.get(r, 0) + 1
    if not votes:
        return None
    best = max(votes, key=votes.get)
    others = sum(v for k, v in votes.items() if k != best)
    return best if votes[best] >= 2 and votes[best] > others else None


def export():
    """data/corrections.jsonl - one line per (request, wrong, right) with how often it was given."""
    f = _db()
    with f.lock:
        rows = f.db.execute("SELECT request, wrong, right, COUNT(*) FROM corrections GROUP BY norm, wrong, right").fetchall()
    with open(EXPORT, "w", encoding="utf-8") as fh:
        for req, wrong, right, n in rows:
            fh.write(json.dumps({"request": req, "wrong": wrong, "right": right, "count": n}, ensure_ascii=False) + "\n")
    return EXPORT, len(rows)


if __name__ == "__main__":
    import tempfile
    import ishti.facts
    ishti.facts._facts = ishti.facts.Facts(os.path.join(tempfile.mkdtemp(), "f.db"))
    EXPORT = os.path.join(tempfile.mkdtemp(), "c.jsonl")
    record("open the imsciences portal and check my attendance", "web_search", "browser")
    assert learned("Open the IMSciences portal and check my attendance!") == "browser"  # the same request: learned at once
    assert learned("open the imsciences portal and check my attendence") is None  # similar, one correction: not yet
    record("open the imsciences portal and check my attendance today", "web_search", "browser")
    assert learned("open the imsciences portal and check my attendence") == "browser"  # two agree: now it counts
    record("open the imsciences portal and check my attendence now", "browser", "web_fetch")
    record("open the imsciences portal and check my attendance please", "browser", "web_fetch")
    assert learned("open the imsciences portal and check my attendance pls") is None  # 2 vs 2: established routing stays
    path, n = export()
    assert n == 4 and json.loads(open(path, encoding="utf-8").readline())["right"] in ("browser", "web_fetch")
    print("[self-check] corrections OK - exact request learned at once, similar needs 2+ agreeing, ties don't override, export")
