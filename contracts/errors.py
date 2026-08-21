"""Normalized error taxonomy — the interchange failure semantics.

Lives in contracts/ (not core/) because failure semantics are part of every
block contract (03_BLOCK_CONTRACT.md): any host implementing or calling a brick
maps onto these, and any brick can raise these without importing a runtime.
core.errors re-exports this module for backward compatibility.
"""

from __future__ import annotations


class UltronError(Exception):
    """Base for all normalized errors."""


class CancelledError(UltronError):
    """Run or operation was cancelled via its cancellation token."""


class DeadlineExceededError(UltronError):
    """Run or operation exceeded its deadline."""


class BudgetExceededError(UltronError):
    """A run/worker budget ceiling was hit (tokens, calls, money, writes)."""


class PolicyDeniedError(UltronError):
    """PolicyEngine denied an ActionIntent."""


class BlockUnavailableError(UltronError):
    """A block (model/browser/memory/tool/etc.) failed health check or is unreachable."""


class VerificationFailedError(UltronError):
    """Post-action verification did not confirm the claimed side effect."""


class ContractViolationError(UltronError):
    """An adapter did not satisfy its declared block contract."""


class ToolExecutionError(UltronError):
    """A tool block failed to execute a policy-approved ActionIntent."""
