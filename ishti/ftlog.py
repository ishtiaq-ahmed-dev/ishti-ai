"""Behavior-data collection for the brain fine-tuning pipeline (Part 3).

Collection (passive, from the dispatcher): every request->skill->answer exchange is
logged with the routing decision and a quality tag (accepted / corrected / failed).
Nothing is trained on until it is curated: near-duplicates collapsed, secrets
removed, private relationship-graph facts and client data excluded, one-off
ambiguous exchanges dropped in favour of repeated patterns and clear corrections.
Export: JSONL chat-format ({"messages": [...]}) ready for a LoRA/QLoRA job.
Storage: SQLite (ishti.db, table ftlog). The raw log stays on this PC; export()
is the only thing that leaves it (into a local training folder).
"""
import json
import os
import re
import sqlite3
import sys
import threading
import time

if __package__ in (None, ""):  # run directly: python ishti/ftlog.py
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ishti import ROOT
from ishti.test_mode import scratch_path

DB_PATH = scratch_path(os.path.join(ROOT, "data", "ishti.db"))  # shared facts DB, own table
LOCK = threading.RLock()
_conn = None
MIN_TURNS = 2  # an exportable example needs a real exchange, not "hi" / "Hello!"

# redaction: anything that looks like a secret never reaches the training set.
# A labelled value ("password Hunter2x", "api_key=sk-...") is swallowed whole - label AND value.
SECRETISH = re.compile(
    r"(?i)\b(?:password|passwd|pwd|pass|token|api[_-]?key|secret|pin|otp|cvv|cvc|credit\s*card|"
    r"card\s*number|iban|iban\s*no|account\s*number)\b\s*(?:is|=|:)?\s*[\"']?[A-Za-z0-9_/.+-]{3,}[\"']?"
    r"|\b(?:sk|pk|ghp|gho|xox[baprs]|AKIA)[A-Za-z0-9_-]{8,}")
EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
PHONE = re.compile(r"\b(?:\+92|0)3\d{2}[- ]?\d{7}\b")


_conn_path = None  # which file _conn is open on (DB_PATH can be re-pointed by tests)


def conn():
    global _conn, _conn_path
    with LOCK:
        path = os.path.abspath(DB_PATH)
        if _conn is None or _conn_path != path:
            try:
                _conn.close()
            except Exception:
                pass
            os.makedirs(os.path.dirname(path), exist_ok=True)
            _conn = sqlite3.connect(path, check_same_thread=False)
            _conn.row_factory = sqlite3.Row
            _conn.execute("PRAGMA journal_mode=WAL")
            _conn.execute("""CREATE TABLE IF NOT EXISTS ftlog(
                id INTEGER PRIMARY KEY, ts REAL, request TEXT, answer TEXT, skill TEXT,
                source TEXT, quality TEXT DEFAULT 'accepted', channel TEXT DEFAULT 'text',
                domain TEXT DEFAULT '')""")
            _conn.commit()
            _conn_path = path
        return _conn


def redact(text):
    """Secrets, card numbers, phone numbers out of anything that could be trained on."""
    out = SECRETISH.sub("[redacted]", text or "")
    out = PHONE.sub("[redacted]", out)
    out = EMAIL.sub("[email]", out)
    return out


def record(request, answer, skill, source="", quality="accepted", channel="text"):
    """One exchange. Called by the dispatcher's _finish; corrections overwrite quality."""
    if not request or not answer:
        return None
    c = conn()
    with LOCK, c:
        cur = c.execute("INSERT INTO ftlog(ts, request, answer, skill, source, quality, channel) "
                        "VALUES(?,?,?,?,?,?,?)",
                        (time.time(), redact(request)[:4000], redact(answer)[:6000], skill or "none",
                         source or "", quality, channel or "text"))
        return cur.lastrowid


def mark_quality(request, quality):
    """The owner corrected this request (or confirmed it): update its quality tag.
    Matches on the normalized request text."""
    key = re.sub(r"\W+", " ", (request or "").lower()).strip()[:200]
    if not key:
        return 0
    c = conn()
    with LOCK, c:
        n = c.execute("UPDATE ftlog SET quality=? WHERE quality='accepted' AND "
                      "replace(replace(lower(request), '?', ''), ',', '') LIKE ? || '%'",
                      (quality, key[:150])).rowcount
    return n


def stats():
    c = conn()
    with LOCK:
        total = c.execute("SELECT COUNT(*) FROM ftlog").fetchone()[0]
        by_q = dict(c.execute("SELECT quality, COUNT(*) FROM ftlog GROUP BY quality").fetchall())
        by_s = dict(c.execute("SELECT skill, COUNT(*) FROM ftlog GROUP BY skill ORDER BY 2 DESC LIMIT 8").fetchall())
        span = c.execute("SELECT MIN(ts), MAX(ts) FROM ftlog").fetchone()
    return {"total": total, "by_quality": by_q, "top_skills": by_s,
            "first": span[0], "last": span[1]}


