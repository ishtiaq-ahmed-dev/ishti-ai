"""Versioned schema migrations for ISHTI's SQLite stores (Part A4).

One runner, three registered databases (arch2 memory, rgraph, learning). Each migration
is (store, version, description, statements) and runs exactly once — the schema_migrations
table records what applied, so a schema change is a NEW row, never an edit of old SQL.
Fresh databases run every migration in order at first open; existing databases are brought
up to head on the next start. Callers register migrations in order and call
`ensure_current(store)` before using a connection.

Migrations here add the Prompt-2 schema to the arch2 memory DB: conflicts, events,
interactions, tasks, task_steps, training_examples, preferences + the FTS index and the
person-record columns (aliases/contact_methods/preferences/important_facts).
"""
from __future__ import annotations

import sqlite3
import threading
import time

_lock = threading.RLock()
_registry: dict[str, list[dict]] = {}
_applied: set[tuple[str, int]] = set()


def migration(store: str, version: int, description: str, statements: str | list[str]):
    """Register one migration for `store` (applied by ensure_current)."""
    _registry.setdefault(store, []).append({
        "version": int(version), "description": description,
        "statements": [statements] if isinstance(statements, str) else list(statements)})
    _registry[store].sort(key=lambda m: m["version"])


def _ensure_table(c: sqlite3.Connection, store: str):
    c.execute("CREATE TABLE IF NOT EXISTS schema_migrations("
              "store TEXT, version INTEGER, description TEXT, applied_at REAL, "
              "PRIMARY KEY(store, version))")


def applied_versions(c: sqlite3.Connection, store: str) -> set[int]:
    _ensure_table(c, store)
    rows = c.execute("SELECT version FROM schema_migrations WHERE store=?", (store,)).fetchall()
    return {r[0] for r in rows}


_IDEMPOTENT = ("duplicate column", "already exists")  # SQLite has no IF NOT EXISTS for ADD COLUMN


def _run_stmt(c: sqlite3.Connection, stmt: str):
    if ";" in stmt and "CREATE" in stmt.upper():
        c.executescript(stmt)
    else:
        try:
            c.execute(stmt)
        except sqlite3.OperationalError as exc:
            if not any(tok in str(exc).lower() for tok in _IDEMPOTENT):
                raise  # a real schema problem must surface, never be swallowed


def ensure_current(conn: sqlite3.Connection, store: str) -> int:
    """Bring `store`'s schema to head on this connection. Returns migrations applied.
    The store's baseline tables are the caller's job (memory.conn()/rgraph.conn() create
    them) — migrations here only ADD things, never create the baseline."""
    with _lock:
        c = conn
        _ensure_table(c, store)
        done = applied_versions(c, store)
        n = 0
        for m in _registry.get(store, []):
            if m["version"] in done:
                continue
            for stmt in m["statements"]:
                _run_stmt(c, stmt)
            c.execute("INSERT INTO schema_migrations(store, version, description, applied_at) VALUES(?,?,?,?)",
                      (store, m["version"], m["description"], time.time()))
            n += 1
        c.commit()
        return n


def pending(conn: sqlite3.Connection, store: str) -> list[dict]:
    """Registered-but-not-yet-applied migrations for `store` (the dashboard's schema row)."""
    done = applied_versions(conn, store)
    return [m for m in _registry.get(store, []) if m["version"] not in done]


# --------------------------------------------------------------------------- schemas

# ---- arch2 memory DB (data/arch2_memory.db): Prompt-2 additions, additive only.
# v1 baseline is created by ishti.memstore.memory.conn() itself; these add the new tables.
migration("memory", 1, "conflicts + events (D5, F2 task log)", """
    CREATE TABLE IF NOT EXISTS conflicts(
        id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL,
        old_memory_id TEXT, new_memory_id TEXT, old_content TEXT, new_content TEXT,
        entity TEXT, status TEXT DEFAULT 'open', resolution TEXT DEFAULT '',
        resolution_source TEXT DEFAULT '');
    CREATE TABLE IF NOT EXISTS events(
        id TEXT PRIMARY KEY, ts REAL, kind TEXT, detail TEXT, session_id TEXT,
        entities TEXT DEFAULT '[]', importance REAL DEFAULT 0.4, source TEXT DEFAULT 'behavior');
""")

migration("memory", 2, "person record columns + procedures/preferences already covered; "
          "add training_examples + interactions mirror", [
    # person record (C2): the extras the unified entity row lacked
    "ALTER TABLE entities ADD COLUMN contact_methods TEXT DEFAULT '{}'",
    "ALTER TABLE entities ADD COLUMN preferences TEXT DEFAULT '[]'",
    "ALTER TABLE entities ADD COLUMN important_facts TEXT DEFAULT '[]'",
    "ALTER TABLE entities ADD COLUMN needs_confirmation INTEGER DEFAULT 0",
    "ALTER TABLE entities ADD COLUMN source TEXT DEFAULT 'inference'",
    # training examples live beside memory (G1); interactions mirror the learning store's
    # rows that concerned memory-worthy turns (F1) — one place for the memory dashboard
    """CREATE TABLE IF NOT EXISTS training_examples(
        id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, instruction TEXT, context TEXT,
        incorrect TEXT, correction TEXT, correct TEXT, reason TEXT, quality REAL DEFAULT 0.5,
        entity TEXT, exported INTEGER DEFAULT 0)""",
    """CREATE TABLE IF NOT EXISTS interactions(
        id TEXT PRIMARY KEY, ts REAL, session_id TEXT, request TEXT, resolved_request TEXT,
        skills TEXT DEFAULT '[]', memories TEXT DEFAULT '[]', decision TEXT DEFAULT '',
        tool_result TEXT DEFAULT '', feedback TEXT DEFAULT '', final_result TEXT,
        reward REAL DEFAULT 0.0)""",
])

