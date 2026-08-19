"""ModelProvider v1 contract. Adapters implement this; core/routing depends only on this file."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from core.run_context import RunContext

CONTRACT_ID = "model_provider"
CONTRACT_VERSION = "1.0.0"


@dataclass(frozen=True)
class ModelCapabilities:
    streaming: bool = False
    vision: bool = False
    audio: bool = False
    tool_calling: bool = False
    embeddings: bool = False
    prompt_cache: bool = False
    structured_output: bool = False


@dataclass(frozen=True)
class ModelResult:
    text: str
    input_tokens: int
    output_tokens: int
    finish_reason: str


@dataclass(frozen=True)
class HealthStatus:
    healthy: bool
    detail: str = ""


class ModelProvider(Protocol):
    """Required: text_generation, cancellation, health, usage_reporting.
    Optional capabilities are declared via .capabilities and must be checked before use.
    """

    block_id: str
    capabilities: ModelCapabilities

    def generate(self, run: RunContext, prompt: str) -> ModelResult:
        """Blocking text generation. Must honor run.check_alive() at minimum before/after the call
        and raise core.errors.CancelledError / DeadlineExceededError promptly on cancellation."""
        ...

    def health(self) -> HealthStatus:
        ...
