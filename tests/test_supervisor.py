"""Supervisor tests: CircuitBreaker state machine, FallbackChain ordering
and skip reasons (with breaker integration), ComponentMonitor transition
events. All health fns are plain lambdas; all clocks are fake `now` values —
offline and deterministic."""

from __future__ import annotations

from core.bus import EventBus
from core.events import ComponentDegraded, ComponentRecovered
from core.supervisor import BlockHealth, CircuitBreaker, ComponentMonitor, FallbackChain


# ---------------------------------------------------------------- CircuitBreaker


def test_breaker_starts_closed_and_allows_calls():
    breaker = CircuitBreaker("model")
    assert breaker.state == "closed"
    assert breaker.allows(0.0) is True
    assert breaker.opened_at is None


def test_breaker_opens_after_failure_threshold():
    breaker = CircuitBreaker("model", failure_threshold=3, cooldown_s=60.0)
    breaker.record_failure(0.0)
    breaker.record_failure(1.0)
    assert breaker.state == "closed"  # below threshold: still usable
    assert breaker.allows(2.0) is True
    breaker.record_failure(2.0)
    assert breaker.state == "open"
    assert breaker.opened_at == 2.0
    assert breaker.allows(3.0) is False


def test_breaker_transitions_to_half_open_at_cooldown_boundary():
    breaker = CircuitBreaker("model", failure_threshold=1, cooldown_s=60.0)
    breaker.record_failure(100.0)
    assert breaker.state == "open"
    assert breaker.allows(159.9) is False  # still inside cooldown
    assert breaker.state == "open"  # a refused call must not transition
    assert breaker.allows(160.0) is True  # exactly at opened_at + cooldown
    assert breaker.state == "half_open"
    assert breaker.allows(160.0) is True  # half_open keeps allowing the probe


def test_breaker_success_closes_from_any_state_and_resets_counter():
    from_open = CircuitBreaker("a", failure_threshold=1, cooldown_s=10.0)
    from_open.record_failure(0.0)
    from_open.record_success()
    assert from_open.state == "closed" and from_open.allows(0.0) is True
    assert from_open.opened_at is None

    from_half_open = CircuitBreaker("b", failure_threshold=1, cooldown_s=10.0)
    from_half_open.record_failure(0.0)
    assert from_half_open.allows(10.0) is True  # -> half_open
    from_half_open.record_success()
    assert from_half_open.state == "closed"

    # the failure count is reset on close: threshold-2 breaker survives one failure
    reset = CircuitBreaker("c", failure_threshold=2, cooldown_s=10.0)
    reset.record_failure(0.0)
    reset.record_failure(1.0)  # opens
    reset.record_success()
    reset.record_failure(2.0)
    assert reset.state == "closed"


def test_breaker_failure_in_half_open_reopens_with_fresh_cooldown():
    breaker = CircuitBreaker("model", failure_threshold=1, cooldown_s=10.0)
    breaker.record_failure(0.0)  # open at t=0
    assert breaker.allows(10.0) is True  # probe allowed -> half_open
    breaker.record_failure(12.0)  # failed probe re-opens, anchored at 12
    assert breaker.state == "open"
    assert breaker.opened_at == 12.0
    assert breaker.allows(21.9) is False  # fresh cooldown from the re-open
    assert breaker.allows(22.0) is True
    assert breaker.state == "half_open"


def test_breaker_extra_failures_while_open_do_not_extend_cooldown():
    breaker = CircuitBreaker("model", failure_threshold=1, cooldown_s=10.0)
    breaker.record_failure(0.0)
    breaker.record_failure(5.0)  # already open; must not re-anchor
    assert breaker.state == "open"
    assert breaker.opened_at == 0.0
    assert breaker.allows(10.0) is True  # cooldown from t=0, not t=5
    assert breaker.state == "half_open"


# ---------------------------------------------------------------- FallbackChain


def test_fallback_picks_first_healthy_in_order():
    chain = FallbackChain(
        [
            ("primary", lambda: BlockHealth("primary", True), CircuitBreaker("primary")),
            ("secondary", lambda: BlockHealth("secondary", True), CircuitBreaker("secondary")),
        ]
    )
    assert chain.resolve(0.0) == ("primary", "")


