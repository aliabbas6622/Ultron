"""SQLite-backed MemoryProvider (V0.2 dev backend).

04_TECH_STACK: PostgreSQL+pgvector is the default persistence target; SQLite is
the simple local-testing backend. Both speak the same MemoryProvider v1
contract, and both MUST go through schema migrations (06: "migrations are
required from the first schema version"). This module owns its migration list;
a PostgreSQL backend would carry the same version numbers upward, never
re-number them.

Deterministic conflict behavior (03 conformance): retrieval excludes records
superseded by ANY stored record, filters by temporal validity, and orders by
importance DESC, observed_at DESC, id ASC — same state in, same order out.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time

from contracts.memory import HealthStatus, MemoryProvider, MemoryQuery, MemoryRecord
from core.errors import ContractViolationError
from core.ids import new_memory_id
from core.run_context import RunContext

# --- migrations: append-only, never edit an applied version -----------------

MIGRATIONS: list[tuple[int, str]] = [
    (
        1,
        """
        CREATE TABLE memory_records (
            id            TEXT PRIMARY KEY,
            kind          TEXT NOT NULL,
            subject       TEXT NOT NULL,
            predicate     TEXT NOT NULL,
            value         TEXT NOT NULL,
            source        TEXT NOT NULL DEFAULT '',
            observed_at   REAL NOT NULL DEFAULT 0,
            valid_from    REAL NOT NULL DEFAULT 0,
            valid_until   REAL,
            confidence    REAL NOT NULL DEFAULT 1.0,
            importance    REAL NOT NULL DEFAULT 0.5,
            sensitivity   TEXT NOT NULL DEFAULT 'normal',
            scope         TEXT NOT NULL DEFAULT 'global',
            supersedes    TEXT,
            contradictions TEXT NOT NULL DEFAULT '[]',  -- JSON array of ids
            content_hash  TEXT NOT NULL DEFAULT '',
            created_at    REAL NOT NULL
        );
        CREATE INDEX idx_memory_subject ON memory_records (subject, predicate);
        CREATE INDEX idx_memory_kind_scope ON memory_records (kind, scope);
        """,
    ),
]

_COLUMNS = (
    "id, kind, subject, predicate, value, source, observed_at, valid_from, valid_until, "
    "confidence, importance, sensitivity, scope, supersedes, contradictions, content_hash"
)


def record_content_hash(record: MemoryRecord) -> str:
    payload = json.dumps(
        [record.kind, record.subject, record.predicate, record.value, record.source],
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


class SqliteMemoryStore:
    """Dev MemoryProvider over a single SQLite file. One connection per store;
    the V0.1 runtime is single-node, single-writer."""

    block_id = "memory.sqlite"

    def __init__(self, path: str) -> None:
        self._path = path
        parent = os.path.dirname(os.path.abspath(path))
        if parent:
            os.makedirs(parent, exist_ok=True)
        self._conn = sqlite3.connect(path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._migrate()

    # --- migrations ----------------------------------------------------------

    def _migrate(self) -> None:
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at REAL NOT NULL)"
        )
        applied = {row["version"] for row in self._conn.execute("SELECT version FROM schema_migrations")}
        for version, sql in MIGRATIONS:
            if version in applied:
                continue
            with self._conn:
                self._conn.executescript(sql)
                self._conn.execute(
                    "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)", (version, time.time())
                )

    def applied_versions(self) -> list[int]:
        return [
            row["version"]
            for row in self._conn.execute("SELECT version FROM schema_migrations ORDER BY version")
        ]

    # --- MemoryProvider ------------------------------------------------------

    def write(self, run: RunContext, record: MemoryRecord) -> MemoryRecord:
        run.check_alive()
        known = {row["id"] for row in self._conn.execute("SELECT id FROM memory_records")}
        for ref in filter(None, (record.supersedes, *record.contradictions)):
            if ref not in known:
                raise ContractViolationError(f"{self.block_id}: supersedes/contradiction ref unknown id {ref!r}")

        stored = MemoryRecord(
            id=record.id or new_memory_id(),
            kind=record.kind,
            subject=record.subject,
            predicate=record.predicate,
            value=record.value,
            source=record.source,
            observed_at=record.observed_at,
            valid_from=record.valid_from,
            valid_until=record.valid_until,
            confidence=record.confidence,
            importance=record.importance,
            sensitivity=record.sensitivity,
            scope=record.scope,
            supersedes=record.supersedes,
            contradictions=tuple(record.contradictions),
            content_hash=record_content_hash(record),
        )
        values = (
            stored.id, stored.kind, stored.subject, stored.predicate, stored.value,
            stored.source, stored.observed_at, stored.valid_from, stored.valid_until,
            stored.confidence, stored.importance, stored.sensitivity, stored.scope,
            stored.supersedes, json.dumps(list(stored.contradictions)), stored.content_hash,
            time.time(),  # created_at, store bookkeeping (not part of the record)
        )
        insert_columns = f"{_COLUMNS}, created_at"
        with self._conn:
            self._conn.execute(
                f"INSERT INTO memory_records ({insert_columns}) VALUES ({','.join('?' * len(values))})",
                values,
            )
        return stored

    def retrieve(self, run: RunContext, query: MemoryQuery) -> list[MemoryRecord]:
        run.check_alive()
        now = query.now if query.now is not None else time.time()

        where, params = [], []
        if query.kind is not None:
            where.append("kind = ?")
            params.append(query.kind)
        if query.subject is not None:
            where.append("subject = ?")
            params.append(query.subject)
        if query.predicate is not None:
            where.append("predicate = ?")
            params.append(query.predicate)
        if query.scope is not None:
            where.append("scope = ?")
            params.append(query.scope)

        sql = f"SELECT {_COLUMNS} FROM memory_records"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY importance DESC, observed_at DESC, id ASC"
        rows = self._conn.execute(sql, params).fetchall()

        superseded_ids = {
            row["supersedes"]
            for row in self._conn.execute("SELECT DISTINCT supersedes FROM memory_records WHERE supersedes IS NOT NULL")
        }

        results = []
        for row in rows:
            record = self._row_to_record(row)
            if record.id in superseded_ids:
                continue  # superseded by a stored record
            if record.valid_until is not None and record.valid_until < now:
                continue  # temporal validity expired
            results.append(record)
            if len(results) >= query.limit:
                break
        return results

    def health(self) -> HealthStatus:
        try:
            self._conn.execute("SELECT 1 FROM memory_records LIMIT 1").fetchone()
            versions = self.applied_versions()
            return HealthStatus(healthy=True, detail=f"sqlite @ {self._path}, schema v{versions[-1] if versions else 0}")
        except sqlite3.Error as exc:
            return HealthStatus(healthy=False, detail=f"sqlite error: {exc}")

    def close(self) -> None:
        self._conn.close()

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> MemoryRecord:
        return MemoryRecord(
            id=row["id"],
            kind=row["kind"],
            subject=row["subject"],
            predicate=row["predicate"],
            value=row["value"],
            source=row["source"],
            observed_at=row["observed_at"],
            valid_from=row["valid_from"],
            valid_until=row["valid_until"],
            confidence=row["confidence"],
            importance=row["importance"],
            sensitivity=row["sensitivity"],
            scope=row["scope"],
            supersedes=row["supersedes"],
            contradictions=tuple(json.loads(row["contradictions"])),
            content_hash=row["content_hash"],
        )
