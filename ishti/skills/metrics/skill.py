"""Health metrics (inventory domain 16 #4/#34): log and trend numbers, never advise.

"log weight 82", "log blood pressure 130 over 85", "steps yesterday 8000",
"my weight this month", "sugar trend", "metrics summary"

Store: data/metrics.db (metric, value, systolic/diastolic for BP, day). Averages, period
trend vs the previous period, min/max. The replies are strictly informational — no
diagnosis, no dosage, no targets beyond the owner's own numbers. T0, local write only.

Self-check uses a tempdir DB override like every other DB-backed skill.
"""
import datetime as dt
import os
import re
import sqlite3
import threading

from ishti import CONFIG
from ishti.skills import trace

DB = os.path.join(os.path.dirname(CONFIG["memory"]["db_path"]), "metrics.db")
_lock = threading.Lock()
I = re.I

LOG = re.compile(
    r"^\s*log\s+(?P<metric>weight|steps|sugar|glucose|heart\s*rate|pulse|temperature|temp|water|mood-?score)"
    r"(?:\s+(?P<when>yesterday|today))?\s+(?P<val>-?\d+(?:\.\d+)?)\s*(?:kg|kgs|lbs)?\s*$", I)
BP = re.compile(
    r"^\s*log\s+(?:blood\s+pressure|bp|my\s+bp)(?:\s+(?P<when>yesterday|today))?\s+"
    r"(?P<sys>\d{2,3})\s*(?:over|/)\s*(?P<dia>\d{2,3})\s*$", I)
ASK = re.compile(
    r"^\s*(?:(?:what\s+(?:is|was)\s+(?:my|the)\s+)?(?P<metric>weight|steps|sugar|glucose|heart\s*rate|pulse|blood\s+pressure|bp)"
    r"|my\s+(?P<metric2>weight|steps|sugar|glucose|heart\s*rate|pulse|blood\s+pressure|bp)\s+(?:this|last)\s+"
    r"(?P<period>week|month)|(?P<metric3>weight|steps|sugar|bp|blood\s+pressure)\s+(?:trend|history|summary))\b"
    r".*?\s*$", I)
SUMMARY = re.compile(r"^\s*(?:my\s+)?metrics\s+(?:summary|overview|all)\s*$", I)


def _db():
    os.makedirs(os.path.dirname(DB), exist_ok=True)
    c = sqlite3.connect(DB, timeout=10)
    c.execute("CREATE TABLE IF NOT EXISTS readings(id INTEGER PRIMARY KEY, metric TEXT, day TEXT, "
              "value REAL, sys INTEGER, dia INTEGER)")
    return c


def _day(when):
    return (dt.date.today() - dt.timedelta(days=1 if when and when.lower() == "yesterday" else 0)).isoformat()


def _log(metric, day, value=None, sys_=None, dia=None):
    with _lock, _db() as c:
        c.execute("INSERT INTO readings(metric, day, value, sys, dia) VALUES(?,?,?,?,?)",
                  (metric, day, value, sys_, dia))
    return f"Logged **{metric}**" + (f" {value:g}" if value is not None else f" {sys_}/{dia}") + f" for {day}."


def _periods(period):
    today = dt.date.today()
    if period == "month":
        start = today.replace(day=1)
        prev_end = start - dt.timedelta(days=1)
        prev_start = prev_end.replace(day=1)
    else:
        start = today - dt.timedelta(days=today.weekday())
        prev_end = start - dt.timedelta(days=1)
        prev_start = prev_end - dt.timedelta(days=6)
    return start.isoformat(), today.isoformat(), prev_start.isoformat(), prev_end.isoformat()


def _readings(metric, a, b):
    with _lock:
        return _db().execute(
            "SELECT day, value, sys, dia FROM readings WHERE metric=? AND day BETWEEN ? AND ? ORDER BY day",
            (metric, a, b)).fetchall()