def test_fallback_skips_unhealthy_and_documents_reason():
    primary_breaker = CircuitBreaker("primary")
    chain = FallbackChain(
        [
            ("primary", lambda: BlockHealth("primary", False, "provider unreachable"), primary_breaker),
            ("secondary", lambda: BlockHealth("secondary", True), CircuitBreaker("secondary")),
        ]
    )
    name, reason = chain.resolve(0.0)
    assert name == "secondary"
    assert reason == "skipped primary:unhealthy"
    assert primary_breaker.state == "closed"  # one failure, threshold not reached


def test_fallback_skips_open_breaker_without_probing_it():
    calls: list[str] = []
    primary_breaker = CircuitBreaker("primary", failure_threshold=1)
    primary_breaker.record_failure(0.0)  # open

    def primary_health() -> BlockHealth:
        calls.append("primary")
        return BlockHealth("primary", True)

    chain = FallbackChain(
        [
            ("primary", primary_health, primary_breaker),
            ("secondary", lambda: BlockHealth("secondary", True), CircuitBreaker("secondary")),
        ]
    )
    name, reason = chain.resolve(1.0)
    assert name == "secondary"
    assert reason == "skipped primary:breaker-open"
    assert calls == []  # an open breaker means no health probe at all


def test_fallback_reports_all_unavailable_when_nothing_is_usable():
    primary_breaker = CircuitBreaker("primary", failure_threshold=1)
    primary_breaker.record_failure(0.0)  # open
    chain = FallbackChain(
        [
            ("primary", lambda: BlockHealth("primary", True), primary_breaker),
            ("secondary", lambda: BlockHealth("secondary", False, "down"), CircuitBreaker("secondary")),
        ]
    )
    assert chain.resolve(1.0) == ("", "all blocks unavailable")


def test_fallback_records_success_on_chosen_block():
    secondary_breaker = CircuitBreaker("secondary")
    secondary_breaker.record_failure(0.0)  # dirty the breaker, then let resolve close it
    chain = FallbackChain(
        [
            ("primary", lambda: BlockHealth("primary", False, "down"), CircuitBreaker("primary", failure_threshold=1)),
            ("secondary", lambda: BlockHealth("secondary", True), secondary_breaker),
        ]
    )
    assert chain.resolve(1.0)[0] == "secondary"
    assert secondary_breaker.state == "closed"


def test_fallback_breaker_integration_opens_then_recovers_after_cooldown():
    state = {"healthy": False}
    probes: list[str] = []
    primary_breaker = CircuitBreaker("primary", failure_threshold=3, cooldown_s=60.0)
    secondary_breaker = CircuitBreaker("secondary")

    def primary_health() -> BlockHealth:
        probes.append("primary")
        return BlockHealth("primary", state["healthy"])

    chain = FallbackChain(
        [
            ("primary", primary_health, primary_breaker),
            ("secondary", lambda: BlockHealth("secondary", True), secondary_breaker),
        ]
    )

    # three unhealthy resolves open the primary breaker (opened at t=2)
    assert chain.resolve(0.0)[0] == "secondary"
    assert chain.resolve(1.0)[0] == "secondary"
    assert chain.resolve(2.0)[0] == "secondary"
    assert primary_breaker.state == "open"
    assert len(probes) == 3

    # while open: skipped without probing, secondary keeps serving
    name, reason = chain.resolve(3.0)
    assert (name, reason) == ("secondary", "skipped primary:breaker-open")
    assert len(probes) == 3

    # primary reports healthy again, but the breaker is still cooling down
    state["healthy"] = True
    name, reason = chain.resolve(10.0)
    assert (name, reason) == ("secondary", "skipped primary:breaker-open")
    assert len(probes) == 3

    # cooldown elapsed (opened at t=2): the half_open probe succeeds, closes
    # the breaker, and primary is first in line again
    name, reason = chain.resolve(62.0)
    assert (name, reason) == ("primary", "")
    assert primary_breaker.state == "closed"
    assert secondary_breaker.state == "closed"


# ---------------------------------------------------------------- ComponentMonitor


def _monitor_with_recorder():
    bus = EventBus()
    degraded: list[ComponentDegraded] = []
    recovered: list[ComponentRecovered] = []
    bus.subscribe(ComponentDegraded, degraded.append)
    bus.subscribe(ComponentRecovered, recovered.append)
    return ComponentMonitor(bus), degraded, recovered


def test_monitor_first_check_is_baseline_and_publishes_nothing():
    monitor, degraded, recovered = _monitor_with_recorder()
    monitor.watch("model", lambda: BlockHealth("model", True))
    monitor.watch("browser", lambda: BlockHealth("browser", False, "cold start"))

    results = monitor.check(100.0)

    assert degraded == [] and recovered == []  # first look is a baseline, not a flip
    assert [health.name for health in results] == ["browser", "model"]  # sorted by name
    snapshot = monitor.snapshot()
    assert set(snapshot) == {"model", "browser"}
    assert snapshot["model"].healthy is True
    assert snapshot["browser"].healthy is False
    assert snapshot["browser"].detail == "cold start"


