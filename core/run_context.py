"""RunContext: carried through every run. Cancellation/deadline are primitives, not add-ons."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from core.errors import BudgetExceededError, CancelledError, DeadlineExceededError
from core.ids import new_run_id, new_trace_id


@dataclass
class Budget:
    max_wall_time_s: float | None = None
    max_input_tokens: int | None = None
    max_output_tokens: int | None = None
    max_model_calls: int | None = None
    max_tool_calls: int | None = None
    max_workers: int | None = None
    max_money_usd: float | None = None
    max_external_writes: int | None = None

    # usage counters, mutated as the run progresses
    used_input_tokens: int = 0
    used_output_tokens: int = 0
    used_model_calls: int = 0
    used_tool_calls: int = 0
    used_money_usd: float = 0.0
    used_external_writes: int = 0

    def check(self) -> None:
        checks = (
            (self.max_input_tokens, self.used_input_tokens, "max_input_tokens"),
            (self.max_output_tokens, self.used_output_tokens, "max_output_tokens"),
            (self.max_model_calls, self.used_model_calls, "max_model_calls"),
            (self.max_tool_calls, self.used_tool_calls, "max_tool_calls"),
            (self.max_money_usd, self.used_money_usd, "max_money_usd"),
            (self.max_external_writes, self.used_external_writes, "max_external_writes"),
        )
        for limit, used, name in checks:
            if limit is not None and used > limit:
                raise BudgetExceededError(f"{name} exceeded: {used} > {limit}")

    def child(self) -> "Budget":
        """Sub-budget for a worker. ponytail: same ceilings, no proportional split — split when max_workers>1 fan-out lands."""
        return Budget(
            max_wall_time_s=self.max_wall_time_s,
            max_input_tokens=self.max_input_tokens,
            max_output_tokens=self.max_output_tokens,
            max_model_calls=self.max_model_calls,
            max_tool_calls=self.max_tool_calls,
            max_money_usd=self.max_money_usd,
            max_external_writes=self.max_external_writes,
        )


@dataclass
class RunContext:
    run_id: str = field(default_factory=new_run_id)
    trace_id: str = field(default_factory=new_trace_id)
    owner_id: str | None = None
    trust_context: str = "owner"  # 02: owner | trusted | untrusted worker, etc. policy may consult
    deadline_at: float | None = None  # epoch seconds
    budget: Budget = field(default_factory=Budget)
    # cross-cutting run record: instruction bundle versions used, context
    # breakdown, verification outcome. Populated by the runtime, read by
    # telemetry/eval (05: "Every run should record the instruction versions used").
    metadata: dict = field(default_factory=dict)
    _cancelled: bool = field(default=False, repr=False)

    def cancel(self) -> None:
        self._cancelled = True

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    def check_alive(self) -> None:
        """Raise if this run must stop now. Call at every await/loop boundary."""
        if self._cancelled:
            raise CancelledError(f"run {self.run_id} cancelled")
        if self.deadline_at is not None and time.time() > self.deadline_at:
            raise DeadlineExceededError(f"run {self.run_id} deadline exceeded")
        self.budget.check()

    def child(self) -> "RunContext":
        return RunContext(
            trace_id=self.trace_id,
            owner_id=self.owner_id,
            deadline_at=self.deadline_at,
            budget=self.budget.child(),
        )
