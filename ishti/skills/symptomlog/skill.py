"""Symptom diary (inventory domain 16 #3): record and summarise, never diagnose.

"log headache severity 7", "log nausea", "my symptoms this week", "headache history",
"doctor summary", "symptom stats"

Store: data/symptoms.db (day, symptom, severity, note). Views: recent entries, per-symptom
frequency + average severity, a doctor-visit summary (sorted by frequency then severity,
with first-seen and last-seen dates). Emergency language routes away by design — this skill
refuses to match chest pain / can't breathe class wording entirely (moodlog's safety gate
pattern: some asks must never land here). T0, local only.
"""
import datetime as dt
import os
import re
import sqlite3
import threading

from ishti import CONFIG
from ishti.skills import trace

DB = os.path.join(os.path.dirname(CONFIG["memory"]["db_path"]), "symptoms.db")
_lock = threading.Lock()
I = re.I

LOG = re.compile(
    r"^\s*log\s+(?P<sym>[a-z][\w \-]{2,40}?)(?:\s+(?:severity|level|pain)\s*(?P<sev>\d{1,2}))?"
    r"(?:\s*[:\-]\s*(?P<note>.+))?\s*$", I)
RECENT = re.compile(r"^\s*(?:my\s+)?symptoms?\s+(?:this|last)\s+(?:week|month)|^\s*(?:my\s+)?(?:recent|latest)\s+symptoms?\s*$", I)
HISTORY = re.compile(r"^\s*(?P<sym>[a-z][\w \-]{2,40}?)\s+(?:history|log|record)\s*$", I)
STATS = re.compile(r"^\s*symptom\s+stats?\s*$", I)
SYMPTOM_WORD = re.compile(r"\b(?:head\s*aches?|migraines?|fever|cough|pain|aches?|nausea|dizz(?:y|iness)|fatigue|tired(?:ness)?|"
                          r"cold|flu|allerg(?:y|ies)|rash|insomnia|anxiety|sore\s+throat|cramps?|vomit\w*|diarrh\w*|"
                          r"bloating|heartburn|acidity|asthma|wheez\w*|sneez\w*|itch\w*|swelling|bp|blood\s+pressure|sugar)\b", I)
DOCTOR = re.compile(r"^\s*(?:doctor|dr)\s+(?:summary|visit|prep|report)|^\s*(?:prepare|get)\s+(?:a\s+)?doctor\s+summary\s*$", I)
LIST = re.compile(r"^\s*(?:symptom\s+log|my\s+symptom\s+log)\s*$", I)
# never match: anything in the emergency class must not be filed as a diary entry
EMERGENCY = re.compile(r"\b(?:chest\s+pain|can'?t\s+breathe|cannot\s+breathe|stroke|suicid\w*|unconscious|seizure)\b", I)
SEV_NOTE = re.compile(r"^(?:severity|level|pain)?\s*(\d{1,2})?\s*[:\-]?\s*(.*)$", I)


def _db():
    os.makedirs(os.path.dirname(DB), exist_ok=True)
    c = sqlite3.connect(DB, timeout=10)
    c.execute("CREATE TABLE IF NOT EXISTS entries(id INTEGER PRIMARY KEY, day TEXT, symptom TEXT, "
              "severity INTEGER, note TEXT)")
    return c


def _norm(s):
    # strip trailing severity words left by the LOG regex ("smoke-test-symptom severity 3")
    s = re.sub(r"\s*(?:severity|level|pain)\s*\d{0,2}\s*$", "", s.strip(), flags=re.I)
    return re.sub(r"\s+", " ", s.lower()).strip()


def log(sym, severity=None, note=""):
    sym = _norm(sym)
    if severity is not None and not 1 <= int(severity) <= 10:
        return "Severity is 1–10."
    day = dt.date.today().isoformat()
    with _lock, _db() as c:
        c.execute("INSERT INTO entries(day, symptom, severity, note) VALUES(?,?,?,?)",
                  (day, sym, int(severity) if severity else None, (note or "").strip()[:300]))
    return (f"Logged **{sym}**" + (f" (severity {int(severity)}/10)" if severity else "") +
            f" for {day}." + (" I'm a diary, not a doctor — if this is severe or sudden, see a clinician."
                              if severity and severity >= 8 else ""))


def recent(days=7):
    cut = (dt.date.today() - dt.timedelta(days=days)).isoformat()
    with _lock:
        rows = _db().execute("SELECT day, symptom, severity, note FROM entries WHERE day>=? ORDER BY day DESC, id DESC",
                             (cut,)).fetchall()
    if not rows:
        return f"No symptoms logged in the last {days} day(s)."
    return (f"**Symptoms, last {days} day(s):**\n" +
            "\n".join(f"- {d}: {s}" + (f" ({sev}/10)" if sev else "") + (f" — {n}" if n else "")
                      for d, s, sev, n in rows[:20]))


def history(sym):
    sym = _norm(sym)
    with _lock:
        rows = _db().execute("SELECT day, severity, note FROM entries WHERE symptom=? ORDER BY day",
                             (sym,)).fetchall()
    if not rows:
        return f"No entries for **{sym}**."
    sevs = [r[1] for r in rows if r[1]]
    out = (f"**{sym.title()}**: {len(rows)} entr(y/ies), first {rows[0][0]}, last {rows[-1][0]}"
           + (f", average severity {sum(sevs) / len(sevs):.1f}/10, max {max(sevs)}" if sevs else "") + ".")
    out += "\n" + "\n".join(f"- {d}: " + (f"{s}/10" if s else "logged") for d, s, _n in rows[-10:])
    out += "\nA diary is not a diagnosis — show a clinician the pattern, not a conclusion."
    return out


