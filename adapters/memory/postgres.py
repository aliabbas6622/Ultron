"""PostgreSQL-backed MemoryProvider (04_TECH_STACK default persistence target).

SQLite (core/memory_sqlite.py) is the dev backend; PostgreSQL is the default.
This adapter carries the SAME contract semantics and the SAME migration
version numbers (version 1 = memory_records): the list is append-only and
never re-numbered, so a deployment can move between backends without schema
drift. Retrieval semantics are identical to the SQLite backend: deterministic
ordering (importance DESC, observed_at DESC, id ASC), records superseded by
ANY stored record excluded, temporal validity filtered against query.now
(falling back to the store's clock), and unknown supersedes/contradiction
refs rejected as ContractViolationError.

Dependency direction: adapters MAY import vendor SDKs (psycopg) and
contracts/, and MUST NOT import core/. That is why the content-hash function
below is duplicated rather than imported — it must stay byte-identical to
core.memory_sqlite.record_content_hash (tests/test_memory_postgres.py pins
it to the canonical sha256-of-canonical-JSON definition).

psycopg is imported at CONSTRUCTION time, not import time: environments
without the postgres dependency group can still import this module (and the
rest of the app) unharmed. Construction itself is health-driven like every
other brick: an unreachable server does NOT raise from __init__ — the failure
is stored lazily, health() reports unhealthy, and write/retrieve raise
BlockUnavailableError. The DSN carries credentials and is never stored on a
loggable attribute, never logged, and never surfaced in health details (host
name and port only).
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

from contracts.call_context import CallContext
from contracts.errors import BlockUnavailableError, ContractViolationError
from contracts.memory import HealthStatus, MemoryQuery, MemoryRecord, new_memory_id

if TYPE_CHECKING:  # typing only; never executed at runtime
    import psycopg

# --- migrations: append-only, version numbers MATCH core/memory_sqlite.py ----

MIGRATIONS: list[tuple[int, str]] = [
    (
        1,
        """
        CREATE TABLE memory_records (
            id             TEXT PRIMARY KEY,
            kind           TEXT NOT NULL,
            subject        TEXT NOT NULL,
            predicate      TEXT NOT NULL,
            value          TEXT NOT NULL,
            source         TEXT NOT NULL DEFAULT '',
            observed_at    DOUBLE PRECISION NOT NULL DEFAULT 0,
            valid_from     DOUBLE PRECISION NOT NULL DEFAULT 0,
            valid_until    DOUBLE PRECISION,
            confidence     DOUBLE PRECISION NOT NULL DEFAULT 1.0,
            importance     DOUBLE PRECISION NOT NULL DEFAULT 0.5,
            sensitivity    TEXT NOT NULL DEFAULT 'normal',
            scope          TEXT NOT NULL DEFAULT 'global',
            supersedes     TEXT,
            contradictions TEXT NOT NULL DEFAULT '[]',  -- JSON array of ids
            content_hash   TEXT NOT NULL DEFAULT '',
            created_at     DOUBLE PRECISION NOT NULL
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

_SCHEMA_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def record_content_hash(record: MemoryRecord) -> str:
    # 16-char sha256 over the canonical payload. MUST stay byte-identical to
    # core.memory_sqlite.record_content_hash (adapters may not import core, so
    # the helper is duplicated here); tests/test_memory_postgres.py asserts
    # the parity against a locally computed sha256 of the same canonical JSON.
    payload = json.dumps(
        [record.kind, record.subject, record.predicate, record.value, record.source],
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


class PostgresMemoryStore:
    """Default MemoryProvider (04_TECH_STACK) over a single PostgreSQL
    connection. The V0.1 runtime is single-node, single-writer, so one
    connection with explicit commit/rollback transaction blocks mirrors the
    SQLite backend (`with conn:` in psycopg3 CLOSES the connection at block
    exit, so the sqlite pattern is emulated by _tx() below). Duplicate ids
    fail with the driver's integrity error, exactly as they do on SQLite."""

    block_id = "memory.postgres"

    def __init__(self, dsn: str, *, search_path: str | None = None) -> None:
        # Vendor import happens here (not at module import time) so the rest
        # of the app never breaks when the postgres group is not installed.
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:
            raise ImportError(
                "PostgresMemoryStore requires psycopg; "
                "install the postgres group: uv sync --group postgres"
            ) from exc

        self._conn: psycopg.Connection[Any] | None = None
        self._connect_error: str | None = None
        self._host = "postgres"
        self._port = ""
        try:
            self._conn = psycopg.connect(dsn, autocommit=False, row_factory=dict_row)
            try:
                self._host = self._conn.info.host or "postgres"
                self._port = str(self._conn.info.port or "")
            except Exception:
                pass  # health detail falls back to the generic label
            if search_path is not None:
                self._set_search_path(search_path)  # test isolation: throwaway schema
            self._migrate()
        except psycopg.Error as exc:
            # Health-driven like other bricks: construction must not raise on
            # an unreachable server. Store the failure lazily (exception class
            # only — libpq messages vary and the DSN is off-limits), report it
            # via health(), and let write/retrieve raise BlockUnavailableError.
            self._connect_error = f"postgres unreachable ({type(exc).__name__})"
            conn, self._conn = self._conn, None
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass

    # --- connection guard ----------------------------------------------------

    def _dead_detail(self) -> str:
        return self._connect_error or f"{self.block_id}: connection is closed"

    def _require_conn(self) -> Any:
        conn = self._conn
        if conn is None or conn.closed or getattr(conn, "broken", False):
            raise BlockUnavailableError(self._dead_detail())
        return conn

    # --- transactions ----------------------------------------------------------

    @contextmanager
    def _tx(self) -> Any:
        """Commit on success, roll back on error: the psycopg3 equivalent of
        the sqlite backend's `with self._conn:` blocks (psycopg3's own
        `with conn:` would close the connection at block exit)."""
        try:
            yield self._conn
        except BaseException:
            self._conn.rollback()
            raise
        else:
            self._conn.commit()

    # --- migrations ----------------------------------------------------------

    def _set_search_path(self, search_path: str) -> None:
        if not _SCHEMA_NAME.fullmatch(search_path):
            raise ValueError(f"invalid schema name: {search_path!r}")
        with self._tx():
            # parameterized set_config: no identifier interpolation at all
            self._conn.execute("SELECT set_config('search_path', %s, false)", (search_path,))

    def _migrate(self) -> None:
        with self._tx():
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations "
                "(version INTEGER PRIMARY KEY, applied_at DOUBLE PRECISION NOT NULL)"
            )
        with self._tx():
            applied = {
                row["version"]
                for row in self._conn.execute("SELECT version FROM schema_migrations").fetchall()
            }
        for version, sql in MIGRATIONS:
            if version in applied:
                continue
            with self._tx():
                # psycopg executes one statement per call; the sqlite backend
                # runs the same script via executescript
                for stmt in (part.strip() for part in sql.split(";")):
                    if stmt:
                        self._conn.execute(stmt)
                self._conn.execute(
                    "INSERT INTO schema_migrations (version, applied_at) VALUES (%s, %s)",
                    (version, time.time()),
                )

    def applied_versions(self) -> list[int]:
        conn = self._require_conn()
        with self._tx():  # never leave the connection idle-in-transaction
            rows = conn.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            ).fetchall()
        return [row["version"] for row in rows]

    # --- MemoryProvider ------------------------------------------------------

    def write(self, run: CallContext, record: MemoryRecord) -> MemoryRecord:
        run.check_alive()
        conn = self._require_conn()
        import psycopg  # already imported by __init__; cheap sys.modules hit

        try:
            with self._tx():
                known = {row["id"] for row in conn.execute("SELECT id FROM memory_records").fetchall()}
            for ref in filter(None, (record.supersedes, *record.contradictions)):
                if ref not in known:
                    raise ContractViolationError(
                        f"{self.block_id}: supersedes/contradiction ref unknown id {ref!r}"
                    )

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
            # contradictions rides as JSON text (mirroring the sqlite column);
            # on read it is parsed back into a tuple
            columns = [c.strip() for c in f"{_COLUMNS}, created_at".split(",")]
            placeholders = ", ".join("%s" for _ in columns)
            values = (
                stored.id, stored.kind, stored.subject, stored.predicate, stored.value,
                stored.source, stored.observed_at, stored.valid_from, stored.valid_until,
                stored.confidence, stored.importance, stored.sensitivity, stored.scope,
                stored.supersedes, json.dumps(list(stored.contradictions)), stored.content_hash,
                time.time(),  # created_at, store bookkeeping (not part of the record)
            )
            sql = f"INSERT INTO memory_records ({', '.join(columns)}) VALUES ({placeholders})"
            with self._tx():
                conn.execute(sql, values)
        except (psycopg.OperationalError, psycopg.InterfaceError) as exc:
            # connection died mid-operation: normalized block-unavailable failure
            raise BlockUnavailableError(
                f"{self.block_id}: connection lost ({type(exc).__name__})"
            ) from exc
        return stored

    def retrieve(self, run: CallContext, query: MemoryQuery) -> list[MemoryRecord]:
        run.check_alive()
        conn = self._require_conn()
        import psycopg  # already imported by __init__; cheap sys.modules hit

        try:
            now = query.now if query.now is not None else time.time()

            where, params = [], []
            if query.kind is not None:
                where.append("kind = %s")
                params.append(query.kind)
            if query.subject is not None:
                where.append("subject = %s")
                params.append(query.subject)
            if query.predicate is not None:
                where.append("predicate = %s")
                params.append(query.predicate)
            if query.scope is not None:
                where.append("scope = %s")
                params.append(query.scope)

            sql = f"SELECT {_COLUMNS} FROM memory_records"
            if where:
                sql += " WHERE " + " AND ".join(where)
            sql += " ORDER BY importance DESC, observed_at DESC, id ASC"

            with self._tx():
                rows = conn.execute(sql, params or None).fetchall()
                superseded_ids = {
                    row["supersedes"]
                    for row in conn.execute(
                        "SELECT DISTINCT supersedes FROM memory_records WHERE supersedes IS NOT NULL"
                    ).fetchall()
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
        except (psycopg.OperationalError, psycopg.InterfaceError) as exc:
            # connection died mid-operation: normalized block-unavailable failure
            raise BlockUnavailableError(
                f"{self.block_id}: connection lost ({type(exc).__name__})"
            ) from exc

    def health(self) -> HealthStatus:
        conn = self._conn
        if conn is None or conn.closed or getattr(conn, "broken", False):
            return HealthStatus(healthy=False, detail=self._dead_detail())
        try:
            with self._tx():  # never leave the connection idle-in-transaction
                conn.execute("SELECT 1").fetchone()
            versions = self.applied_versions()
            at = f"{self._host}:{self._port}" if self._port else self._host
            return HealthStatus(
                healthy=True,
                detail=f"postgres @ {at}, schema v{versions[-1] if versions else 0}",
            )
        except Exception as exc:  # health() must never raise
            # exception class only: libpq messages vary and the DSN is off-limits
            return HealthStatus(healthy=False, detail=f"postgres unreachable ({type(exc).__name__})")

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()

    @staticmethod
    def _row_to_record(row: dict[str, Any]) -> MemoryRecord:
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
            contradictions=tuple(json.loads(row["contradictions"])),  # JSON text -> tuple
            content_hash=row["content_hash"],
        )
