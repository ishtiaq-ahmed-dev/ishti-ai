"""Action audit log with attribution (A3): every action ISHTI takes, and WHO initiated it.

Initiators - kept apart because they mean different things when something goes wrong:
  owner       the owner did it directly (a button in the app, an edit in "About you", a yes to a question)
  instructed  ISHTI did it on the owner's explicit instruction (the skill the request was routed to)
  judgment    ISHTI chose it itself during a multi-step task (planner steps, escalation to another skill, the
              browser engine's own clicks/typing, fall-through to another skill)
  standing    ISHTI did it on a standing rule the owner set earlier (a schedule, an automation)
T2/T3 entries record what was asked and when the owner confirmed. Local SQLite (data/audit.db); queried by
"what did you do while I was away", "show me today's actions". Secrets are masked before they are written."""
import contextlib
import datetime as dt
import os
import sqlite3
import threading
import time

from ishti import CONFIG
from ishti.test_mode import scratch_path

DB = scratch_path(os.path.join(os.path.dirname(CONFIG["memory"]["db_path"]), "audit.db"))
WHO = {"owner": "you, directly", "instructed": "me, on your instruction", "judgment": "me, my own judgment mid-task",
       "standing": "me, on a rule/schedule you set"}
_local = threading.local()
_lock = threading.Lock()


_conn_cache = {}


def _db():
    """A cached per-thread connection, created once per (thread, DB path). Measured 2026-10-01:
    connecting + CREATE TABLE IF NOT EXISTS on every record() cost ~12 ms of every request (a
    request records several times), and `with sqlite3.connect(...)` only commits - it never closes,
    so connections leaked until GC. The cache key includes the path because tests re-point DB.
    check_same_thread=False: the connection stays owned by its creating thread's cache slot.
    """
    key = (threading.get_ident(), DB)
    c = _conn_cache.get(key)
    if c is None:
        with context(skill=""):
            os.makedirs(os.path.dirname(DB), exist_ok=True)
        c = sqlite3.connect(DB, timeout=10, check_same_thread=False)
        # WAL + synchronous=NORMAL: the audit log is a pure log (no owner data; the module's contract
        # is "must never break the action"), so losing the last commits to a power cut is acceptable
        # and each record() stops paying a full-disk fsync (~3 ms -> measured ~0.1 ms). WAL is intact
        # across app crashes, so nothing is lost there.
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA synchronous=NORMAL")
        c.execute("""CREATE TABLE IF NOT EXISTS actions(ts REAL, session TEXT, channel TEXT, initiator TEXT, skill TEXT,
                     action TEXT, detail TEXT, tier TEXT, confirmed REAL, request TEXT)""")
        _conn_cache[key] = c
    return c


def _reset_cache():
    """Tests re-point DB to a scratch file: drop the cached connections so the next _db() reopens."""
    for c in _conn_cache.values():
        try:
            c.close()
        except sqlite3.Error:
            pass
    _conn_cache.clear()


def current():
    """The thread's action context: {"skill", "initiator", "session", "channel", "request", "redact"} (or {})."""
    return getattr(_local, "ctx", {})


@contextlib.contextmanager
def context(**kw):
    """Everything recorded (and every scope check) inside runs as this skill/initiator."""
    prev = current()
    _local.ctx = {**prev, **kw}
    try:
        yield _local.ctx
    finally:
        _local.ctx = prev


def _mask(value, secrets):
    for secret in ([secrets] if isinstance(secrets, str) else secrets or []):
        if secret and value:
            value = value.replace(secret, "•" * 8)
    return value


def record(action, detail="", *, skill=None, initiator=None, tier=None, confirmed=None, session=None, request=None):
    if getattr(_local, "recording", False):
        return
    ctx = current()
    secrets = ctx.get("redact") or (ctx.get("view") or {}).get("redact")  # the request may add secrets mid-run
    row = (time.time(), session or ctx.get("session", ""), ctx.get("channel", ""), initiator or ctx.get("initiator", "judgment"),
           skill or ctx.get("skill", ""), _mask(str(action), secrets)[:200], _mask(str(detail), secrets)[:500],
           tier or ctx.get("tier", ""), confirmed, _mask(str(request or ctx.get("request", "")), secrets)[:300])
    _local.recording = True
    try:
        with _lock:
            c = _db()
            with c:
                c.execute("INSERT INTO actions VALUES(?,?,?,?,?,?,?,?,?,?)", row)
    except (sqlite3.Error, OSError):
        print("[audit] not recorded: database unavailable")
    finally:
        _local.recording = False


