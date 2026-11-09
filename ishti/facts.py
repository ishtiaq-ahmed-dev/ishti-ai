"""Exact fact store (SQLite). One row per (entity, attribute): a change updates the row in
place and keeps the old value in `previous`, so the store never goes stale or duplicates.
Also holds the routing log used later to fine-tune the Laya checkpoint."""
from ishti.core.logutil import swallowed
import difflib
import os
import re
import sqlite3
import threading
import time

from ishti import CONFIG

FILLER = {"the", "my", "project", "folder", "repo", "directory", "dir", "a", "an", "our"}


def norm(name: str) -> str:
    # Unicode-aware ([^\W_]+ keeps letters/digits from every script, so Urdu/Pashto names
    # are not silently collapsed onto one empty key).
    words = re.findall(r"[^\W_]+", str(name or "").casefold(), re.UNICODE)
    return " ".join([w for w in words if w not in FILLER] or words)


class Facts:
    def __init__(self, path: str = None):
        from ishti.test_mode import scratch_path
        path = path or scratch_path(CONFIG["memory"]["db_path"])
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.lock = threading.Lock()
        # Speed (Cycle 4): the store is written on every request (route_log) and updated whenever the
        # owner tells ISHTI a fact. WAL + synchronous=NORMAL removes the per-commit full fsync
        # (~3 ms, the same fix audit.py measured) - a route-log row does not need to survive a power cut.
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self.db.execute("PRAGMA busy_timeout=5000")
        with self.lock, self.db:
            self.db.execute("""CREATE TABLE IF NOT EXISTS facts(
                key TEXT, attr TEXT, entity TEXT, value TEXT, previous TEXT, updated_at REAL,
                PRIMARY KEY(key, attr))""")
            self.db.execute("""CREATE TABLE IF NOT EXISTS route_log(
                ts REAL, text TEXT, skill TEXT, confidence REAL, source TEXT, detail TEXT)""")

    def set(self, entity: str, attr: str, value: str):
        """Upsert. Returns the previous value (None if new).

        The write key is exact/normalized only. Fuzzy matching belongs to reads: with the old
        `find()` here, "Sara" (ratio 0.89 to "Sarah") silently overwrote Sarah's values."""
        k = self._exact_key(entity) or norm(entity)
        if not k:
            return None
        from ishti.audit import SECRETISH
        if SECRETISH.search(f"{attr} is {value}"):
            # a password/PIN/key never lands in the plain fact store (it is shown back in "what do you know about me"
            # and sent with prompts); the vault is the only place for it. Every caller is covered here.
            return None
        with self.lock, self.db:
            row = self.db.execute("SELECT value FROM facts WHERE key=? AND attr=?", (k, attr)).fetchone()
            prev = row[0] if row else None
            self.db.execute(
                """INSERT INTO facts VALUES(?,?,?,?,?,?) ON CONFLICT(key, attr) DO UPDATE SET
                   value=excluded.value, entity=excluded.entity, previous=?, updated_at=excluded.updated_at""",
                (k, attr, entity.strip(), value, None, time.time(), prev))
        try:
            from ishti import events
            events.publish("memory_updated", {"type": "fact", "entity": entity, "attr": attr, "value": value})
        except Exception:
            swallowed('ishti/facts.py:set:53')
        _supersede_memories(attr, prev, value)
        return prev

    def keys(self):
        with self.lock:
            return [r[0] for r in self.db.execute("SELECT DISTINCT key FROM facts")]

    def find(self, name: str):
        """Exact key, then spacing-insensitive, then close spelling. None if unknown."""
        k = self._exact_key(name)
        if k:
            return k
        keys = self.keys()
        close = difflib.get_close_matches(norm(name), keys, n=1, cutoff=0.85)
        return close[0] if close else None

    def _exact_key(self, name: str):
        """Exact or spacing-insensitive key match only (no fuzzy spelling)."""
        k = norm(name)
        if not k:
            return None
        keys = self.keys()
        if k in keys:
            return k
        squashed = {x.replace(" ", ""): x for x in keys}
        return squashed.get(k.replace(" ", ""))

    def get(self, name: str) -> dict:
        """{'entity': display name, attr: value, ...} or {} if unknown."""
        k = self.find(name)
        if not k:
            return {}
        with self.lock:
            rows = self.db.execute("SELECT entity, attr, value FROM facts WHERE key=?", (k,)).fetchall()
        return {"entity": rows[-1][0], **{a: v for _, a, v in rows}} if rows else {}

    def forget(self, name: str, attr: str = None) -> int:
        k = self.find(name)
        if not k:
            return 0
        with self.lock, self.db:
            if attr:
                rc = self.db.execute("DELETE FROM facts WHERE key=? AND attr=?", (k, attr)).rowcount
            else:
                rc = self.db.execute("DELETE FROM facts WHERE key=?", (k,)).rowcount
        try:
            from ishti import events
            events.publish("memory_updated", {"type": "forget", "entity": name, "attr": attr})
        except Exception:
            swallowed('ishti/facts.py:forget:93')
        return rc

    def owner_profile(self, limit: int = 20) -> list:
        """The owner's personal facts [(attr, value)], newest first - settings the app keeps under
        "owner" (voice, languages, app preferences, standing instructions) are not facts about them.
        ponytail: capped at `limit` rows so a long-lived owner doesn't grow every chat prompt."""
        with self.lock:
            rows = self.db.execute("SELECT attr, value FROM facts WHERE key='owner' ORDER BY updated_at DESC").fetchall()
        return [(a, v) for a, v in rows if a not in OWNER_SETTINGS and not a.startswith("prefers_")][:limit]

    def mentioned_in(self, text: str) -> list:
        """Facts whose entity appears (whole words) in text: [(entity, attr, value)]."""
        t = f" {norm(text)} "
        hits = [k for k in self.keys() if f" {k} " in t]
        with self.lock:
            return [r for k in hits for r in
                    self.db.execute("SELECT entity, attr, value FROM facts WHERE key=?", (k,))]

    def log_route(self, text, skill, confidence, source, detail=""):
        from ishti.audit import SECRETISH  # a typed key/password never lands in the routing log verbatim
        text, detail = SECRETISH.sub("[redacted]", text or ""), SECRETISH.sub("[redacted]", detail or "")
        with self.lock, self.db:
            self.db.execute("INSERT INTO route_log VALUES(?,?,?,?,?,?)",
                            (time.time(), text, skill, confidence, source, detail))


