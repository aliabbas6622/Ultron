"""Verifier v1 contract.

"'Done' from the model is not proof" (05) — and neither is a ToolResult
claiming ok=True. A Verifier brick checks the claimed side effect against
external evidence. It is swappable like any block: file-write verification
ships as a brick in tools/; network or DB effects get their own.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from contracts.action import ActionIntent
from contracts.call_context import CallContext
from contracts.tool import ToolResult

CONTRACT_ID = "verifier"
CONTRACT_VERSION = "1.0.0"


@dataclass(frozen=True)
class VerificationOutcome:
    verified: bool
    detail: str = ""


@runtime_checkable
class Verifier(Protocol):
    """Required: verify(intent, result) -> evidence-based outcome. Implementations
    may declare which intent kinds they can verify; a runtime composes verifiers
    per effect type."""

    block_id: str
    supports_kind: str

    def verify(self, run: CallContext, intent: ActionIntent, result: ToolResult) -> VerificationOutcome: ...
