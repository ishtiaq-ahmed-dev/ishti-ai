"""Learning store: one SQLite file (data/learning.db) for the whole learning loop.

Separate from the fact store on purpose (spec section 22): `facts`/`memory.db` is *what ISHTI
knows now* (fast, changeable, deletable); this file is *what ISHTI observed and learned* -
interactions, feedback, mistakes, lessons, candidates, workflows, entities and the offline
training dataset. Lessons can write back into the fact store; the fact store never depends
on this one.

Fail-soft by design: every helper swallows database errors and returns empty values, because
a learning write must never break a reply. Tests repoint the file with set_db_path(); nothing
else changes DB_PATH at runtime.
"""
import json
import os
import sqlite3
import threading
import time
import uuid

from ishti import CONFIG, ROOT

DEFAULT_DB = os.path.join(ROOT, "data", "learning.db")
DEFAULTS = {
    "enabled": True,
    "proactive": False,
    "thresholds": {"weak": 0.3, "possible": 0.6, "probable": 0.8, "strong": 0.95},
    "evidence": {"similar_request": 2, "widen_scope": 3, "candidate_rule": 3, "workflow": 3},
    "temporary_ttl_s": 3600,
    "entities": {"ambiguity_margin": 0.2, "min_confidence": 0.6},
    "dataset": {"min_quality": 0.6, "per_skill_cap": 0.4},
}
SCHEMA = """
CREATE TABLE IF NOT EXISTS interactions(
  id TEXT PRIMARY KEY, ts REAL, session_id TEXT, channel TEXT, request TEXT, skill TEXT,
  decision TEXT, memories TEXT, considered TEXT, result TEXT, status TEXT, reward REAL,
  task_id TEXT, step_no INTEGER, domain TEXT, source TEXT);
CREATE INDEX IF NOT EXISTS ix_interactions_ts ON interactions(ts);
CREATE TABLE IF NOT EXISTS tasks(
  task_id TEXT PRIMARY KEY, ts REAL, goal TEXT, steps TEXT, final_status TEXT,
  correction TEXT, lessons TEXT, reward REAL, domain TEXT, source TEXT);
CREATE TABLE IF NOT EXISTS feedback(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, interaction_id TEXT, kind TEXT, polarity TEXT,
  target TEXT, message TEXT, error_type TEXT, incorrect TEXT, correct TEXT, source TEXT,
  confidence REAL, applied TEXT, task_id TEXT);
CREATE INDEX IF NOT EXISTS ix_feedback_ts ON feedback(ts);
CREATE TABLE IF NOT EXISTS mistakes(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, interaction_id TEXT, error_type TEXT,
  signature TEXT, wrong TEXT, right TEXT, domain TEXT, note TEXT);
CREATE INDEX IF NOT EXISTS ix_mistakes_sig ON mistakes(signature);
CREATE TABLE IF NOT EXISTS lessons(
  lesson_id TEXT PRIMARY KEY, created REAL, updated REAL, kind TEXT, scope TEXT, context TEXT,
  statement TEXT, incorrect TEXT, correct TEXT, evidence INTEGER DEFAULT 1, positive INTEGER DEFAULT 0,
  negative INTEGER DEFAULT 0, confidence REAL, status TEXT, source TEXT, priority INTEGER DEFAULT 40,
  action TEXT, expires REAL, entity TEXT, occasions INTEGER DEFAULT 1, confirmed INTEGER DEFAULT 0,
  note TEXT);
CREATE INDEX IF NOT EXISTS ix_lessons_status ON lessons(status);
CREATE TABLE IF NOT EXISTS lesson_events(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, lesson_id TEXT, delta REAL, reason TEXT,
  interaction_id TEXT, evidence TEXT);
CREATE TABLE IF NOT EXISTS candidates(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, kind TEXT, signature TEXT, payload TEXT,
  status TEXT, question TEXT, source TEXT, answered REAL);
CREATE TABLE IF NOT EXISTS workflows(
  id INTEGER PRIMARY KEY AUTOINCREMENT, signature TEXT UNIQUE, name TEXT, steps TEXT, count INTEGER,
  first_seen REAL, last_seen REAL, status TEXT, confidence REAL);
CREATE TABLE IF NOT EXISTS entities(
  id INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT UNIQUE, name TEXT, org TEXT, email TEXT, phone TEXT,
  relationship TEXT, aliases TEXT, confidence REAL, mentions INTEGER DEFAULT 1, last_seen REAL,
  source TEXT, note TEXT);
CREATE TABLE IF NOT EXISTS entity_mentions(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, entity_key TEXT, channel TEXT, detail TEXT, source TEXT);
CREATE TABLE IF NOT EXISTS dataset(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, instruction TEXT, context TEXT, incorrect TEXT,
  correct TEXT, correction TEXT, reason TEXT, quality REAL, skill TEXT, domain TEXT, source TEXT,
  tags TEXT, exported INTEGER DEFAULT 0);
CREATE INDEX IF NOT EXISTS ix_dataset_skill ON dataset(skill);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT, updated REAL);
"""