OWNER_SETTINGS = {"entity", "instructions", "voice", "enabled_languages"}


def _supersede_memories(attr, prev, value):
    """Memory rows stating the old value of a fact that just changed are superseded (valid_until stamped,
    conflict logged) by the same sentence with the new value. ponytail: whole-word text substitution - right for
    "lives in Peshawar" -> "lives in Lahore"; a value too short or numeric (counters, settings) is skipped."""
    prev, value = str(prev or "").strip(), str(value or "").strip()
    if (not prev or prev.lower() == value.lower() or len(prev) < 3 or prev.replace(".", "").isdigit()
            or attr in OWNER_SETTINGS or attr.startswith("prefers_")):
        return
    try:
        import re
        from ishti.memstore import memory
        old_word = re.compile(rf"\b{re.escape(prev)}\b", re.I)
        for row in memory.search(text=prev, mtype="semantic", limit=10):
            if old_word.search(row["content"]):
                memory.supersede(row["id"], old_word.sub(value, row["content"]), source="explicit_user")
    except Exception as _exc:
        swallowed('ishti/facts.py:_supersede_memories', _exc)
_facts = None


def get_facts() -> Facts:
    global _facts
    if _facts is None:
        _facts = Facts()
    return _facts


if __name__ == "__main__":
    import tempfile
    f = Facts(os.path.join(tempfile.mkdtemp(), "t.db"))
    assert f.set("Project Falcon", "location", r"D:\work\falcon") is None
    assert f.get("falcon")["location"] == r"D:\work\falcon"          # "project" is filler
    assert f.set("falcon", "location", r"D:\archive\falcon") == r"D:\work\falcon"  # update, not append
    assert len(f.keys()) == 1 and f.get("the falcon project")["location"] == r"D:\archive\falcon"
    assert f.find("falcn") == "falcon" and f.find("zebra") is None
    assert f.mentioned_in("open falcon please")[0][2] == r"D:\archive\falcon"
    assert f.forget("Falcon") == 1 and f.get("falcon") == {}
    print("[self-check] facts OK - upsert keeps one row, fuzzy lookup, forget")
