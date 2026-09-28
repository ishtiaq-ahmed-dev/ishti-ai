"""Mood log (local SQLite, data/moodlog.db) — deterministic.

"rate my mood today 4", "mood 2: stressful day", "day was good: finished the thesis",
"my mood this week", "mood trend", "how have I been feeling".
Numbers are computed in code (averages, counts, trends); replies are supportive,
never diagnostic. Distress language always points to people/services.
"""
import datetime as dt
import os
import re
import sqlite3
import threading

from ishti import CONFIG

DB = os.path.join(os.path.dirname(CONFIG["memory"]["db_path"]), "moodlog.db")
_lock = threading.Lock()
EMOJI = {1: "😞", 2: "😕", 3: "😐", 4: "🙂", 5: "😄"}
WORDS = {"awful": 1, "terrible": 1, "horrible": 1, "bad": 2, "meh": 3, "okay": 3, "ok": 3,
         "fine": 3, "good": 4, "great": 4, "amazing": 5, "wonderful": 5, "fantastic": 5}
SUPPORT = {
    1: "Rough days happen. If low days stack up for 2+ weeks, talking to someone you trust is strength, not weakness.",
    2: "Not your best day. Rest counts as productive.",
    3: "A neutral day is still a day forward.",
    4: "Good day — worth noting what made it work.",
    5: "Great day! Save what worked as a note — future-you will want the reminder.",
}
I = re.I

DISTRESS = re.compile(r"\b(?:suicid\w*|kill myself|end it all|self[- ]?harm|hurt myself|no reason to live)\b", I)
URGENT = ("That sounds heavier than a mood log is for. Please talk to someone today — "
          "a person you trust, or a doctor. In an emergency call 1122 (Pakistan) or your local emergency number.")

RATE = re.compile(r"^\s*(?:rate\s+(?:my\s+)?(?:mood|day)(?:\s+today)?\s*(?:at|as)?\s*(?P<num>\d{1,2})(?:\s*/\s*5)?"
                  r"|mood\s+(?P<num2>\d{1,2}))\s*(?:[:\-—]\s*(?P<note>.+))?\s*$", I | re.S)
WORD_RATE = re.compile(r"^\s*(?:rate\s+)?(?:my\s+)?(?:day|mood)\s+(?:was\s+)?"
                       r"(?P<word>awful|terrible|horrible|bad|meh|okay|ok|fine|good|great|amazing|wonderful|fantastic)\b"
                       r"\s*[:\-—]?\s*(?P<note2>.*)$", I | re.S)
TREND = re.compile(r"^\s*(?:my\s+)?mood\s+trend\s*$|^\s*how\s+have\s+i\s+been\s+doing\s*(?:lately)?\s*\??$", I)
WEEK_M = re.compile(r"^\s*(?:my\s+)?mood\s+(?:this|last)\s+week\s*$|^\s*(?:my\s+)?weekly\s+mood\s*$", I)
FEELING = re.compile(r"^\s*how\s+have\s+i\s+been\s+feeling\s*(?:lately|recently)?\s*\??$", I)


def _db():
    os.makedirs(os.path.dirname(DB), exist_ok=True)
    c = sqlite3.connect(DB, timeout=10)
    c.execute("""CREATE TABLE IF NOT EXISTS moods(
        day TEXT PRIMARY KEY, score INTEGER NOT NULL, note TEXT,
        logged TEXT DEFAULT (datetime('now')))""")
    return c


def _norm_note(n):
    n = re.sub(r"\s+", " ", (n or "").strip()).rstrip(".!")
    return n[:200]


def _rate(day, score, note):
    if not 1 <= score <= 5:
        return "Rate the day 1–5 (1 lowest, 5 best), e.g. \"rate my mood today 4\"."
    with _lock, _db() as c:
        row = c.execute("SELECT score FROM moods WHERE day=?", (day,)).fetchone()
        c.execute("INSERT INTO moods(day, score, note) VALUES(?,?,?) "
                  "ON CONFLICT(day) DO UPDATE SET score=excluded.score, note=excluded.note",
                  (day, score, note))
    return (f"{EMOJI[score]} Day rated **{score}/5**" + (f" — \"{note}\"" if note else "") +
            f". {SUPPORT[score]}" +
            (f" (was {row[0]}/5)" if row else "") +
            " Say \"my mood this week\" for the trend.")


