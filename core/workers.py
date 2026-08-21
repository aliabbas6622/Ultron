"""Multi-agent workers: temporary specialist agents over any ModelProvider brick.

A WorkerPool fans a batch of WorkerTasks out to a bounded thread pool. Each
worker is a disposable agent (docs: "Disposable workers with strict
sub-budgets and isolated context"):

- ISOLATED CONTEXT: every worker runs on its own RunContext — same trace and
  deadline as the parent, but its own cancellation flag and its own Budget.
  A worker's budget bust or failure never poisons the parent run or its
  siblings ("Workers inherit sub-budgets").
- STRICT SUB-BUDGET: by default each worker gets parent.budget.child() (same
  ceilings, fresh counters). Pass `budget_factory` to hand out different
  budgets (e.g. tighter caps for cheap roles) — the factory's object is used
  verbatim, identity and limits intact.
- GRACEFUL DEGRADATION: any exception a worker raises (model down, budget
  bust, cancellation...) is normalized to WorkerResult.error_class so one
  failing worker can never kill its siblings; results come back in task
  order regardless of completion order.
- COOPERATIVE CANCELLATION: the parent's liveness gates every worker (a
  cancelled/deadline-dead parent fails its whole pool with CancelledError /
  DeadlineExceededError), while each worker's own context stays independent
  — cancelling one child would not stop the others.

Pure composition (03 LEGO rule): the model is an injected ModelProvider v1
brick; this module imports no block implementation.
"""

from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from typing import Callable

from contracts.model import ModelProvider
from core.chat import chat_turn
from core.run_context import Budget, RunContext

__all__ = ["WorkerTask", "WorkerResult", "WorkerPool", "pool_totals"]


def new_worker_id() -> str:
    return f"worker_{uuid.uuid4().hex[:12]}"


@dataclass(frozen=True)
class WorkerTask:
    """One specialist assignment. `worker_id` is auto-filled at spawn."""

    role: str  # e.g. "researcher", "summarizer"
    prompt: str  # the worker's assignment
    worker_id: str = ""  # filled at spawn: f"worker_{uuid4().hex[:12]}"


@dataclass(frozen=True)
class WorkerResult:
    """Normalized outcome: error_class is None only on success."""

    worker_id: str
    role: str
    text: str
    error_class: str | None = None  # normalized failure class name, None on success
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def ok(self) -> bool:
        return self.error_class is None


def pool_totals(results: list[WorkerResult]) -> tuple[int, int]:
    """(sum input_tokens, sum output_tokens) across results — pool bookkeeping."""
    return (
        sum(r.input_tokens for r in results),
        sum(r.output_tokens for r in results),
    )


class WorkerPool:
    """Runs WorkerTasks against a ModelProvider brick in parallel with bounded
    concurrency. Each worker gets its own RunContext — isolated
    cancellation/deadline — and a sub-budget. A failing or cancelled worker
    never kills its siblings (graceful degradation)."""

    def __init__(self, parent: RunContext, max_parallel: int = 2,
                 budget_factory: Callable[[], Budget] | None = None) -> None:
        self.parent = parent
        self.max_parallel = max(1, int(max_parallel))  # clamp: at least one lane
        self.budget_factory = budget_factory

    def _child_context(self) -> RunContext:
        """Isolated context for one worker: parent's trace/owner/deadline, own
        budget (factory-made when provided, else parent.budget.child())."""
        budget = self.budget_factory() if self.budget_factory is not None else self.parent.budget.child()
        return RunContext(
            trace_id=self.parent.trace_id,
            owner_id=self.parent.owner_id,
            deadline_at=self.parent.deadline_at,
            budget=budget,
        )

    def _run_one(self, model: ModelProvider, task: WorkerTask) -> WorkerResult:
        """One worker's whole life. Never raises: ANY exception is normalized
        into an error result so siblings and the caller survive it."""
        worker_id = task.worker_id or new_worker_id()
        task = replace(task, worker_id=worker_id)  # frozen: fill via replace()
        child = self._child_context()
        try:
            # The parent's liveness gates spawning work; the child's own
            # check_alive() inside chat_turn guards the call itself.
            self.parent.check_alive()
            answer = chat_turn(child, model, [], task.prompt)
            return WorkerResult(
                worker_id=worker_id,
                role=task.role,
                text=answer.text,
                input_tokens=child.budget.used_input_tokens,
                output_tokens=child.budget.used_output_tokens,
            )
        except BaseException as exc:  # noqa: BLE001 — graceful degradation: never kill siblings
            return WorkerResult(
                worker_id=worker_id,
                role=task.role,
                text="",
                error_class=type(exc).__name__,
                # partial accounting: whatever this worker burned before dying
                input_tokens=child.budget.used_input_tokens,
                output_tokens=child.budget.used_output_tokens,
            )

    def run_all(self, model: ModelProvider, tasks: list[WorkerTask]) -> list[WorkerResult]:
        """Execute tasks with at most max_parallel in flight; results are
        returned in TASK order regardless of completion order."""
        if not tasks:
            return []
        with ThreadPoolExecutor(max_workers=self.max_parallel) as executor:
            futures = [executor.submit(self._run_one, model, task) for task in tasks]
            return [future.result() for future in futures]  # task order, not completion order
