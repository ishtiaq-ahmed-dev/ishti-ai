"""Typed, owner-visible behavior facts with provenance and client isolation."""

from __future__ import annotations

import dataclasses
import os
import sqlite3
import threading
import time
import uuid
from enum import Enum
from typing import Any, Iterable


class FactType(str, Enum):
    LANGUAGE = "language"
    TONE = "tone"
    TIME_HABIT = "time_habit"
    FREQUENT_TASK = "frequent_task"


class ProvenanceType(str, Enum):
    EXPLICIT_USER = "explicit_user"
    USER_CORRECTION = "user_correction"
    OBSERVED = "observed"
    INFERRED = "inferred"
    IMPORTED = "imported"


@dataclasses.dataclass(frozen=True)
class Provenance:
    source: ProvenanceType
    reference: str = ""
    confidence: float = 1.0

    def __post_init__(self) -> None:
        if not isinstance(self.source, ProvenanceType):
            raise TypeError("provenance source must be a ProvenanceType")
        if not isinstance(self.reference, str):
            raise TypeError("provenance reference must be text")
        if not 0.0 <= float(self.confidence) <= 1.0:
            raise ValueError("provenance confidence must be between 0 and 1")
        if len(self.reference) > 500:
            raise ValueError("provenance reference is too long")


@dataclasses.dataclass(frozen=True)
class BehaviorFact:
    id: str
    fact_type: FactType
    value: str
    provenance: Provenance
    scope: str
    client_id: str | None
    created_at: float
    updated_at: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "fact_type": self.fact_type.value,
            "value": self.value,
            "provenance": {
                "source": self.provenance.source.value,
                "reference": self.provenance.reference,
                "confidence": self.provenance.confidence,
            },
            "scope": self.scope,
            "client_id": self.client_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


def default_db_path() -> str:
    configured = os.environ.get("ISHTI_LEARNING_DB")
    if configured:
        path = os.path.abspath(configured)
        from ishti.test_mode import scratch_path
        return scratch_path(path)
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(root, "data", "learning.db")
    from ishti.test_mode import scratch_path
    return scratch_path(path)