def _week():
    since = (dt.date.today() - dt.timedelta(days=6)).isoformat()
    with _lock, _db() as c:
        rows = c.execute("SELECT day, score, note FROM moods WHERE day >= ? ORDER BY day", (since,)).fetchall()
        total = c.execute("SELECT COUNT(*) FROM moods").fetchone()[0]
    if not rows:
        return "No moods logged yet. Try: \"rate my mood today 4\"."
    vals = [s for _d, s, _n in rows]
    avg = sum(vals) / len(vals)
    best, worst = max(vals), min(vals)
    lines = [f"- {day}: {EMOJI[s]} {s}/5" + (f" — {n}" if n else "") for day, s, n in rows]
    return (f"Mood, last 7 days ({len(rows)} logged, {total} total):\n" + "\n".join(lines) +
            f"\nAverage **{avg:.1f}/5** · best {best}/5 · worst {worst}/5")


def _trend():
    with _lock, _db() as c:
        rows = c.execute("SELECT day, score FROM moods ORDER BY day DESC LIMIT 14").fetchall()
    if len(rows) < 8:
        return ("Not enough data for a trend yet (need ~2 weeks). Keep rating days — "
                "even a 3 is information.")
    this_week = [s for _d, s in rows[:7]]
    prev_week = [s for _d, s in rows[7:14]]
    now = sum(this_week) / len(this_week)
    before = sum(prev_week) / len(prev_week)
    delta = now - before
    if delta >= 0.5:
        verdict = "trending up 📈"
    elif delta <= -0.5:
        verdict = "trending down 📉 — worth a kind week to yourself"
    else:
        verdict = "steady"
    return (f"Trend (this week {now:.1f}/5 vs previous {before:.1f}/5): **{verdict}**. "
            "A log is a mirror, not a diagnosis — you know your life best.")


def match(text):
    t = text.strip()
    return bool(RATE.match(t) or WORD_RATE.match(t) or TREND.match(t) or WEEK_M.match(t)
                or FEELING.match(t) or DISTRESS.search(t))


def run(text, session):
    t = text.strip()
    if DISTRESS.search(t):
        return {"reply": URGENT, "sources": []}
    m = RATE.match(t)
    if m:
        score = int(m.group("num") or m.group("num2"))
        note = (m.group("note") or "").strip().lstrip(":—- ")
        return {"reply": _rate(dt.date.today().isoformat(), score, _norm_note(note)), "sources": []}
    m = WORD_RATE.match(t)
    if m:
        score = WORDS[m.group("word").lower()]
        note = (m.group("note2") or "").strip().lstrip(":—- ")
        return {"reply": _rate(dt.date.today().isoformat(), score, _norm_note(note)), "sources": []}
    if TREND.match(t):
        return {"reply": _trend(), "sources": []}
    if WEEK_M.match(t) or FEELING.match(t):
        return {"reply": _week(), "sources": []}
    return None


if __name__ == "__main__":
    import tempfile
    DB = os.path.join(tempfile.mkdtemp(), "m.db")
    assert match("rate my mood today 4")
    assert match("mood 2: stressful day")
    assert match("mood 5 — great day at the park")
    assert match("day was good: finished the thesis")
    assert match("my mood this week")
    assert match("mood trend")
    assert match("how have I been feeling")
    assert not match("I drank 3 glasses of water")   # lifelog
    assert not match("log sleep 7.5 hours")          # sleeplog
    r = run("rate my mood today 4", {})
    assert "4/5" in r["reply"], r
    r2 = run("mood 5: great day at the park", {})
    assert "5/5" in r2["reply"] and "park" in r2["reply"], r2
    r3 = run("my mood this week", {})
    assert "Average" in r3["reply"] and "5.0/5" in r3["reply"], r3  # same-day re-rate overwrites
    # seed two prior weeks deterministically: -1..-6 = 2, -7..-13 = 4
    today = dt.date.today()
    with _lock, _db() as c:
        for i in range(1, 7):
            c.execute("INSERT OR REPLACE INTO moods(day, score, note) VALUES(?,?,?)",
                      ((today - dt.timedelta(days=i)).isoformat(), 2, ""))
        for i in range(7, 14):
            c.execute("INSERT OR REPLACE INTO moods(day, score, note) VALUES(?,?,?)",
                      ((today - dt.timedelta(days=i)).isoformat(), 4, ""))
    r4 = run("mood trend", {})
    assert "Trend" in r4["reply"] and "trending down" in r4["reply"], r4
    r5 = run("rate my mood today 9", {})
    assert "1–5" in r5["reply"], r5
    r6 = run("i want to kill myself", {})
    assert "1122" in r6["reply"], r6
    r7 = run("day was good: finished the thesis", {})
    assert "4/5" in r7["reply"] and "thesis" in r7["reply"], r7
    print("[self-check] moodlog OK - rate(word/num)/week/trend/safety gate, all local SQLite")