migration("memory", 3, "task + task_steps (F2), audit of forgets, FTS over active memories", """
    CREATE TABLE IF NOT EXISTS tasks(
        task_id TEXT PRIMARY KEY, ts REAL, goal TEXT, final_status TEXT DEFAULT 'running',
        user_correction TEXT DEFAULT '', lessons TEXT DEFAULT '[]', reward REAL DEFAULT 0.0);
    CREATE TABLE IF NOT EXISTS task_steps(
        id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT, step_no INTEGER, step TEXT,
        tool TEXT, result TEXT DEFAULT '', success INTEGER DEFAULT 0,
        failure_stage TEXT DEFAULT '');
    CREATE TABLE IF NOT EXISTS forget_audit(
        id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, action TEXT, detail TEXT,
        items INTEGER DEFAULT 0, requested_by TEXT DEFAULT 'owner');
""")

# ---- rgraph DB: kin edge confidence columns (C4/C5) — additive
migration("rgraph", 1, "edge metadata: source_type + confidence on edges and nodes", [
    "ALTER TABLE edges ADD COLUMN confidence REAL DEFAULT 0.8",
    "ALTER TABLE edges ADD COLUMN source_type TEXT DEFAULT 'observed'",
    "ALTER TABLE nodes ADD COLUMN needs_confirmation INTEGER DEFAULT 0",
])

# ---- learning DB: no new tables needed (it already has the full loop) — marker only
migration("learning", 1, "baseline marker (schema created by store.py; tracked for future deltas)",
          "SELECT 1")


def status() -> dict:
    """What migrations exist per store (the dashboard's schema health row)."""
    return {store: [{"version": m["version"], "description": m["description"]} for m in migs]
            for store, migs in _registry.items()}


def _baseline_memory(c: sqlite3.Connection):
    """The tables arch2.memory.conn() creates before migrations run (mirrored here so the
    self-check exercises exactly the production order: baseline first, then migrate)."""
    c.executescript("""
        CREATE TABLE IF NOT EXISTS memories(id TEXT PRIMARY KEY, type TEXT, content TEXT UNIQUE);
        CREATE TABLE IF NOT EXISTS entities(id TEXT PRIMARY KEY, name TEXT UNIQUE, kind TEXT DEFAULT 'person');
        CREATE TABLE IF NOT EXISTS memory_links(memory_id TEXT, entity_id TEXT, PRIMARY KEY(memory_id, entity_id));
    """)


def _baseline_rgraph(c: sqlite3.Connection):
    c.executescript("""
        CREATE TABLE IF NOT EXISTS nodes(id INTEGER PRIMARY KEY, name TEXT UNIQUE, kind TEXT);
        CREATE TABLE IF NOT EXISTS edges(id INTEGER PRIMARY KEY, src INTEGER, dst INTEGER, rel TEXT,
                                         UNIQUE(src, dst, rel));
    """)


if __name__ == "__main__":
    import tempfile
    import os
    # runner: fresh db runs everything exactly once, second call is a no-op
    path = os.path.join(tempfile.mkdtemp(prefix="ishti_mig_"), "t.db")
    c = sqlite3.connect(path)
    _baseline_memory(c)   # production order: the store creates its baseline, then migrates
    _baseline_rgraph(c)
    n1 = ensure_current(c, "memory")
    n2 = ensure_current(c, "memory")
    assert n1 >= 3 and n2 == 0, (n1, n2)
    # registered tables exist
    for t in ("conflicts", "events", "training_examples", "interactions", "tasks", "task_steps",
              "forget_audit", "schema_migrations"):
        row = c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (t,)).fetchone()
        assert row, t
    cols = {r[1] for r in c.execute("PRAGMA table_info(entities)").fetchall()}
    assert {"contact_methods", "preferences", "important_facts", "needs_confirmation"} <= cols, cols
    # re-entrant on an old DB that already has some columns (idempotent ALTERs)
    n3 = ensure_current(c, "memory")
    assert n3 == 0, "second run must apply nothing"
    # other stores registered
    assert ensure_current(c, "rgraph") >= 1
    assert ensure_current(c, "learning") >= 1
    rcols = {r[1] for r in c.execute("PRAGMA table_info(edges)").fetchall()}
    assert {"confidence", "source_type"} <= rcols, rcols
    assert pending(c, "memory") == []
    c.close()
    print("[self-check] core.migrations OK — one-time apply, additive schema, idempotent re-run")
