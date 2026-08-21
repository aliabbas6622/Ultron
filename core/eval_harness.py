"""Eval Harness — V0.1 (08_ACCEPTANCE_AND_ROADMAP.md).

Deterministic fixtures + replay fixtures + quality/efficiency metrics:

- A Scenario is a deterministic fixture: frozen page projection, frozen model
  response, expected policy decision, expected side effect, expected route.
- ReplayBrowser/ReplayModel replay those frozen responses (no network, no model
  cost) and record exactly what the runtime asked of them, so infrastructure
  changes can be re-evaluated against identical block behavior.
- run_scenario() executes the fixture through the real runtime and grades the
  outcome + RunMetrics against the expectations, returning an EvalResult.
- freeze() pins the observed compiled-prompt hash into the fixture: later
  infrastructure changes must keep producing the same prompt for the frozen
  block behavior, or the replay eval fails.

"A routing/context optimization is not accepted solely because it uses fewer
tokens" — pass quality expectations AND budget expectations, never tokens alone.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field, replace

from contracts.browser import HealthStatus as BrowserHealth
from contracts.browser import PageProjection
from contracts.model import HealthStatus as ModelHealth
from contracts.model import ModelCapabilities, ModelResult
from core.bus import EventBus
from core.errors import (
    BlockUnavailableError,
    BudgetExceededError,
    CancelledError,
    DeadlineExceededError,
    PolicyDeniedError,
)
from core.policy import PolicyEngine
from core.run_context import RunContext
from core.runtime import run_visit_summarize_save
from core.telemetry import MetricsRecorder, RunMetrics

# snake_case fixture keys -> normalized error class a frozen block response replays
ERROR_CLASSES: dict[str, type[Exception]] = {
    "block_unavailable": BlockUnavailableError,
    "cancelled": CancelledError,
    "deadline_exceeded": DeadlineExceededError,
    "budget_exceeded": BudgetExceededError,
}


class ReplayBrowser:
    """Frozen BrowserProvider double: replays the scenario's page projection."""

    block_id = "replay_browser"

    def __init__(self, scenario: "Scenario") -> None:
        self._scenario = scenario
        self.visited: list[tuple[str, float]] = []

    def visit(self, run, url, timeout_s):
        run.check_alive()
        self.visited.append((url, timeout_s))
        if self._scenario.browser_error:
            raise ERROR_CLASSES[self._scenario.browser_error](f"replay: {self._scenario.browser_error}")
        return self._scenario.page

    def health(self):
        return BrowserHealth(healthy=self._scenario.browser_error is None)


class ReplayModel:
    """Frozen ModelProvider double: replays the scenario's response or error and
    records every prompt it received (for replay-fixture hash comparison)."""

    block_id = "replay_model"
    capabilities = ModelCapabilities()

    def __init__(self, scenario: "Scenario") -> None:
        self._scenario = scenario
        self.prompts: list[str] = []

    def generate(self, run, prompt) -> ModelResult:
        run.check_alive()
        self.prompts.append(prompt)
        if self._scenario.model_error:
            raise ERROR_CLASSES[self._scenario.model_error](f"replay: {self._scenario.model_error}")
        return ModelResult(
            text=self._scenario.model_text,
            input_tokens=self._scenario.model_input_tokens or len(prompt) // 4,
            output_tokens=self._scenario.model_output_tokens,
            finish_reason="stop",
            cached_tokens=self._scenario.cached_tokens,
        )

    def health(self):
        return ModelHealth(healthy=self._scenario.model_error is None)


@dataclass(frozen=True)
class Scenario:
    """Deterministic fixture for the V0.1 slice."""

    name: str
    url: str
    page: PageProjection
    model_text: str = ""
    model_input_tokens: int = 0  # 0 -> len(prompt)//4 estimate
    model_output_tokens: int = 8
    cached_tokens: int = 0
    output_filename: str = "summary.txt"
    expected_policy: str = "allow"  # allow | deny
    expected_file_content: str | None = None  # None -> model_text.strip()
    expected_prompt_hash: str | None = None  # None -> not checked (see freeze())
    expected_error_class: str | None = None  # e.g. "PolicyDeniedError", "BlockUnavailableError"
    browser_error: str | None = None  # ERROR_CLASSES key the browser replays
    model_error: str | None = None  # ERROR_CLASSES key the model replays
    tags: tuple[str, ...] = ()

    @property
    def expect_success(self) -> bool:
        return self.expected_error_class is None and self.model_error is None and self.browser_error is None

    @property
    def effective_error_class(self) -> str | None:
        """Explicit expectation wins; otherwise a replayed block error implies it."""
        if self.expected_error_class is not None:
            return self.expected_error_class
        if self.model_error is not None:
            return ERROR_CLASSES[self.model_error].__name__
        if self.browser_error is not None:
            return ERROR_CLASSES[self.browser_error].__name__
        return None


