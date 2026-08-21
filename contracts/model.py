"""ModelProvider v1 contract. Adapters implement this; core/routing depends only on this file.
Self-contained: imports nothing outside contracts/ — any host can implement or drive it."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from contracts.call_context import CallContext
from contracts.health import HealthStatus

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
    cached_tokens: int = 0  # prompt-cache hit tokens, reported for eval metrics


@runtime_checkable
class ModelProvider(Protocol):
    """Required: text_generation, cancellation, health, usage_reporting.
    Optional capabilities are declared via .capabilities and must be checked before use.
    """

    block_id: str
    capabilities: ModelCapabilities

    def generate(self, run: CallContext, prompt: str) -> ModelResult:
        """Blocking text generation. Must honor run.check_alive() at minimum before/after the call
        and raise CancelledError / DeadlineExceededError promptly on cancellation."""
        ...

    def health(self) -> HealthStatus:
        ...
