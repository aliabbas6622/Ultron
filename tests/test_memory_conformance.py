"""MemoryProvider v1 conformance suite (03_BLOCK_CONTRACT.md).

Every memory implementation must pass this suite. It runs against any
MemoryProvider factory; here it covers the SQLite dev backend. The checks map
1:1 to the contract's conformance clauses: preserves provenance, preserves
confidence, respects supersedes/contradictions semantics, deterministic
conflict behavior, retrieval drops no required fields. Plus the V0.2 "migrations
from day one" requirement.
"""

from __future__ import annotations

import os

import pytest

from contracts.memory import MemoryQuery, MemoryRecord
from core.errors import ContractViolationError
from core.memory_sqlite import SqliteMemoryStore, record_content_hash
from core.run_context import RunContext


def make_store(tmp_path) -> SqliteMemoryStore:
    return SqliteMemoryStore(os.path.join(str(tmp_path), "memory.db"))


def rec(**kw) -> MemoryRecord:
    defaults = dict(
        kind="preferences",
        subject="owner",
        predicate="prefers",
        value="dark mode",
        source="owner_instruction",
        observed_at=1000.0,
        confidence=0.9,
        importance=0.8,
    )
    defaults.update(kw)
    return MemoryRecord(**defaults)


def test_write_fills_id_and_content_hash(tmp_path):
    store = make_store(tmp_path)
    stored = store.write(RunContext(), rec())
    assert stored.id.startswith("mem_")
    assert stored.content_hash == record_content_hash(rec())
    store.close()


def test_preserves_provenance_and_confidence(tmp_path):
    store = make_store(tmp_path)
    written = store.write(RunContext(), rec(source="web:example.com", confidence=0.42))
    got = store.retrieve(RunContext(), MemoryQuery(subject="owner"))[0]
    assert got.source == "web:example.com"  # provenance intact
    assert got.confidence == pytest.approx(0.42)  # confidence intact
    assert got.observed_at == 1000.0
    store.close()


def test_retrieval_does_not_drop_required_fields(tmp_path):
    store = make_store(tmp_path)
    store.write(RunContext(), rec(
        valid_from=900.0, importance=0.7, sensitivity="sensitive", scope="device:workstation",
    ))
    got = store.retrieve(RunContext(), MemoryQuery(subject="owner"))[0]
    # fields that are legitimately nullable when unset
    nullable = {"valid_until", "supersedes"}
    for field in ("id", "kind", "subject", "predicate", "value", "source", "observed_at",
                  "valid_from", "valid_until", "confidence", "importance", "sensitivity",
                  "scope", "supersedes", "contradictions", "content_hash"):
        if field not in nullable:
            assert getattr(got, field) is not None, f"missing {field}"
    assert isinstance(got.contradictions, tuple)
    store.close()


def test_supersedes_hides_superseded_record(tmp_path):
    store = make_store(tmp_path)
    run = RunContext()
    old = store.write(run, rec(value="light mode", observed_at=1000.0))
    new = store.write(run, rec(value="dark mode", observed_at=2000.0, supersedes=old.id))

    got = store.retrieve(run, MemoryQuery(subject="owner"))
    assert [r.id for r in got] == [new.id]
    assert got[0].value == "dark mode"
    store.close()


def test_contradictions_are_recorded_links_not_deletions(tmp_path):
    store = make_store(tmp_path)
    run = RunContext()
    a = store.write(run, rec(value="postgres", observed_at=1000.0))
    b = store.write(run, rec(value="sqlite", observed_at=2000.0, contradictions=(a.id,)))

    # both stay retrievable; the contradiction is carried on the record
    got = store.retrieve(run, MemoryQuery(subject="owner"))
    assert {r.id for r in got} == {a.id, b.id}
    assert got[0].contradictions == (a.id,)
    store.close()


def test_deterministic_conflict_ordering(tmp_path):
    store = make_store(tmp_path)
    run = RunContext()
    ids = [
        store.write(run, rec(value=f"v{i}", observed_at=1000.0 + i, importance=0.5)).id
        for i in range(5)
    ]
    first = store.retrieve(run, MemoryQuery(subject="owner"))
    second = store.retrieve(run, MemoryQuery(subject="owner"))
    assert [r.id for r in first] == [r.id for r in second]  # identical across calls
    # equal importance -> observed_at DESC -> written order reversed
    assert [r.id for r in first] == list(reversed(ids))
    assert [r.observed_at for r in first] == [1004.0, 1003.0, 1002.0, 1001.0, 1000.0]
    store.close()


def test_temporal_validity_filters_expired_records(tmp_path):
    store = make_store(tmp_path)
    run = RunContext()
    store.write(run, rec(value="temporary", observed_at=1000.0, valid_until=1500.0))
    store.write(run, rec(value="permanent", observed_at=1000.0, predicate="uses", valid_until=None))

    got = store.retrieve(run, MemoryQuery(subject="owner", now=2000.0))
    assert [r.value for r in got] == ["permanent"]
    # at a time before expiry the temporary record is still visible
    got_before = store.retrieve(run, MemoryQuery(subject="owner", now=1200.0))
    assert {r.value for r in got_before} == {"temporary", "permanent"}
    store.close()


def test_unknown_supersedes_ref_is_a_contract_violation(tmp_path):
    store = make_store(tmp_path)
    with pytest.raises(ContractViolationError, match="unknown id"):
        store.write(RunContext(), rec(supersedes="mem_does_not_exist"))
    store.close()


def test_unknown_kind_is_rejected(tmp_path):
    store = make_store(tmp_path)
    with pytest.raises(ValueError, match="unknown memory kind"):
        store.write(RunContext(), rec(kind="vibes"))
    store.close()


def test_persistence_across_reopen_and_migrations_from_day_one(tmp_path):
    path = os.path.join(str(tmp_path), "memory.db")
    store = SqliteMemoryStore(path)
    run = RunContext()
    store.write(run, rec(value="dark mode"))
    versions = store.applied_versions()
    store.close()

    reopened = SqliteMemoryStore(path)  # reopening applies no new migrations, keeps data
    assert reopened.applied_versions() == versions == [1]
    assert reopened.retrieve(run, MemoryQuery(subject="owner"))[0].value == "dark mode"
    assert reopened.health().healthy
    reopened.close()