@dataclass(frozen=True)
class EvalResult:
    scenario: str
    passed: bool
    failures: list[str] = field(default_factory=list)
    metrics: RunMetrics | None = None
    observed_prompt_hash: str | None = None

    def __bool__(self) -> bool:
        return self.passed


def prompt_hash(prompt: str) -> str:
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16]


def freeze(scenario: Scenario, workdir: str) -> Scenario:
    """Run once against the frozen blocks and pin the observed prompt hash into
    the fixture. The pinned hash is the replay contract."""
    result = run_scenario(scenario, workdir)
    if not result.passed:
        raise ValueError(f"cannot freeze failing scenario {scenario.name!r}: {result.failures}")
    if result.observed_prompt_hash is None:
        raise ValueError(f"scenario {scenario.name!r} produced no prompt to freeze")
    return replace(scenario, expected_prompt_hash=result.observed_prompt_hash)


def run_scenario(scenario: Scenario, workdir: str) -> EvalResult:
    """Execute a scenario through the real runtime path and grade it."""
    output_path = os.path.join(workdir, scenario.output_filename)
    run = RunContext()
    bus = EventBus()
    recorder = MetricsRecorder(bus, run)
    policy = PolicyEngine(allowed_write_dir=workdir)
    browser = ReplayBrowser(scenario)
    model = ReplayModel(scenario)

    failures: list[str] = []
    error: Exception | None = None
    existed_before = os.path.isfile(output_path)  # workdirs are reused across scenarios
    try:
        run_visit_summarize_save(
            run=run, bus=bus, browser=browser, model=model, policy=policy,
            url=scenario.url, output_path=output_path,
        )
    except Exception as exc:  # normalized runtime errors are graded, not crashed on
        error = exc

    # --- route: exactly one model call, one visit, against the selected blocks
    if len(model.prompts) != 1:
        failures.append(f"route: expected 1 model call, got {len(model.prompts)}")
    if browser.visited != [(scenario.url, 30.0)]:
        failures.append(f"route: browser visits {browser.visited}, expected [({scenario.url!r}, 30.0)]")

    observed = prompt_hash(model.prompts[0]) if model.prompts else None

    # --- replay: compiled prompt must match the frozen hash
    if scenario.expected_prompt_hash is not None and observed is not None:
        if observed != scenario.expected_prompt_hash:
            failures.append(f"replay: prompt hash {observed} != frozen {scenario.expected_prompt_hash}")

    # --- policy expectation
    if scenario.expected_policy == "deny":
        if not isinstance(error, PolicyDeniedError):
            failures.append(f"policy: expected PolicyDeniedError, got {error!r}")
    elif error is not None and scenario.effective_error_class is None:
        failures.append(f"unexpected error: {error!r}")

    # --- normalized error class
    expected_error = scenario.effective_error_class
    if expected_error is not None:
        if error is None:
            failures.append(f"expected error {expected_error}, run succeeded")
        elif type(error).__name__ != expected_error:
            failures.append(f"expected error {expected_error}, got {type(error).__name__}")

    # --- side effect
    expect_write = scenario.expected_policy == "allow" and scenario.expect_success
    if expect_write:
        expected_content = scenario.expected_file_content
        if expected_content is None:
            expected_content = scenario.model_text.strip()
        if not os.path.isfile(output_path):
            failures.append(f"side effect: {output_path} not written")
        else:
            with open(output_path, encoding="utf-8") as f:
                if f.read() != expected_content:
                    failures.append("side effect: file content mismatch")
    elif os.path.isfile(output_path) and not existed_before:
        failures.append("side effect: file written despite expected failure/deny")

    metrics = recorder.finish(error=error)
    # a deny fixture is an expected failure: the run stops at policy, by design
    expected_run_success = scenario.expect_success and scenario.expected_policy == "allow"
    if metrics.success != expected_run_success:
        failures.append(f"metrics: success={metrics.success}, expected {expected_run_success}")

    return EvalResult(
        scenario=scenario.name,
        passed=not failures,
        failures=failures,
        metrics=metrics,
        observed_prompt_hash=observed,
    )


def summarize(results: list[EvalResult]) -> dict:
    """Aggregate for reporting: per-scenario pass + per-run metrics."""
    return {
        "total": len(results),
        "passed": sum(1 for r in results if r.passed),
        "scenarios": {r.scenario: r.passed for r in results},
        "metrics": [r.metrics.to_dict() for r in results if r.metrics],
    }
