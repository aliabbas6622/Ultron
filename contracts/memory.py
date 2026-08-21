"""MemoryProvider v1 contract (V0.2: persistent structured memory + migrations).

Memory is NOT conversation replay: records are typed statements with provenance,
confidence, and temporal validity. Supersession is explicit via `supersedes`;
contradictions are recorded links, never silent overwrites. Deterministic
conflict behavior: retrieval prefers the newest valid observation, tie-broken
by id, so two retrievals of the same state always return the same order.
Execution/task state stays OUT of this block (06: separate from memory).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from core.run_context import RunContext

CONTRACT_ID = "memory_provider"
CONTRACT_VERSION = "1.0.0"

# 06 initial memory classes
MEMORY_KINDS = (
    "identity",
    "preferences",
    "semantic",
    "episodic",
    "procedural",
    "relationship",
    "environmental",
)


@dataclass(frozen=True)
class MemoryRecord:
    # the statement: subject-predicate-value, e.g. ("owner", "prefers", "dark mode")
    kind: str  # one of MEMORY_KINDS
    subject: str
    predicate: str
    value: str

    # identity (store-assigned on write when omitted)
    id: str = ""

    # provenance (03 conformance: must survive write+retrieval intact)
    source: str = ""  # e.g. "owner_instruction", "web:example.com"
    observed_at: float = 0.0  # epoch seconds
    valid_from: float = 0.0
    valid_until: float | None = None  # None -> no expiry

    # quality/labeling
    confidence: float = 1.0  # 0..1
    importance: float = 0.5  # 0..1, retrieval ordering input
    sensitivity: str = "normal"  # normal | sensitive
    scope: str = "global"

    # conflict semantics
    supersedes: str | None = None  # id of the record this replaces
    contradictions: tuple[str, ...] = ()  # ids this record contradicts (kept as links)

    content_hash: str = ""  # set by the store on write; tamper-evidence for the payload

    def __post_init__(self) -> None:
        if self.kind not in MEMORY_KINDS:
            raise ValueError(f"unknown memory kind: {self.kind!r} (expected one of {MEMORY_KINDS})")


@dataclass(frozen=True)
class MemoryQuery:
    kind: str | None = None
    subject: str | None = None
    predicate: str | None = None
    scope: str | None = None
    limit: int = 50
    now: float | None = None  # temporal-validity reference; None -> caller's clock


@dataclass(frozen=True)
class HealthStatus:
    healthy: bool
    detail: str = ""


class MemoryProvider(Protocol):
    """Required: write, retrieve, health. Adapters implement this against a
    persistent backend (SQLite for dev, PostgreSQL for default per 04_TECH_STACK)."""

    block_id: str

    def write(self, run: RunContext, record: MemoryRecord) -> MemoryRecord:
        """Persist a record; returns it with id + content_hash filled in. Writing a
        record whose supersedes/contradictions point at unknown ids is a
        ContractViolationError. Must honor run.check_alive()."""
        ...

    def retrieve(self, run: RunContext, query: MemoryQuery) -> list[MemoryRecord]:
        """Deterministically ordered, valid-at-`now`, superseded records excluded.
        Never drops required fields (full records or nothing)."""
        ...

    def health(self) -> HealthStatus:
        ...
