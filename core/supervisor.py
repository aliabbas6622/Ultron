"""Self-monitoring and self-diagnostics (supervisor layer).

The runtime watches its own blocks (03: every block declares "health
semantics" and "failure semantics") with three composable pieces, none of
which knows a concrete block:

- CircuitBreaker: per-block failure latch. closed -> open after
  `failure_threshold` failures -> half_open once `cooldown_s` has elapsed.
  An open breaker means "stop touching this block"; half_open means "one
  probe": a successful probe closes the breaker, a failed one re-opens it
  with a fresh cooldown.
- FallbackChain: ordered (name, health_fn, breaker) entries; resolve()
  picks the first healthy block whose breaker allows a call, records
  success/failure on the way past, and documents every skip in the reason.
- ComponentMonitor: polls health callables and publishes
  ComponentDegraded / ComponentRecovered on the EventBus exactly when a
  block's status flips — never on steady state, never on the baseline check.

Health arrives as callables, so any brick composes in from outside (LEGO
rule); this module imports no block implementation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from core.bus import EventBus
from core.events import ComponentDegraded, ComponentRecovered
from core.ids import new_run_id, new_trace_id

__all__ = ["BlockHealth", "CircuitBreaker", "FallbackChain", "ComponentMonitor"]


@dataclass(frozen=True)
class BlockHealth:
    """One block's health at an instant. `detail` is a short reason string
    (a health code, not a payload or secret — 09 Logging)."""

    name: str
    healthy: bool
    detail: str = ""


HealthFn = Callable[[], BlockHealth]


class CircuitBreaker:
    """Failure latch for one block.

    States:
    - closed:    failures accumulate; calls allowed.
    - open:      threshold reached at `opened_at`; calls refused until the
                 cooldown elapses. Extra failures while open do NOT extend
                 the cooldown — it stays anchored at the moment it opened.
    - half_open: cooldown elapsed; the next allowed call is a probe.
                 record_success() closes the breaker, record_failure()
                 re-opens it with a fresh cooldown.
    """

    def __init__(self, name: str, failure_threshold: int = 3, cooldown_s: float = 60.0) -> None:
        self.name = name
        self.failure_threshold = failure_threshold
        self.cooldown_s = cooldown_s
        self._state = "closed"
        self._failures = 0
        self._opened_at: float | None = None

    @property
    def state(self) -> str:
        """One of "closed" | "open" | "half_open"."""
        return self._state

    @property
    def opened_at(self) -> float | None:
        """When the breaker opened, None while closed (diagnostics aid)."""
        return self._opened_at

    def record_success(self) -> None:
        """A successful call closes the breaker from any state and resets it."""
        self._state = "closed"
        self._failures = 0
        self._opened_at = None

    def record_failure(self, now: float) -> None:
        """Record a failed call. Reaching the threshold opens the breaker;
        a failure while half_open re-opens it immediately (fresh cooldown)."""
        if self._state == "open":
            return  # already open; cooldown stays anchored at opened_at
        if self._state == "half_open":
            self._open(now)
            return
        self._failures += 1
        if self._failures >= self.failure_threshold:
            self._open(now)

    def allows(self, now: float) -> bool:
        """May the block be called at `now`? The open -> half_open transition
        happens here, on the first call at or after the cooldown expires."""
        if self._state == "closed":
            return True
        if self._state == "open":
            if self._opened_at is not None and now < self._opened_at + self.cooldown_s:
                return False
            self._state = "half_open"
            return True
        return True  # half_open: probes allowed

    def _open(self, now: float) -> None:
        self._state = "open"
        self._opened_at = now


class FallbackChain:
    """Ordered fallback over blocks: resolve() picks the first entry whose
    breaker allows a call AND whose health is healthy.

    Side effects while resolving: an open breaker is never probed (that is
    the point of the breaker); an unhealthy probe records a failure on that
    block's breaker (which may open it); the chosen block's breaker records
    a success.
    """

    def __init__(self, entries: list[tuple[str, HealthFn, CircuitBreaker]]) -> None:
        self._entries: list[tuple[str, HealthFn, CircuitBreaker]] = list(entries)

    def resolve(self, now: float) -> tuple[str, str]:
        """(chosen_name, reason). reason documents skipped predecessors,
        e.g. "skipped a:unhealthy, b:breaker-open"; "" when the first entry
        resolved cleanly. ("", "all blocks unavailable") when nothing is
        usable."""
        skips: list[str] = []
        for name, health_fn, breaker in self._entries:
            if not breaker.allows(now):
                skips.append(f"{name}:breaker-open")
                continue
            health = health_fn()
            if not health.healthy:
                breaker.record_failure(now)
                skips.append(f"{name}:unhealthy")
                continue
            breaker.record_success()
            reason = f"skipped {', '.join(skips)}" if skips else ""
            return name, reason
        return "", "all blocks unavailable"


class ComponentMonitor:
    """Watches blocks through health callables and turns status flips into
    ComponentDegraded / ComponentRecovered events on the bus.

    check() publishes ONLY on transitions (healthy -> unhealthy publishes
    ComponentDegraded with reason=health.detail; unhealthy -> healthy
    publishes ComponentRecovered). The first check of a block establishes
    the baseline and publishes nothing. The monitor carries its own
    run/trace identity: it is supervisor infrastructure, not part of any
    user run.
    """

    def __init__(self, bus: EventBus) -> None:
        self._bus = bus
        self._health_fns: dict[str, HealthFn] = {}
        self._last: dict[str, BlockHealth] = {}
        self.run_id = new_run_id()
        self.trace_id = new_trace_id()

    def watch(self, name: str, health_fn: HealthFn) -> None:
        """Register (or replace) a block's health callable."""
        self._health_fns[name] = health_fn

    def check(self, now: float) -> list[BlockHealth]:
        """Probe every watched block once; publish one event per status flip;
        return the current snapshot sorted by name."""
        current: list[BlockHealth] = []
        for name in sorted(self._health_fns):
            health = self._health_fns[name]()
            previous = self._last.get(name)
            self._last[name] = health
            if previous is not None and previous.healthy != health.healthy:
                if health.healthy:
                    self._bus.publish(
                        ComponentRecovered(
                            run_id=self.run_id, trace_id=self.trace_id, at=now, block_id=name
                        )
                    )
                else:
                    self._bus.publish(
                        ComponentDegraded(
                            run_id=self.run_id,
                            trace_id=self.trace_id,
                            at=now,
                            block_id=name,
                            reason=health.detail,
                        )
                    )
            current.append(health)
        return current

    def snapshot(self) -> dict[str, BlockHealth]:
        """Last-known health per watched block (empty before the first check)."""
        return dict(self._last)
