"""Telemetry (05 implementation-order item 11; 09 "Logging").

Two pieces:
- RunMetrics/MetricsRecorder: collects the 08 eval-harness metrics for one run
  (success, verification result, input/output/cached tokens, latency, retries,
  tool calls, cost, context breakdown) by subscribing to the EventBus plus the
  RunContext the runtime mutates.
- log_json / attach_bus_logger: structured JSON logging. One line per event with
  run_id/trace_id/block_id, plus budget usage on completion. Never logs secrets
  or raw tool/page payloads — event fields only.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from core.bus import EventBus
from core.events import (
    ComponentDegraded,
    ComponentRecovered,
    Event,
    ModelCompleted,
    ModelStarted,
    PolicyDenied,
    RunCancelled,
    ToolCompleted,
    VerificationFailed,
)
from core.run_context import RunContext

logger = logging.getLogger("ultron")

_RECORDED_EVENTS: tuple[type[Event], ...] = (
    ModelStarted,
    ModelCompleted,
    ToolCompleted,
    PolicyDenied,
    VerificationFailed,
    ComponentDegraded,
    ComponentRecovered,
    RunCancelled,
)


def log_json(event: str, **fields: Any) -> None:
    """Structured log line: flat JSON on the ultron logger. Fields must already be
    scalar (run_id, block_id, counts...). Callers must not pass secrets or payloads."""
    record = {"ts": round(time.time(), 3), "event": event, **fields}
    logger.info(json.dumps(record, sort_keys=True, default=str))


def attach_bus_logger(bus: EventBus) -> None:
    """Emit one structured JSON line per recorded event type on this bus (09 Logging)."""

    def _make(evt_type: type[Event]):
        def handler(event: Event) -> None:
            log_json(
                "runtime_event",
                event_type=evt_type.__name__,
                run_id=event.run_id,
                trace_id=event.trace_id,
                **{k: v for k, v in vars(event).items() if k not in ("run_id", "trace_id", "at")},
            )

        return handler

    for evt_type in _RECORDED_EVENTS:
        bus.subscribe(evt_type, _make(evt_type))


@dataclass
class RunMetrics:
    """08 eval-harness "Quality/efficiency metrics" for a single run."""

    run_id: str = ""
    trace_id: str = ""
    success: bool = False
    error_class: str | None = None  # normalized failure class, None on success
    verification_ok: bool | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    model_calls: int = 0
    tool_calls: int = 0
    retries: int = 0  # V0.1 has no retry loop; adapters report retries when one exists
    latency_s: float = 0.0
    cost_usd: float = 0.0
    context_breakdown: dict[str, int] = field(default_factory=dict)
    instruction_versions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return dict(vars(self))


class MetricsRecorder:
    """Subscribe before starting the run; call finish() in a finally block."""

    def __init__(self, bus: EventBus, run: RunContext) -> None:
        self._run = run
        self._started = time.perf_counter()
        self._model_calls = 0
        self._tool_calls = 0
        self._input_tokens = 0
        self._output_tokens = 0
        self._cached_tokens = 0
        bus.subscribe(ModelStarted, self._on_model_started)
        bus.subscribe(ModelCompleted, self._on_model_completed)
        bus.subscribe(ToolCompleted, self._on_tool_completed)

    def _on_model_started(self, event: ModelStarted) -> None:
        self._model_calls += 1

    def _on_model_completed(self, event: ModelCompleted) -> None:
        self._input_tokens += event.input_tokens
        self._output_tokens += event.output_tokens
        self._cached_tokens += event.cached_tokens

    def _on_tool_completed(self, event: ToolCompleted) -> None:
        if event.ok:
            self._tool_calls += 1

    def finish(self, error: Exception | None = None) -> RunMetrics:
        budget = self._run.budget
        return RunMetrics(
            run_id=self._run.run_id,
            trace_id=self._run.trace_id,
            success=error is None,
            error_class=type(error).__name__ if error is not None else None,
            verification_ok=self._run.metadata.get("verification_ok"),
            input_tokens=self._input_tokens or budget.used_input_tokens,
            output_tokens=self._output_tokens or budget.used_output_tokens,
            cached_tokens=self._cached_tokens,
            model_calls=self._model_calls,
            tool_calls=self._tool_calls,
            latency_s=round(time.perf_counter() - self._started, 6),
            cost_usd=budget.used_money_usd,
            context_breakdown=dict(self._run.metadata.get("context_breakdown", {})),
            instruction_versions=list(self._run.metadata.get("instructions", [])),
        )
