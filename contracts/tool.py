"""ToolProvider v1 contract — the "tools part" as a standalone LEGO brick.

Any host (ULTRON core or any external agent) can:
- take a ToolProvider implementation and execute policy-approved ActionIntents,
- or implement this protocol and have ULTRON drive its tools.

Grab-and-go rule: an implementation of this contract must depend on this
contracts/ package only (plus its own SDKs) — never on a runtime.

Lazy schema loading (06): hosts see the compact descriptor catalog first and
request full schemas per tool only when selected, so a big tool registry never
bloats context by default.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from contracts.action import ActionIntent
from contracts.call_context import CallContext
from contracts.health import HealthStatus

CONTRACT_ID = "tool_provider"
CONTRACT_VERSION = "1.0.0"


@dataclass(frozen=True)
class ToolDescriptor:
    """Compact capability catalog entry. `params_schema` is loaded lazily."""

    kind: str  # matches ActionIntent.kind this tool executes
    name: str  # human-facing label
    risk_class: str = "normal"  # normal | side_effect | destructive
    description: str = ""
    params_schema: dict[str, Any] | None = None  # None until a host asks for it


@dataclass(frozen=True)
class ToolResult:
    ok: bool
    action_id: str
    kind: str
    outputs: dict[str, Any] = field(default_factory=dict)  # e.g. {"path": ..., "bytes_written": ...}
    error: str | None = None  # normalized failure detail when ok is False


@runtime_checkable
class ToolProvider(Protocol):
    """Required: describe, execute, health, cancellation via CallContext.

    Contract: execute() receives only policy-approved intents; a tool that is
    asked to execute a kind it never advertised raises ContractViolationError.
    A failed execution returns ToolResult(ok=False, error=...) — normalized,
    never a raw vendor exception.
    """

    block_id: str

    def describe(self) -> list[ToolDescriptor]: ...

    def execute(self, run: CallContext, intent: ActionIntent) -> ToolResult: ...

    def health(self) -> HealthStatus: ...