def _norm(text):
    return re.sub(r"\W+", " ", (text or "").lower()).strip()


def curate(rows):
    """Dedup near-identical examples, drop too-short exchanges, keep the highest-value
    rows first (corrections > errors-turned-good > accepted), balance across skills."""
    seen, out = set(), []
    corr = [r for r in rows if r["quality"] == "corrected"]
    acc = [r for r in rows if r["quality"] == "accepted"]
    for r in corr + acc:  # corrections carry the most signal, so they're kept preferentially
        key = _norm(r["request"])[:160]
        if key in seen or len(r["request"].split()) < MIN_TURNS or not r["answer"].strip():
            continue
        seen.add(key)
        out.append(dict(r))
    # balance: at most 2x the best-represented skill per other skill is fine, but cap any
    # single skill at 40% of the set so a heavy week doesn't skew training
    cap = max(1, int(len(out) * 0.4))
    counts, balanced = {}, []
    for r in out:
        counts[r["skill"]] = counts.get(r["skill"], 0) + 1
        if counts[r["skill"]] <= cap:
            balanced.append(r)
    return balanced


def export(path=None, limit=5000):
    """Curated set -> JSONL chat format. Returns (path, n_examples). Private sources
    (channel 'voice' is fine; 'scheduled' automation runs are dropped as noise) and any
    row whose text still contains redaction markers keeps the marker, not the secret."""
    c = conn()
    with LOCK:
        rows = [dict(r) for r in c.execute("SELECT * FROM ftlog WHERE channel != 'scheduled' "
                                           "ORDER BY ts DESC LIMIT ?", (limit,)).fetchall()]
    data = curate(rows)
    out_path = path or os.path.join(ROOT, "data", "finetune", f"dataset_{time.strftime('%Y%m%d')}.jsonl")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    import io
    n = 0
    with io.open(out_path, "w", encoding="utf-8") as fh:
        for r in data:
            example = {"messages": [
                {"role": "user", "content": r["request"]},
                {"role": "assistant", "content": r["answer"]}],
                "meta": {"skill": r["skill"], "quality": r["quality"]}}
            fh.write(json.dumps(example, ensure_ascii=False) + "\n")
            n += 1
    return out_path, n


def ready_for_training(min_examples=300):
    """The fine-tune job shouldn't run on crumbs."""
    s = stats()
    return s["total"] >= min_examples, s


if __name__ == "__main__":
    import tempfile
    DB_PATH = os.path.join(tempfile.mkdtemp(), "t.db")
    _conn = None
    # redaction first
    txt = redact("my password is Hunter2x and api_key=sk-abcdef123456789 call 0300-1234567 or a@b.com")
    assert "Hunter2x" not in txt and "sk-abcdef" not in txt and "0300-1234567" not in txt, txt
    assert "a@b.com" not in txt
    # record + correct
    r1 = record("what is 17 times 23", "391", "chat", "rule", "accepted")
    r2 = record("search the latest nvidia gpu", "couldn't find it", "web_search", "laya", "accepted")
    assert mark_quality("search the latest nvidia gpu", "corrected") == 1
    s = stats()
    assert s["total"] == 2 and s["by_quality"]["corrected"] == 1, s
    # curation: near-dup dropped, short dropped, corrections kept
    record("hi", "Hello!", "chat", "reflex", "accepted")
    for i in range(5):
        record(f"how much ram am i using - v{i}", "16 GB used", "system", "rule", "accepted")
    record("what is 17 times 23", "392", "chat", "rule", "accepted")  # near-dup of r1
    rows = [dict(r) for r in conn().execute("SELECT * FROM ftlog").fetchall()]
    kept = curate(rows)
    keys = [_norm(r["request"])[:160] for r in kept]
    assert len(keys) == len(set(keys)), "dedup failed"
    assert not any(r["request"] == "hi" for r in kept), "short exchange should drop"
    assert all(not (r["request"] == "what is 17 times 23" and r["answer"] == "392") for r in kept), "dup kept"
    assert any(r["quality"] == "corrected" for r in kept), "corrections must be kept"
    # export
    path, n = export()
    assert n == len(kept) and os.path.isfile(path), (n, len(kept))
    with open(path, encoding="utf-8") as fh:
        first = json.loads(fh.readline())
    assert first["messages"][0]["role"] == "user" and first["meta"]["skill"], first
    ok, s = ready_for_training(min_examples=3)
    assert ok and s["total"] >= 3
    print(f"[self-check] ftlog OK - redaction, quality marks, dedup/balance, JSONL export ({n} examples)")
