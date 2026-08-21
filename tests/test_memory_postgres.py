"""PostgreSQL MemoryProvider backend tests (04_TECH_STACK default persistence).

The server-backed tests re-run the core conformance scenarios from
tests/test_memory_conformance.py against adapters.memory.postgres
.PostgresMemoryStore, with identical assertions. They are gated on
ULTRON_PG_DSN (plus psycopg being importable); without a server they skip
cleanly so the default dev environment stays green. Every session runs inside
a throwaway schema created/dropped by the `schema` fixture, so the tests never
touch data in the caller's default schema (and each test uses its own subject
to stay independent within the shared session schema).

The always-run tests (no gate) cover what must hold everywhere: content-hash
parity with the contract semantics, the friendly ImportError when psycopg is
missing, and the health-driven construction rules (unreachable server never
raises from __init__, health() reports unhealthy without leaking DSN
credentials, write/retrieve raise BlockUnavailableError).

Note on gating: the spec asks for a module-wide `pytestmark = skipif(not
DSN, ...)`, but that would also gate the required ALWAYS-RUN tests. The gate
is therefore applied per-test via the `requires_pg` marker below — same skip
condition, same clean skip without a server, but the ungated tests always run.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import sys
import uuid

import pytest

from adapters.memory.postgres import PostgresMemoryStore, record_content_hash
from contracts.errors import BlockUnavailableError, ContractViolationError
from contracts.memory import MemoryQuery, MemoryRecord
from core.run_context import RunContext

DSN = os.environ.get("ULTRON_PG_DSN")
HAS_PSYCOPG = importlib.util.find_spec("psycopg") is not None

# The spec's module-wide pytestmark, scoped to the server-backed tests only
# (see the module docstring note) so the always-run tests below stay ungated.
requires_pg = pytest.mark.skipif(
    not DSN or not HAS_PSYCOPG,
    reason="set ULTRON_PG_DSN and install the postgres group "
    "(uv sync --group postgres) to run PostgreSQL backend tests",
)


@pytest.fixture(scope="session")
def schema():
    import psycopg  # gated by requires_pg on every test that uses this fixture

    name = f"ultron_test_{uuid.uuid4().hex[:12]}"  # injection-safe by construction
    try:
        admin = psycopg.connect(DSN, autocommit=True)
    except psycopg.Error as exc:
        pytest.skip(f"ULTRON_PG_DSN set but server unreachable: {type(exc).__name__}")
    try:
        admin.execute(f'CREATE SCHEMA "{name}"')
        yield name
    finally:
        admin.execute(f'DROP SCHEMA IF EXISTS "{name}" CASCADE')
        admin.close()


def make_store(schema: str) -> PostgresMemoryStore:
    return PostgresMemoryStore(DSN, search_path=schema)


def rec(subject: str, **kw) -> MemoryRecord:
    defaults = dict(
        kind="preferences",
        subject=subject,
        predicate="prefers",
        value="dark mode",
        source="owner_instruction",
        observed_at=1000.0,
        confidence=0.9,
        importance=0.8,
    )
    defaults.update(kw)
    return MemoryRecord(**defaults)


# --- conformance scenarios (mirror tests/test_memory_conformance.py) ---------


@requires_pg
def test_write_fills_id_and_content_hash(schema):
    store = make_store(schema)
    stored = store.write(RunContext(), rec("owner_hash"))
    assert stored.id.startswith("mem_")
    # parity: same canonical payload hashing as the contract semantics dictates
    payload = json.dumps(
        ["preferences", "owner_hash", "prefers", "dark mode", "owner_instruction"],
        ensure_ascii=False,
        sort_keys=True,
    )
    assert stored.content_hash == hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
    store.close()


@requires_pg
def test_preserves_provenance_and_confidence(schema):
    store = make_store(schema)
    written = store.write(
        RunContext(), rec("owner_prov", source="web:example.com", confidence=0.42)
    )
    got = store.retrieve(RunContext(), MemoryQuery(subject="owner_prov"))[0]
    assert got.source == "web:example.com"  # provenance intact
    assert got.confidence == pytest.approx(0.42)  # confidence intact
    assert got.observed_at == 1000.0
    assert got.id == written.id
    store.close()


@requires_pg
def test_retrieval_does_not_drop_required_fields(schema):
    store = make_store(schema)
    store.write(
        RunContext(),
        rec("owner_fields", valid_from=900.0, importance=0.7,
            sensitivity="sensitive", scope="device:workstation"),
    )
    got = store.retrieve(RunContext(), MemoryQuery(subject="owner_fields"))[0]
    # fields that are legitimately nullable when unset
    nullable = {"valid_until", "supersedes"}
    for field in ("id", "kind", "subject", "predicate", "value", "source", "observed_at",
                  "valid_from", "valid_until", "confidence", "importance", "sensitivity",
                  "scope", "supersedes", "contradictions", "content_hash"):
        if field not in nullable:
            assert getattr(got, field) is not None, f"missing {field}"
    assert isinstance(got.contradictions, tuple)
    store.close()


@requires_pg
def test_supersedes_hides_superseded_record(schema):
    store = make_store(schema)
    run = RunContext()
    old = store.write(run, rec("owner_sup", value="light mode", observed_at=1000.0))
    new = store.write(
        run, rec("owner_sup", value="dark mode", observed_at=2000.0, supersedes=old.id)
    )

    got = store.retrieve(run, MemoryQuery(subject="owner_sup"))
    assert [r.id for r in got] == [new.id]
    assert got[0].value == "dark mode"
    store.close()


@requires_pg
def test_contradictions_are_recorded_links_not_deletions(schema):
    store = make_store(schema)
    run = RunContext()
    a = store.write(run, rec("owner_contra", value="postgres", observed_at=1000.0))
    b = store.write(
        run, rec("owner_contra", value="sqlite", observed_at=2000.0, contradictions=(a.id,))
    )

    # both stay retrievable; the contradiction is carried on the record
    got = store.retrieve(run, MemoryQuery(subject="owner_contra"))
    assert {r.id for r in got} == {a.id, b.id}
    assert got[0].contradictions == (a.id,)
    store.close()


@requires_pg
def test_deterministic_conflict_ordering(schema):
    store = make_store(schema)
    run = RunContext()
    ids = [
        store.write(run, rec("owner_order", value=f"v{i}", observed_at=1000.0 + i,
                             importance=0.5)).id
        for i in range(5)
    ]
    first = store.retrieve(run, MemoryQuery(subject="owner_order"))
    second = store.retrieve(run, MemoryQuery(subject="owner_order"))
    assert [r.id for r in first] == [r.id for r in second]  # identical across calls
    # equal importance -> observed_at DESC -> written order reversed
    assert [r.id for r in first] == list(reversed(ids))
    assert [r.observed_at for r in first] == [1004.0, 1003.0, 1002.0, 1001.0, 1000.0]
    store.close()


@requires_pg
def test_temporal_validity_filters_expired_records(schema):
    store = make_store(schema)
    run = RunContext()
    store.write(run, rec("owner_time", value="temporary", observed_at=1000.0, valid_until=1500.0))
    store.write(run, rec("owner_time", value="permanent", observed_at=1000.0,
                         predicate="uses", valid_until=None))

    got = store.retrieve(run, MemoryQuery(subject="owner_time", now=2000.0))
    assert [r.value for r in got] == ["permanent"]
    # at a time before expiry the temporary record is still visible
    got_before = store.retrieve(run, MemoryQuery(subject="owner_time", now=1200.0))
    assert {r.value for r in got_before} == {"temporary", "permanent"}
    store.close()


@requires_pg
def test_unknown_supersedes_ref_is_a_contract_violation(schema):
    store = make_store(schema)
    with pytest.raises(ContractViolationError, match="unknown id"):
        store.write(RunContext(), rec("owner_badref", supersedes="mem_does_not_exist"))
    store.close()


@requires_pg
def test_unknown_kind_is_rejected(schema):
    store = make_store(schema)
    with pytest.raises(ValueError, match="unknown memory kind"):
        store.write(RunContext(), rec("owner_badkind", kind="vibes"))
    store.close()


@requires_pg
def test_persistence_across_reopen_and_migrations_from_day_one(schema):
    store = make_store(schema)
    run = RunContext()
    store.write(run, rec("owner_reopen"))
    versions = store.applied_versions()
    store.close()

    reopened = make_store(schema)  # reopening applies no new migrations, keeps data
    assert reopened.applied_versions() == versions == [1]
    assert reopened.retrieve(run, MemoryQuery(subject="owner_reopen"))[0].value == "dark mode"
    assert reopened.health().healthy
    reopened.close()


@requires_pg
def test_health_reports_schema_version_and_never_leaks_dsn(schema):
    store = make_store(schema)
    status = store.health()
    assert status.healthy
    assert "schema v1" in status.detail
    assert DSN not in status.detail  # the DSN carries credentials
    assert "postgresql://" not in status.detail
    store.close()


@requires_pg
def test_health_never_raises_after_close(schema):
    store = make_store(schema)
    store.close()
    status = store.health()
    assert status.healthy is False
    assert status.detail  # says what went wrong without raising
    store.close()  # idempotent


# --- always-run tests (no gate) ----------------------------------------------


def test_content_hash_parity_with_contract_semantics():
    # adapters may not import core, so the hash is duplicated in the adapter;
    # this pins it to the canonical sha256-of-canonical-JSON definition.
    record = MemoryRecord(kind="semantic", subject="s", predicate="p", value="v", source="src")
    payload = json.dumps(["semantic", "s", "p", "v", "src"], ensure_ascii=False, sort_keys=True)
    assert record_content_hash(record) == hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def test_missing_psycopg_raises_friendly_import_error(monkeypatch):
    # simulate psycopg being absent regardless of whether it is installed here
    monkeypatch.setitem(sys.modules, "psycopg", None)
    with pytest.raises(ImportError, match="uv sync --group postgres"):
        PostgresMemoryStore("postgresql://user:pw@127.0.0.1:1/nope")


def test_unreachable_dsn_is_health_driven_and_leaks_no_credentials():
    """Construction is health-driven: an unreachable server never raises from
    __init__ (the failure is stored lazily), health() reports unhealthy with
    no DSN credential material, and write/retrieve raise BlockUnavailableError."""
    # bogus password + bogus database: even if something answered on port 1,
    # auth/db selection would still fail; connect_timeout avoids long hangs
    dsn = "postgresql://ultron:s3cret-pw@127.0.0.1:1/ultron_none?connect_timeout=3"
    try:
        store = PostgresMemoryStore(dsn)
    except ImportError as exc:
        # psycopg not installed in this environment: friendly message, no crash
        assert "uv sync --group postgres" in str(exc)
        return

    status = store.health()
    assert status.healthy is False
    assert status.detail
    assert "s3cret-pw" not in status.detail  # no credential material
    assert dsn not in status.detail  # no full DSN
    assert "postgresql://" not in status.detail

    run = RunContext()
    with pytest.raises(BlockUnavailableError):
        store.write(run, MemoryRecord(kind="identity", subject="s", predicate="p", value="v"))
    with pytest.raises(BlockUnavailableError):
        store.retrieve(run, MemoryQuery())
    store.close()  # no-op on a dead connection, must not raise