def query(since=0.0, until=None, initiators=None, limit=200):
    sql = "SELECT ts, initiator, skill, action, detail, tier, confirmed, request, channel FROM actions WHERE ts>=? AND ts<=?"
    args = [since, until or time.time() + 1]
    if initiators:
        sql += f" AND initiator IN ({','.join('?' * len(initiators))})"
        args += list(initiators)
    with _db() as c:
        return c.execute(sql + " ORDER BY ts DESC LIMIT ?", args + [limit]).fetchall()


def last_owner_request(before):
    """When the owner last spoke to ISHTI (a routed request) before `before`, or 0."""
    with _db() as c:
        row = c.execute("SELECT MAX(ts) FROM actions WHERE action LIKE 'request:%' AND ts<? AND channel IN ('text','voice')",
                        (before,)).fetchone()
    return row[0] or 0.0


def report(text, now=None):
    """Plain-language answer for "what did you do today / while I was away / yesterday"."""
    import re
    now = now or time.time()
    today = dt.datetime.combine(dt.date.fromtimestamp(now), dt.time()).timestamp()
    if re.search(r"\bwhile\s+i\s+was\s+(?:away|gone|out|asleep)|since\s+(?:i\s+left|my\s+last\s+message)\b", text, re.I):
        # the current request was logged a moment ago: "away" is the gap before it
        since, label, who = last_owner_request(now - 5), "while you were away", ("standing", "judgment", "instructed")
    elif re.search(r"\byesterday\b", text, re.I):
        since, now, label, who = today - 86400, today, "yesterday", None
    else:
        since, label, who = today, "today", None
    rows = [r for r in query(since, now, who) if not r[3].startswith("request:") and not (label == "while you were away" and r[0] >= now - 5)]
    if not rows:
        return f"Nothing {label}." if label != "while you were away" else "Nothing - I didn't do anything while you were away."
    lines = []
    for ts, initiator, skill, action, detail, tier, confirmed, request, _ in rows[:40]:
        when = dt.datetime.fromtimestamp(ts).strftime("%H:%M" if label != "yesterday" else "%H:%M")
        extra = f" (confirmed {dt.datetime.fromtimestamp(confirmed):%H:%M})" if confirmed else ""
        lines.append(f"- {when} {skill or '-'}: {action}{extra} - by {WHO.get(initiator, initiator)}")
    counts = {}
    for r in rows:
        counts[r[1]] = counts.get(r[1], 0) + 1
    summary = ", ".join(f"{n} by {WHO.get(k, k)}" for k, n in counts.items())
    return f"What I did {label}: {len(rows)} action(s) - {summary}.\n" + "\n".join(lines)


if __name__ == "__main__":
    import tempfile
    DB = os.path.join(tempfile.mkdtemp(), "a.db")
    t = time.time()
    with context(skill="weather", initiator="instructed", session="s1", channel="text", request="weather tomorrow"):
        record("request: weather tomorrow")
        record("forecast fetched", "open-meteo")
    with context(skill="browser", initiator="judgment", redact=["Hunter2!pw"]):
        record("type into password", "typed Hunter2!pw")
    record("send WhatsApp to Ali", tier="T2", confirmed=t, initiator="owner", skill="whatsapp")
    with context(skill="automation", initiator="standing", channel="scheduled"):
        record("summarized new PDF")
    rows = query(t - 1)
    assert len(rows) == 5 and not any("Hunter2" in (r[3] + r[4]) for r in rows), rows  # secrets masked
    assert {r[1] for r in rows} == {"instructed", "judgment", "owner", "standing"}
    rep = report("show me today's actions")
    assert "4 action(s)" in rep and "(confirmed" in rep and "on a rule/schedule you set" in rep, rep
    away = report("what did you do while I was away", now=time.time() + 10)
    assert "summarized new PDF" in away and "request:" not in away, away
    print("[self-check] audit OK - attribution (owner/instructed/judgment/standing), T2 confirmation time, secrets masked, reports")