def test_monitor_publishes_exactly_one_degraded_per_flip():
    state = {"healthy": True}
    monitor, degraded, recovered = _monitor_with_recorder()

    def health() -> BlockHealth:
        return BlockHealth("model", state["healthy"], "" if state["healthy"] else "provider unreachable")

    monitor.watch("model", health)
    monitor.check(100.0)  # baseline healthy
    state["healthy"] = False
    monitor.check(200.0)  # flip -> degraded
    monitor.check(300.0)  # steady unhealthy: no new event

    assert len(degraded) == 1
    event = degraded[0]
    assert event.block_id == "model"
    assert event.reason == "provider unreachable"  # reason carries health.detail
    assert event.at == 200.0
    assert recovered == []
    assert monitor.snapshot()["model"].healthy is False


def test_monitor_publishes_exactly_one_recovered_per_flip_back():
    state = {"healthy": False}
    monitor, degraded, recovered = _monitor_with_recorder()

    def health() -> BlockHealth:
        return BlockHealth("model", state["healthy"])

    monitor.watch("model", health)
    monitor.check(100.0)  # baseline unhealthy: no event
    state["healthy"] = True
    monitor.check(200.0)  # flip -> recovered
    monitor.check(300.0)  # steady healthy: no new event

    assert len(recovered) == 1
    assert recovered[0].block_id == "model"
    assert degraded == []
    assert monitor.snapshot()["model"].healthy is True


def test_monitor_alternating_flips_publish_one_event_each():
    state = {"healthy": True}
    monitor, degraded, recovered = _monitor_with_recorder()
    monitor.watch("model", lambda: BlockHealth("model", state["healthy"]))

    monitor.check(0.0)  # baseline
    state["healthy"] = False
    monitor.check(1.0)  # degraded
    monitor.check(2.0)  # steady
    state["healthy"] = True
    monitor.check(3.0)  # recovered
    monitor.check(4.0)  # steady
    state["healthy"] = False
    monitor.check(5.0)  # degraded again

    assert [e.block_id for e in degraded] == ["model", "model"]
    assert [e.at for e in degraded] == [1.0, 5.0]
    assert [e.block_id for e in recovered] == ["model"]
    assert [e.at for e in recovered] == [3.0]


def test_monitor_events_carry_the_monitors_own_identity():
    state = {"healthy": True}
    bus = EventBus()
    degraded: list[ComponentDegraded] = []
    recovered: list[ComponentRecovered] = []
    bus.subscribe(ComponentDegraded, degraded.append)
    bus.subscribe(ComponentRecovered, recovered.append)
    monitor = ComponentMonitor(bus)
    monitor.watch("model", lambda: BlockHealth("model", state["healthy"]))

    monitor.check(0.0)
    state["healthy"] = False
    monitor.check(1.0)
    state["healthy"] = True
    monitor.check(2.0)

    # the monitor is supervisor infrastructure: every event it publishes is
    # traceable to one stable identity, shared across flips
    assert degraded[0].run_id == monitor.run_id
    assert degraded[0].trace_id == monitor.trace_id
    assert recovered[0].run_id == monitor.run_id
    assert recovered[0].trace_id == monitor.trace_id


def test_monitor_snapshot_tracks_only_the_last_check():
    state = {"browser": True, "model": True, "tool": True}
    monitor, _, _ = _monitor_with_recorder()
    monitor.watch("browser", lambda: BlockHealth("browser", state["browser"]))
    monitor.watch("model", lambda: BlockHealth("model", state["model"]))
    monitor.watch("tool", lambda: BlockHealth("tool", state["tool"]))

    monitor.check(0.0)
    state["model"] = False
    results = monitor.check(1.0)

    assert [health.name for health in results] == ["browser", "model", "tool"]
    snapshot = monitor.snapshot()
    assert snapshot == {
        "browser": BlockHealth("browser", True),
        "model": BlockHealth("model", False),
        "tool": BlockHealth("tool", True),
    }


def test_snapshot_is_empty_before_any_check():
    monitor, _, _ = _monitor_with_recorder()
    monitor.watch("model", lambda: BlockHealth("model", True))
    assert monitor.snapshot() == {}
