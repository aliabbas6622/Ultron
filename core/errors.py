"""Normalized error taxonomy for ULTRON core. Adapters must map vendor errors onto these."""

from __future__ import annotations


class UltronError(Exception):
    """Base for all normalized runtime errors."""


class CancelledError(UltronError):
    """Run or operation was cancelled via its cancellation token."""


class DeadlineExceededError(UltronError):
    """Run or operation exceeded its deadline."""


class BudgetExceededError(UltronError):
    """A run/worker budget ceiling was hit (tokens, calls, money, writes)."""


class PolicyDeniedError(UltronError):
    """PolicyEngine denied an ActionIntent."""


class BlockUnavailableError(UltronError):
    """A block (model/browser/memory/etc.) failed health check or is unreachable."""


class VerificationFailedError(UltronError):
    """Post-action verification did not confirm the claimed side effect."""


class ContractViolationError(UltronError):
    """An adapter did not satisfy its declared block contract."""
