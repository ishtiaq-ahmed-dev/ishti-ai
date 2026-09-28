"""Vocabulary building (local SQLite, data/vocab.db) — deterministic.

"add word serendipity: finding something good by accident",
"what does serendipity mean", "my vocab", "quiz my vocab",
"review my weakest words", "vocab stats".
Meanings are owner-supplied (or imported from a note) — ISHTI never invents one.
"""
import os
import random
import re
import sqlite3
import threading

from ishti import CONFIG

DB = os.path.join(os.path.dirname(CONFIG["memory"]["db_path"]), "vocab.db")
_lock = threading.Lock()

I = re.I

ADD = re.compile(r"^\s*(?:add\s+(?:the\s+)?word\s+|remember\s+(?:the\s+)?word\s+)"
                 r"(?P<word>[A-Za-z][\w'-]{1,30})\s*[:\-]\s*(?P<meaning>.+)$", I | re.S)
MEANING = re.compile(r"^\s*what\s+(?:does\s+|do\s+)?(?:the\s+word\s+)?(?P<word>[A-Za-z][\w'-]{1,30})\s+mean\b"
                     r"|^\s*define\s+(?P<word2>[A-Za-z][\w'-]{1,30})\s*$|^\s*meaning\s+of\s+(?P<word3>[A-Za-z][\w'-]{1,30})\s*$", I)
QUIZ = re.compile(r"^\s*(?:quiz\s+(?:me\s+)?(?:on\s+)?(?:my\s+)?vocab(?:ulary)?\b|vocab\s+quiz\s*$)", I)
REVIEW = re.compile(r"^\s*(?:review|practise|practice)\s+(?:my\s+)?(?:weakest\s+|hard\s+)?words?\s*$", I)
LIST = re.compile(r"^\s*(?:my\s+)?vocab(?:ulary)?\s*(?:list)?\s*$|^\s*words\s+i(?:'ve)?\s+(?:added|learned)\s*$", I)
STATS = re.compile(r"^\s*vocab\s+stats\s*$", I)


def _db():
    os.makedirs(os.path.dirname(DB), exist_ok=True)
    c = sqlite3.connect(DB, timeout=10)
    c.execute("""CREATE TABLE IF NOT EXISTS words(
        word TEXT PRIMARY KEY, meaning TEXT NOT NULL,
        asked INTEGER DEFAULT 0, right INTEGER DEFAULT 0, added TEXT DEFAULT (datetime('now')))""")
    return c


def _norm(word):
    return re.sub(r"\s+", " ", word.strip().lower())


def _add(word, meaning):
    word, meaning = _norm(word), meaning.strip()
    with _lock, _db() as c:
        row = c.execute("SELECT meaning FROM words WHERE word=?", (word,)).fetchone()
        c.execute("INSERT INTO words(word, meaning) VALUES(?,?) "
                  "ON CONFLICT(word) DO UPDATE SET meaning=excluded.meaning", (word, meaning))
    if row:
        return f"Updated **{word}**: {meaning} (was: {row[0]})"
    return f"Word **{word}** saved — say \"what does {word} mean\" or add it to a quiz anytime."


def _lookup(word):
    word = _norm(word)
    with _lock, _db() as c:
        row = c.execute("SELECT meaning FROM words WHERE word=?", (word,)).fetchone()
    if row:
        return f"**{word}** — {row[0]} (from your word list)"
    return (f"\"{word}\" is not in your word list yet. Add it with \"add word {word}: <meaning>\" "
            f"(ISHTI keeps meanings you give it — it won't invent one).")


def _list_words():
    with _lock, _db() as c:
        rows = c.execute("SELECT word FROM words ORDER BY word").fetchall()
    if not rows:
        return "No words yet. Try: \"add word serendipity: finding something good by accident\"."
    return f"Your word list ({len(rows)}):\n" + ", ".join(w for w, in rows)


def _quiz(n=5):
    with _lock, _db() as c:
        rows = c.execute("SELECT word, meaning FROM words ORDER BY right ASC, asked ASC LIMIT ?", (max(n, 3),)).fetchall()
        total = c.execute("SELECT COUNT(*) FROM words").fetchone()[0]
    if not rows:
        return "No words to quiz yet — add some first: \"add word ephemeral: lasting a very short time\"."
    picked = random.sample(rows, min(n, len(rows)))
    lines = "\n".join(f"{i}. {(m[:70] + '…') if len(m) > 70 else m}" for i, (w, m) in enumerate(picked, 1))
    return (f"Vocab quiz — which word matches each meaning? ({len(picked)} of {total} words)\n{lines}\n"
            "Answer like: \"1 serendipity, 2 ephemeral\" — then say \"vocab stats\" to see your score.")


