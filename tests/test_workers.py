"""WorkerPool tests: disposable specialists with strict sub-budgets and
isolated context. Everything is offline and deterministic — a fake model
brick, no network, no sleeps beyond tiny scripted delays."""

from __future__ import annotations

import re
import threading
import time
from dataclasses import replace

from contracts.errors import BlockUnavailableError
from contracts.model import HealthStatus, ModelCapabilities, ModelResult
from core.run_context import Budget, RunContext
from core.workers import WorkerPool, WorkerResult, WorkerTask, pool_totals

WORKER_ID_RE = re.compile(r"^worker_[0-9a-f]{12}$")


def _user_line(prompt: str) -> str:
    """chat_turn compiles the prompt; the assignment is its last 'User:' line."""
    for line in reversed(prompt.splitlines()):
        if line.startswith("User: "):
            return line[len("User: "):]
    return ""


class FakeWorkerModel:
    """Deterministic brick: replies 'out::<assignment>' with fixed usage.
    `fail_with` maps an assignment to an exception CLASS to raise (scripted
    failure), `delay_for` maps one to a sleep (to shuffle completion order)."""

    block_id = "fake-workers"
    capabilities = ModelCapabilities()

    def __init__(self, fail_with: dict | None = None, delay_for: dict | None = None):
        self.fail_with = dict(fail_with or {})
        self.delay_for = dict(delay_for or {})

    def generate(self, run, prompt):
        run.check_alive()
        user = _user_line(prompt)
        delay = self.delay_for.get(user, 0.0)
        if delay:
            time.sleep(delay)
        exc_type = self.fail_with.get(user)
        if exc_type is not None:
            raise exc_type(f"scripted failure for {user!r}")
        return ModelResult(text=f"out::{user}", input_tokens=5, output_tokens=5,
                           finish_reason="stop")

    def health(self):
        return HealthStatus(healthy=True)


class ConcurrencyProbeModel:
    """Deterministic concurrency probe: every generate() blocks at a Barrier
    until `parties` calls are in flight simultaneously (so overlap is proven,
    not hoped for), while a lock tracks the high-water mark of concurrency."""

    block_id = "concurrency-probe"
    capabilities = ModelCapabilities()

    def __init__(self, parties: int):
        self.parties = parties
        self._barrier = threading.Barrier(parties)
        self._lock = threading.Lock()
        self._in_flight = 0
        self.max_observed = 0

    def generate(self, run, prompt):
        run.check_alive()
        with self._lock:
            self._in_flight += 1
            self.max_observed = max(self.max_observed, self._in_flight)
        try:
            self._barrier.wait(timeout=10.0)
            time.sleep(0.005)
        finally:
            with self._lock:
                self._in_flight -= 1
        return ModelResult(text="ok", input_tokens=1, output_tokens=1, finish_reason="stop")

    def health(self):
        return HealthStatus(healthy=True)


def test_results_in_task_order_regardless_of_completion_order():
    # later tasks finish first (longer delays earlier); results must stay in task order
    model = FakeWorkerModel(delay_for={"alpha": 0.05, "beta": 0.03, "gamma": 0.01, "delta": 0.0})
    tasks = [WorkerTask(role=f"role-{name}", prompt=name) for name in ("alpha", "beta", "gamma", "delta")]
    results = WorkerPool(RunContext(), max_parallel=4).run_all(model, tasks)
    assert [r.role for r in results] == ["role-alpha", "role-beta", "role-gamma", "role-delta"]
    assert [r.text for r in results] == ["out::alpha", "out::beta", "out::gamma", "out::delta"]
    assert all(r.error_class is None for r in results)


def test_failing_worker_does_not_kill_siblings():
    model = FakeWorkerModel(fail_with={"explode": BlockUnavailableError, "nope": ValueError})
    tasks = [
        WorkerTask("ok-1", "fine"),
        WorkerTask("boom", "explode"),
        WorkerTask("ok-2", "also fine"),
        WorkerTask("generic", "nope"),
    ]
    results = WorkerPool(RunContext(), max_parallel=2).run_all(model, tasks)
    assert results[0].error_class is None and results[0].text == "out::fine"
    assert results[1].error_class == "BlockUnavailableError" and results[1].text == ""
    assert results[2].error_class is None and results[2].text == "out::also fine"
    assert results[3].error_class == "ValueError" and results[3].text == ""


def test_max_parallel_respected():
    probe = ConcurrencyProbeModel(parties=2)
    tasks = [WorkerTask("r", f"job-{i}") for i in range(6)]
    results = WorkerPool(RunContext(), max_parallel=2).run_all(probe, tasks)
    assert all(r.error_class is None for r in results)  # every barrier met: real 2-way overlap
    assert probe.max_observed <= 2
    assert probe.max_observed == 2  # and never more than the pool's bound


