"""Encrypted business state and cross-process serialized, crash-safe transactions."""
from contextlib import closing, contextmanager
import json
from pathlib import Path
import sqlite3
import time

from ishti import ROOT, secure_store
from ishti.test_mode import scratch_path


def default_state():
    return {"schema": 1, "revision": 0, "profile": {"name": "", "currency": "USD",
            "timezone": "UTC", "goal": "", "enabled": False, "interval_minutes": 60,
            "lead_days": 3, "stale_days": 7, "revenue_drop_pct": 20}, "sources": [],
            "decisions": [], "outcomes": [], "audit": [], "last_cycle": None,
            "last_error": None, "next_run": 0, "heartbeat": 0, "runner": None}


class Store:
    def __init__(self, path=None):
        self.path = Path(path or scratch_path(str(Path(ROOT) / "data" / "business.db")))

    def connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=10)
        db.execute("PRAGMA busy_timeout=10000")
        db.execute("CREATE TABLE IF NOT EXISTS state(id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL)")
        db.commit()
        return db

    @contextmanager
    def transaction(self):
        # IMMEDIATE is the cycle lease: API and standalone worker cannot commit the same cycle twice.
        with closing(self.connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                row = db.execute("SELECT payload FROM state WHERE id=1").fetchone()
                state = json.loads(secure_store.open_text(row[0])) if row else default_state()
                yield state
                state["revision"] += 1
                raw = json.dumps(state, ensure_ascii=False, separators=(",", ":"))
                if len(raw.encode("utf-8")) > 16 * 1024 * 1024:
                    raise ValueError("Business workspace is full; export and reduce source data before continuing.")
                db.execute("INSERT INTO state VALUES(1,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload",
                           (secure_store.seal_text(raw),))
                db.commit()
            except BaseException:
                db.rollback()
                raise

    def read(self):
        with closing(self.connect()) as db:
            row = db.execute("SELECT payload FROM state WHERE id=1").fetchone()
        return json.loads(secure_store.open_text(row[0])) if row else default_state()


def record(state, action, detail, now=None):
    state["audit"].append({"at": now or time.time(), "action": action, "detail": detail})
    state["audit"] = state["audit"][-500:]