LOCK = threading.RLock()
_conn = None
_conn_path = None
DB_PATH_OVERRIDE = None


def cfg():
    """The `learning` config block merged over the built-in defaults."""
    conf = dict(DEFAULTS)
    conf.update({k: v for k, v in (CONFIG.get("learning") or {}).items() if not k.startswith("note")})
    for k in ("thresholds", "evidence", "entities", "dataset"):
        conf[k] = {**DEFAULTS[k], **(conf.get(k) or {})}
    return conf


def enabled():
    return bool(cfg().get("enabled", True))


def db_path():
    return cfg().get("db_path") or DEFAULT_DB


def set_db_path(path):
    """Tests only: point the store at a throwaway file and forget the open connection."""
    global DB_PATH_OVERRIDE
    DB_PATH_OVERRIDE = path
    reset()


def _path():
    return DB_PATH_OVERRIDE or db_path()


def conn():
    """The shared connection, opened lazily and re-opened when the path moves (tests)."""
    global _conn, _conn_path
    with LOCK:
        path = os.path.abspath(_path())
        if _conn is None or _conn_path != path:
            try:
                _conn.close()
            except Exception:
                pass
            os.makedirs(os.path.dirname(path), exist_ok=True)
            _conn = sqlite3.connect(path, check_same_thread=False)
            _conn.row_factory = sqlite3.Row
            _conn.execute("PRAGMA journal_mode=WAL")
            _conn.executescript(SCHEMA)
            _conn.commit()
            _conn_path = path
        return _conn


def reset():
    """Close the open connection (tests, or after an explicit forget-everything)."""
    global _conn, _conn_path
    with LOCK:
        try:
            if _conn is not None:
                _conn.close()
        except Exception:
            pass
        _conn, _conn_path = None, None


def now():
    return time.time()


def uid(prefix=""):
    return f"{prefix}{uuid.uuid4()}"


def rows(sql, args=()):
    try:
        with LOCK:
            return [dict(r) for r in conn().execute(sql, args).fetchall()]
    except Exception:
        return []


def row(sql, args=()):
    got = rows(sql, args)
    return got[0] if got else None


def write(sql, args=()):
    """INSERT/UPDATE/DELETE; returns lastrowid or rowcount, 0 on any error."""
    try:
        with LOCK, conn() as c:
            cur = c.execute(sql, args)
            return cur.lastrowid or cur.rowcount
    except Exception:
        return 0


def add(table, **cols):
    keys = [k for k, v in cols.items() if v is not None]
    sql = f"INSERT INTO {table}({','.join(keys)}) VALUES({','.join('?' for _ in keys)})"
    return write(sql, tuple(cols[k] for k in keys))


def js(value, default=None):
    """JSON text -> object, never raises."""
    if value is None or value == "":
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def dump(value):
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return "{}"


def setting(key, default=None):
    """Runtime override (the `settings` table) falling back to config.json."""
    got = row("SELECT value FROM settings WHERE key=?", (key,))
    if got:
        return js(got["value"], default)
    return cfg().get(key, default)


def set_setting(key, value):
    write("INSERT INTO settings(key,value,updated) VALUES(?,?,?) "
          "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated=excluded.updated",
          (key, dump(value), now()))
    return setting(key)


def proactive():
    return bool(setting("proactive", cfg().get("proactive", False)))


def forget_everything():
    """The dashboard's nuclear option: drop every learned row (weights untouched, memory untouched)."""
    for t in ("interactions", "tasks", "feedback", "mistakes", "lessons", "lesson_events",
              "candidates", "workflows", "entities", "entity_mentions", "dataset"):
        write(f"DELETE FROM {t}")
    return True


if __name__ == "__main__":
    import tempfile
    set_db_path(os.path.join(tempfile.mkdtemp(), "l.db"))
    assert add("lessons", lesson_id="x", created=now(), statement="s") == 1  # lastrowid 1
    assert os.path.isfile(_path())
    assert row("SELECT statement, kind FROM lessons WHERE lesson_id=?", ("x",))["statement"] == "s"
    assert rows("SELECT * FROM interactions") == []
    assert set_setting("proactive", True) is True and proactive() is True
    assert cfg()["thresholds"]["strong"] == 0.95
    assert js(dump({"a": 1}))["a"] == 1 and js("not json", "d") == "d"
    write("INSERT INTO lessons(lesson_id,created) VALUES(?,?)", ("y", now()))
    assert forget_everything() and rows("SELECT * FROM lessons") == []
    print("[self-check] learning.store OK - schema, fail-soft helpers, runtime settings, forget-everything")