def stats():
    with _lock:
        rows = _db().execute(
            "SELECT symptom, COUNT(*) n, AVG(severity) a, MIN(day), MAX(day) FROM entries "
            "GROUP BY symptom ORDER BY n DESC, a DESC").fetchall()
    if not rows:
        return "Nothing logged yet — \"log headache severity 7\"."
    lines = ["**Symptom stats** (by frequency, then severity):"]
    lines += [f"- {s}: {n}×, avg severity {a:.1f}/10 ({first} → {last})" if a else
              f"- {s}: {n}× ({first} → {last})" for s, n, a, first, last in rows[:12]]
    return "\n".join(lines)


def doctor_summary():
    with _lock:
        rows = _db().execute(
            "SELECT symptom, COUNT(*) n, AVG(severity) a, MIN(day), MAX(day) FROM entries "
            "GROUP BY symptom ORDER BY n DESC, a DESC").fetchall()
        total = _db().execute("SELECT COUNT(*) FROM entries").fetchone()[0]
    if not rows:
        return "Nothing to summarise yet."
    lines = [f"**Doctor-visit summary** — {total} logged entr(y/ies), most significant first:",
             "1. What: the symptoms below (self-reported severity 1–10).",
             "2. Pattern:"]
    lines += [f"   - {s}: {n}×, avg severity {a:.1f}/10, {first} → {last}" if a else
              f"   - {s}: {n}×, {first} → {last}" for s, n, a, first, last in rows[:10]]
    lines.append("3. This is your own log, verbatim — take it as conversation material, not a conclusion.")
    return "\n".join(lines)


def match(text):
    t = text.strip()
    if EMERGENCY.search(t):
        return False        # emergency class must never be filed as a diary entry
    if RECENT.match(t) or STATS.match(t) or DOCTOR.match(t) or LIST.match(t):
        return True
    # "<X> history" only when X is a symptom: "rock and roll music history" was filed as a symptom-log query
    if (h := HISTORY.match(t)) and (re.search(r"\bsymptoms?\b", t, I) or SYMPTOM_WORD.search(h["sym"])):
        return True
    m = LOG.match(t)
    return bool(m and not re.match(r"^\s*log\s+(in|into|out)\b", t, I))


def run(text, session):
    t = text.strip()
    if EMERGENCY.search(t):
        return None
    if RECENT.match(t):
        trace(session, "symptomlog", "recent")
        return {"reply": recent(7), "sources": []}
    if STATS.match(t):
        trace(session, "symptomlog", "stats")
        return {"reply": stats(), "sources": []}
    if DOCTOR.match(t):
        trace(session, "symptomlog", "doctor summary")
        return {"reply": doctor_summary(), "sources": []}
    if LIST.match(t):
        trace(session, "symptomlog", "list")
        return {"reply": recent(30), "sources": []}
    if (m := HISTORY.match(t)):
        trace(session, "symptomlog", "history")
        return {"reply": history(m.group("sym")), "sources": []}
    if (m := LOG.match(t)):
        trace(session, "symptomlog", "log")
        sym, sev, note = m.group("sym"), m.group("sev"), m.group("note") or ""
        # the lazy name can stop early and push "test-symptom severity 3" into the note:
        # recover the real symptom + severity from that note before filing
        if note and not sev:
            m2 = re.match(r"^([\w \-]+?)\s+(?:severity|level|pain)\s*(\d{1,2})$", note.strip(), I)
            if m2:
                sym = f"{sym} {m2.group(1)}".strip()
                sev = m2.group(2)
                note = ""
        return {"reply": log(sym, int(sev) if sev else None, note), "sources": []}
    return None


if __name__ == "__main__":
    import tempfile
    DB = os.path.join(tempfile.mkdtemp(), "s.db")  # rebind BEFORE touching the store

    assert match("log headache severity 7")
    assert match("log nausea")
    assert match("my symptoms this week")
    assert match("headache history")
    assert match("symptom stats")
    assert match("doctor summary")
    # emergency class never files here
    for bad in ("log chest pain severity 9", "log can't breathe", "log suicidal thoughts"):
        assert not match(bad), bad
    assert not match("log into my email")
    assert not match("log out")

    r = run("log headache severity 7", {})
    assert "Logged **headache** (severity 7/10)" in r["reply"], r
    run("log headache severity 4", {})
    run("log nausea", {})
    r2 = run("headache history", {})
    assert "2 entr" in r2["reply"] and "5.5/10" in r2["reply"], r2
    r3 = run("symptom stats", {})
    assert "headache: 2×" in r3["reply"], r3
    r4 = run("doctor summary", {})
    assert "Doctor-visit summary" in r4["reply"] and "not a conclusion" in r4["reply"], r4
    r5 = run("log back pain severity 12", {})
    assert "1–10" in r5["reply"], r5
    r6 = run("my symptoms this week", {})
    assert "headache" in r6["reply"], r6
    print("[self-check] symptomlog OK - log/recent/history/stats/doctor summary, severity bounded, "
          "emergency class never filed, diary-not-diagnosis wording")