class BehaviorProfileStore:
    """SQLite repository whose inference reads are always scope-filtered."""

    def __init__(self, path: str | None = None):
        self.path = os.path.abspath(path or default_db_path())
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        with self._db:
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute("PRAGMA busy_timeout=5000")
            self._db.execute("""CREATE TABLE IF NOT EXISTS owner_behavior_facts(
                id TEXT PRIMARY KEY,
                fact_type TEXT NOT NULL CHECK(fact_type IN ('language','tone','time_habit','frequent_task')),
                value TEXT NOT NULL,
                provenance_source TEXT NOT NULL,
                provenance_reference TEXT NOT NULL DEFAULT '',
                provenance_confidence REAL NOT NULL CHECK(provenance_confidence >= 0 AND provenance_confidence <= 1),
                scope TEXT NOT NULL CHECK(scope IN ('owner','client')),
                client_id TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                CHECK((scope='owner' AND client_id IS NULL) OR
                      (scope='client' AND client_id IS NOT NULL AND length(client_id) > 0))
            )""")
            self._db.execute("""CREATE INDEX IF NOT EXISTS idx_owner_behavior_scope
                              ON owner_behavior_facts(scope, client_id, fact_type)""")

    def close(self) -> None:
        with self._lock:
            self._db.close()

    @staticmethod
    def _fact_type(value: FactType | str) -> FactType:
        try:
            return value if isinstance(value, FactType) else FactType(str(value))
        except ValueError as exc:
            raise ValueError("unsupported behavior fact type") from exc

    @staticmethod
    def _scope(scope: str, client_id: str | None) -> tuple[str, str | None]:
        normalized = str(scope or "owner").lower()
        exact_client = str(client_id).strip() if client_id is not None else None
        if normalized not in {"owner", "client"}:
            raise ValueError("scope must be owner or client")
        if normalized == "owner" and exact_client:
            raise ValueError("owner-scoped facts cannot carry a client id")
        if normalized == "client" and not exact_client:
            raise ValueError("client-scoped facts require an exact client id")
        if exact_client and (len(exact_client) > 160 or any(ord(char) < 32 for char in exact_client)):
            raise ValueError("invalid client id")
        return normalized, exact_client

    def add(
        self,
        fact_type: FactType | str,
        value: str,
        provenance: Provenance,
        *,
        scope: str = "owner",
        client_id: str | None = None,
    ) -> BehaviorFact:
        typed = self._fact_type(fact_type)
        if not isinstance(provenance, Provenance):
            raise TypeError("provenance must be a Provenance record")
        clean_value = str(value).strip()
        if not clean_value or len(clean_value) > 1000:
            raise ValueError("behavior fact value must contain 1-1000 characters")
        normalized_scope, exact_client = self._scope(scope, client_id)
        now = time.time()
        fact_id = "behavior_" + uuid.uuid4().hex
        with self._lock, self._db:
            self._db.execute(
                """INSERT INTO owner_behavior_facts
                   (id,fact_type,value,provenance_source,provenance_reference,provenance_confidence,
                    scope,client_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (fact_id, typed.value, clean_value, provenance.source.value, provenance.reference,
                 provenance.confidence, normalized_scope, exact_client, now, now),
            )
        return BehaviorFact(fact_id, typed, clean_value, provenance, normalized_scope, exact_client, now, now)

    @staticmethod
    def _from_row(row: sqlite3.Row) -> BehaviorFact:
        return BehaviorFact(
            id=row["id"], fact_type=FactType(row["fact_type"]), value=row["value"],
            provenance=Provenance(ProvenanceType(row["provenance_source"]),
                                  row["provenance_reference"], row["provenance_confidence"]),
            scope=row["scope"], client_id=row["client_id"],
            created_at=row["created_at"], updated_at=row["updated_at"],
        )

    def list_owner_visible(self) -> list[BehaviorFact]:
        """Return every record for the local owner's profile-management view."""
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM owner_behavior_facts ORDER BY created_at,id"
            ).fetchall()
        return [self._from_row(row) for row in rows]

    def for_inference(self, *, mode: str = "PERSONAL", client_id: str | None = None) -> list[BehaviorFact]:
        """Return owner facts and, in BUSINESS mode, only the exact active client's facts."""
        normalized_mode = str(mode or "PERSONAL").upper()
        if normalized_mode not in {"PERSONAL", "BUSINESS"}:
            raise ValueError("mode must be PERSONAL or BUSINESS")
        if normalized_mode == "PERSONAL":
            sql = "SELECT * FROM owner_behavior_facts WHERE scope='owner' ORDER BY created_at,id"
            params: tuple[str, ...] = ()
        else:
            exact_client = str(client_id or "").strip()
            if exact_client:
                sql = ("SELECT * FROM owner_behavior_facts WHERE "
                       "(scope='owner' AND fact_type IN ('language','tone')) OR "
                       "(scope='client' AND client_id=?) ORDER BY created_at,id")
                params = (exact_client,)
            else:
                sql = ("SELECT * FROM owner_behavior_facts WHERE scope='owner' "
                       "AND fact_type IN ('language','tone') ORDER BY created_at,id")
                params = ()
        with self._lock:
            rows = self._db.execute(sql, params).fetchall()
        return [self._from_row(row) for row in rows]

    def prompt_records(self, *, mode: str = "PERSONAL", client_id: str | None = None) -> list[dict[str, Any]]:
        return [fact.to_dict() for fact in self.for_inference(mode=mode, client_id=client_id)]

    def delete(self, fact_id: str) -> bool:
        """Hard-delete one exact fact after the caller has enforced the T2 owner gate."""
        exact_id = str(fact_id or "").strip()
        if not exact_id.startswith("behavior_"):
            return False
        with self._lock, self._db:
            count = self._db.execute(
                "DELETE FROM owner_behavior_facts WHERE id=?", (exact_id,)
            ).rowcount
        return count == 1

    def delete_many(self, fact_ids: Iterable[str]) -> int:
        return sum(1 for fact_id in dict.fromkeys(fact_ids) if self.delete(fact_id))


_default_store: BehaviorProfileStore | None = None
_default_store_lock = threading.Lock()


def get_store() -> BehaviorProfileStore:
    """Return the process-wide store backed by ``data/learning.db``."""
    global _default_store
    with _default_store_lock:
        if _default_store is None:
            _default_store = BehaviorProfileStore()
        return _default_store


def record_fact(
    fact_type: FactType | str,
    value: str,
    provenance: Provenance,
    *,
    scope: str = "owner",
    client_id: str | None = None,
) -> dict[str, Any]:
    """Persist one typed behavior fact and return its owner-visible representation."""
    return get_store().add(
        fact_type, value, provenance, scope=scope, client_id=client_id
    ).to_dict()


def owner_visible_facts() -> list[dict[str, Any]]:
    return [fact.to_dict() for fact in get_store().list_owner_visible()]


def inference_facts(*, mode: str = "PERSONAL", client_id: str | None = None) -> list[dict[str, Any]]:
    return get_store().prompt_records(mode=mode, client_id=client_id)


def delete_fact(fact_id: str) -> bool:
    """Delete an exact fact ID; the action caller must first enforce the T2 owner gate."""
    return get_store().delete(fact_id)