def test_max_parallel_clamped_to_at_least_one():
    pool = WorkerPool(RunContext(), max_parallel=0)
    assert pool.max_parallel == 1
    results = pool.run_all(FakeWorkerModel(), [WorkerTask("a", "x"), WorkerTask("b", "y")])
    assert [r.error_class for r in results] == [None, None]


def test_budget_factory_used_when_provided():
    parent = RunContext(budget=Budget(max_input_tokens=999))
    shared = Budget(max_input_tokens=50, max_output_tokens=50)
    calls: list[int] = []

    def factory() -> Budget:
        calls.append(1)
        return shared  # same object every time: identity is observable via its counters

    pool = WorkerPool(parent, max_parallel=1, budget_factory=factory)
    results = pool.run_all(FakeWorkerModel(), [WorkerTask("r", f"q{i}") for i in range(3)])
    assert all(r.error_class is None for r in results)
    assert len(calls) == 3  # one sub-budget per worker
    # the factory's exact object was the workers' budget (identity via mutation)
    assert shared.used_model_calls == 3
    assert shared.used_input_tokens == 15 and shared.used_output_tokens == 15
    # the parent's own budget was never billed
    assert parent.budget.used_model_calls == 0 and parent.budget.used_input_tokens == 0


def test_budget_factory_limits_gate_workers():
    def factory() -> Budget:
        # already over max_model_calls: every worker must refuse to run
        return Budget(max_model_calls=0, used_model_calls=1)

    pool = WorkerPool(RunContext(), max_parallel=2, budget_factory=factory)
    results = pool.run_all(FakeWorkerModel(), [WorkerTask("a", "x"), WorkerTask("b", "y")])
    assert [r.error_class for r in results] == ["BudgetExceededError", "BudgetExceededError"]
    assert all(r.text == "" for r in results)


def test_workers_get_isolated_default_subbudgets():
    parent = RunContext(budget=Budget(max_input_tokens=100))
    tasks = [WorkerTask("r", f"q{i}") for i in range(3)]
    results = WorkerPool(parent, max_parallel=2).run_all(FakeWorkerModel(), tasks)
    # per-worker counters (a shared budget would read 5/10/15 cumulatively)
    assert [r.input_tokens for r in results] == [5, 5, 5]
    assert [r.output_tokens for r in results] == [5, 5, 5]
    # workers never bill the parent's budget
    assert parent.budget.used_input_tokens == 0
    assert parent.budget.used_output_tokens == 0
    assert parent.budget.used_model_calls == 0


def test_worker_ids_autofilled_unique_and_preserved():
    tasks = [WorkerTask(f"r{i}", f"q{i}") for i in range(4)]
    tasks[2] = replace(tasks[2], worker_id="worker_preset0000")
    results = WorkerPool(RunContext(), max_parallel=2).run_all(FakeWorkerModel(), tasks)
    ids = [r.worker_id for r in results]
    assert ids[2] == "worker_preset0000"  # caller-provided id is preserved, not replaced
    for wid in ids[:2] + ids[3:]:
        assert WORKER_ID_RE.match(wid), f"bad auto id {wid!r}"
    assert len(set(ids)) == 4  # unique


def test_parent_cancel_before_run_all_fails_every_worker():
    parent = RunContext()
    parent.cancel()
    tasks = [WorkerTask("r", f"q{i}") for i in range(3)]
    results = WorkerPool(parent, max_parallel=2).run_all(FakeWorkerModel(), tasks)
    assert [r.error_class for r in results] == ["CancelledError"] * 3
    assert all(r.text == "" for r in results)


def test_parent_deadline_expired_fails_every_worker():
    parent = RunContext(deadline_at=time.time() - 1.0)
    results = WorkerPool(parent, max_parallel=2).run_all(FakeWorkerModel(), [WorkerTask("r", "q")])
    assert [r.error_class for r in results] == ["DeadlineExceededError"]


def test_pool_totals_sums_usage():
    results = WorkerPool(RunContext(), max_parallel=2).run_all(
        FakeWorkerModel(), [WorkerTask("a", "x"), WorkerTask("b", "y")]
    )
    assert pool_totals(results) == (10, 10)
    assert pool_totals([]) == (0, 0)
    mixed = [
        WorkerResult("w1", "r", "text", None, 3, 4),
        WorkerResult("w2", "r", "", "BlockUnavailableError", 7, 0),
    ]
    assert pool_totals(mixed) == (10, 4)


def test_run_all_with_no_tasks_returns_empty():
    assert WorkerPool(RunContext()).run_all(FakeWorkerModel(), []) == []