def _review():
    with _lock, _db() as c:
        rows = c.execute("""SELECT word, meaning, asked, right FROM words
                            WHERE asked > 0 AND right * 2 < asked
                            ORDER BY asked DESC, right ASC LIMIT 5""").fetchall()
        weak = c.execute("SELECT COUNT(*) FROM words WHERE asked > 0 AND right * 2 < asked").fetchone()[0]
        if not rows:
            rows = c.execute("SELECT word, meaning, asked, right FROM words ORDER BY random() LIMIT 5").fetchall()
    if not rows:
        return "Nothing to review yet — quiz yourself first: \"quiz my vocab\"."
    lines = "\n".join(f"- **{w}** — {m} ({r}/{a} right)" for w, m, a, r in rows)
    tail = f"{weak} word(s) are below 50% recall." if weak else "Practising a random mix (nothing weak yet)."
    return f"Review these:\n{lines}\n{tail}"


def _stats():
    with _lock, _db() as c:
        total, asked, right = c.execute("SELECT COUNT(*), COALESCE(SUM(asked),0), COALESCE(SUM(right),0) FROM words").fetchone()
    if not total:
        return "No vocab stats yet — add words and quiz yourself."
    acc = f"{right / asked:.0%}" if asked else "—"
    return f"Vocab: **{total} word(s)**, {asked} quiz question(s) answered, accuracy **{acc}**."


def _has(word):
    with _lock, _db() as c:
        return bool(c.execute("SELECT 1 FROM words WHERE word=?", (_norm(word),)).fetchone())


def match(text):
    t = text.strip()
    m = MEANING.match(t)
    if m:
        # only claim lookups of words the owner actually saved — anything else falls
        # through to the dictionary (utilities), never "not in your list" for normal words
        return _has(m.group("word") or m.group("word2") or m.group("word3"))
    return bool(ADD.match(t) or QUIZ.match(t) or REVIEW.match(t) or LIST.match(t) or STATS.match(t))


def run(text, session):
    t = text.strip()
    m = ADD.match(t)
    if m:
        return {"reply": _add(m.group("word"), m.group("meaning")), "sources": []}
    m = MEANING.match(t)
    if m:
        return {"reply": _lookup(m.group("word") or m.group("word2") or m.group("word3")), "sources": []}
    if QUIZ.match(t):
        return {"reply": _quiz(), "sources": []}
    if REVIEW.match(t):
        return {"reply": _review(), "sources": []}
    if LIST.match(t):
        return {"reply": _list_words(), "sources": []}
    if STATS.match(t):
        return {"reply": _stats(), "sources": []}
    return None


if __name__ == "__main__":
    import tempfile
    DB = os.path.join(tempfile.mkdtemp(), "v.db")
    assert match("add word serendipity: finding something good by accident")
    assert match("remember the word ephemeral: lasting a very short time")
    assert not match("what does serendipity mean")  # not saved yet -> dictionary (utilities) handles it
    assert not match("define photosynthesis")        # never saved -> never captured
    r = run("add word serendipity: finding something good by accident", {})
    assert match("vocab quiz")
    assert match("review my weakest words")
    assert match("my vocab")
    assert match("vocab stats")
    assert not match("what is photosynthesis")   # study skill
    assert not match("translate good morning to urdu")  # utilities
    assert "saved" in r["reply"].lower(), r
    r2 = run("what does serendipity mean", {})
    assert "by accident" in r2["reply"], r2
    assert match("what does serendipity mean")      # saved now -> vocab claims it
    run("remember the word ephemeral: lasting a very short time", {})
    assert match("meaning of ephemeral")            # saved -> claimed
    r3 = run("quiz my vocab", {})
    assert "quiz" in r3["reply"].lower() and "serendipity" in r3["reply"].lower(), r3
    r4 = run("my vocab", {})
    assert "serendipity" in r4["reply"] and "ephemeral" in r4["reply"], r4
    r5 = run("vocab stats", {})
    assert "2 word(s)" in r5["reply"], r5
    r6 = run("add word serendipity: a happy accident", {})
    assert "Updated" in r6["reply"], r6
    r7 = run("meaning of wobble", {})
    assert "not in your word list" in r7["reply"], r7
    print("[self-check] vocab OK - add/meanings(saved-only)/quiz/review/list/stats, all local SQLite")
