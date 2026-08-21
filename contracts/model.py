"""ModelProvider v1 contract. Adapters implement this; core/routing depends only on this file.
Self-contained: imports nothing outside contracts/ — any host can implement or drive it."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from contracts.action import new_action_id
from contracts.call_context import CallContext
from contracts.health import HealthStatus

if TYPE_CHECKING:
    from typing import Iterator

    from contracts.tool import ToolDescriptor

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
class ToolCall:
    """A model's proposal to use a tool. NOT an authorization — hosts must run it
    through their PolicyEvaluator before any ToolProvider executes it."""

    kind: str  # must match a ToolDescriptor.kind the host advertised
    params: dict = field(default_factory=dict)
    action_id: str = field(default_factory=new_action_id)


@dataclass(frozen=True)
class ModelResult:
    text: str
    input_tokens: int
    output_tokens: int
    finish_reason: str
    cached_tokens: int = 0  # prompt-cache hit tokens, reported for eval metrics
    tool_calls: tuple[ToolCall, ...] = ()  # non-empty only with the optional tool_calling capability


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


@runtime_checkable
class ToolCallingModel(Protocol):
    """Optional capability on top of ModelProvider v1: native function calling.
    Hosts check isinstance(model, ToolCallingModel) AND capabilities.tool_calling
    before using it; anything else falls back to the text ACTION protocol."""

    def generate_with_tools(self, run: CallContext, prompt: str, tools: "list[ToolDescriptor]") -> ModelResult:
        """Returns ModelResult whose tool_calls carry proposals; text may be empty
        when the model chose to call tools only."""
        ...


@runtime_checkable
class StreamingModel(Protocol):
    """Optional capability on top of ModelProvider v1: streamed text generation.
    Hosts check isinstance(model, StreamingModel) (and capabilities.streaming)
    before using it; non-streaming generate() remains the minimum contract."""

    def generate_stream(self, run: CallContext, prompt: str) -> Iterator[str]:
        """Yield text deltas. Must call run.check_alive() before starting and
        raise CancelledError/DeadlineExceededError/BlockUnavailableError promptly."""
        ...