def _report(metric, period=None):
    metric = metric.lower().replace("blood pressure", "bp").replace("heart rate", "pulse")
    if period:
        a, b, pa, pb = _periods(period)
        cur, prev = _readings(metric, a, b), _readings(metric, pa, pb)
    else:
        cur = _readings(metric, "2000-01-01", "2999-12-31")
        prev = []
    if not cur:
        return f"No **{metric}** readings logged yet."
    def avg(vals):
        return sum(vals) / len(vals)
    if metric == "bp":
        sys_ = [r[2] for r in cur if r[2]]
        dia = [r[3] for r in cur if r[3]]
        if not sys_:
            return f"No **{metric}** readings logged yet."
        out = (f"**{metric}** ({len(cur)} reading(s)"
               + (f", {period}" if period else "") + "): "
               f"avg {avg(sys_):.0f}/{avg(dia):.0f}, latest {cur[-1][2]}/{cur[-1][3]}, "
               f"range {min(sys_)}–{max(sys_)} systolic.")
        if prev:
            ps = avg([r[2] for r in prev if r[2]] or [0])
            if ps:
                out += f" Previous period avg {ps:.0f} systolic ({avg(sys_) - ps:+.0f})."
    else:
        vals = [r[1] for r in cur if r[1] is not None]
        if not vals:
            return f"No **{metric}** readings logged yet."
        out = (f"**{metric}** ({len(vals)} reading(s)"
               + (f", {period}" if period else "") + "): "
               f"avg {avg(vals):.4g}, latest {vals[-1]:g}, min {min(vals):g}, max {max(vals):g}.")
        if prev:
            pvals = [r[1] for r in prev if r[1] is not None]
            if pvals:
                out += f" Previous period avg {avg(pvals):.4g} ({avg(vals) - avg(pvals):+.4g})."
    out += "\nInformational only — not medical advice."
    return out


def _summary():
    with _lock:
        rows = _db().execute("SELECT metric, COUNT(*) n, MAX(day) FROM readings GROUP BY metric").fetchall()
    if not rows:
        return "No metrics logged yet. Try \"log weight 82\"."
    return "**Your metrics:**\n" + "\n".join(f"- {m}: {n} reading(s), latest {d}" for m, n, d in rows)


def match(text):
    t = text.strip()
    return bool(LOG.match(t) or BP.match(t) or SUMMARY.match(t) or
                (ASK.match(t) and re.search(r"\b(?:week|month|trend|history|summary|today|yesterday)\b|\bmy\b", t, I)))


def run(text, session):
    t = text.strip()
    if (m := BP.match(t)):
        trace(session, "metrics", "log bp")
        return {"reply": _log("bp", _day(m.group("when")), sys_=int(m.group("sys")), dia=int(m.group("dia"))),
                "sources": []}
    if (m := LOG.match(t)):
        metric = re.sub(r"\s+", " ", m.group("metric").lower())
        trace(session, "metrics", f"log {metric}")
        return {"reply": _log(metric, _day(m.group("when")), value=float(m.group("val"))), "sources": []}
    if SUMMARY.match(t):
        trace(session, "metrics", "summary")
        return {"reply": _summary(), "sources": []}
    m = ASK.match(t)
    if m:
        metric = (m.group("metric") or m.group("metric2") or m.group("metric3") or "").strip()
        period = m.group("period") if "period" in m.groupdict() else None
        trace(session, "metrics", "report")
        return {"reply": _report(metric, period), "sources": []}
    return None


if __name__ == "__main__":
    import tempfile
    DB = os.path.join(tempfile.mkdtemp(), "m.db")  # rebind: CONFIG was read at import

    assert match("log weight 82")
    assert match("log blood pressure today 130 over 85")
    assert match("steps yesterday 8000")
    assert match("my weight this month")
    assert match("sugar trend")
    assert match("metrics summary")
    assert not match("what is a healthy weight")     # advice question -> chat
    assert not match("log into my email")            # 'log' is not ours here

    run("log weight 82", {})
    run("log weight yesterday 83", {})
    run("log blood pressure today 130 over 85", {})
    r = run("my weight this week", {})
    assert "avg 82.5" in r["reply"], r
    assert "not medical advice" in r["reply"], r
    r2 = run("blood pressure trend", {})
    assert "130/85" in r2["reply"], r2
    r3 = run("metrics summary", {})
    assert "weight" in r3["reply"] and "bp" in r3["reply"], r3
    r4 = run("my steps this month", {})
    assert "No **steps** readings" in r4["reply"], r4
    print("[self-check] metrics OK - log/read bp + scalars, weekly/monthly avg + previous-period "
          "trend, informational only, local data/metrics.db")
