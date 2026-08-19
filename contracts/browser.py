"""Browser block contract. Adapters implement this; core depends only on this file."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from core.run_context import RunContext

CONTRACT_ID = "browser_provider"
CONTRACT_VERSION = "1.0.0"


@dataclass(frozen=True)
class PageProjection:
    """Extracted/projected page content. Never the raw DOM/HTML dump."""

    url: str
    title: str
    text: str
    truncated: bool = False


@dataclass(frozen=True)
class HealthStatus:
    healthy: bool
    detail: str = ""


class BrowserProvider(Protocol):
    """Required: navigation, extraction, timeout behavior, cancellation, invalid URL handling, health."""

    block_id: str

    def visit(self, run: RunContext, url: str, timeout_s: float) -> PageProjection:
        """Navigate and return a projected page. Must raise core.errors on invalid URL,
        timeout, or cancellation rather than returning a malformed/partial result silently."""
        ...

    def health(self) -> HealthStatus:
        ...
