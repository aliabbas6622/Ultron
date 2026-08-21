"""ModelRouter tests: every doc-06 routing rule (L0-L3), edge cases
(no candidates, privacy without local, hard with a single provider),
tie-break determinism, constructor copy isolation, and spec validation.
All offline — the router is a pure function of (candidates, request)."""

from __future__ import annotations

import dataclasses

import pytest

from core.router import (
    DIFFICULTIES,
    KINDS,
    CandidateSpec,
    ModelRouter,
    RouteDecision,
    RouteRequest,
)


def spec(name: str, kind: str, tier: int, *, healthy: bool = True,
         local: bool | None = None, model: str = "") -> CandidateSpec:
    if local is None:
        local = kind in ("stub", "ollama")
    return CandidateSpec(name=name, kind=kind, cost_tier=tier,
                         local=local, healthy=healthy, model=model)


STUB = spec("stub", "stub", 0)
LOCAL = spec("ollama-local", "ollama", 1)
CLOUD_STD = spec("cloud-std", "openai-compatible", 2)
CLOUD_TOOL = spec("cloud-tool", "ollama-cloud", 2)
PREMIUM = spec("premium", "openai-compatible", 3)
FULL_REGISTRY = [PREMIUM, CLOUD_TOOL, CLOUD_STD, LOCAL, STUB]


# --- dataclass shape -------------------------------------------------------------

def test_candidate_spec_and_request_defaults():
    assert STUB.model == ""
    req = RouteRequest()
    assert (req.task_text, req.privacy_required, req.tool_use_needed,
            req.difficulty, req.latency_target_s, req.estimated_tokens) == (
        "", False, False, "normal", None, None)
    assert RouteDecision("L1", "x", "r").fallback_chain == ()
    assert set(DIFFICULTIES) == {"trivial", "normal", "hard"}
    assert set(KINDS) == {"stub", "ollama", "ollama-cloud", "openai-compatible"}


@pytest.mark.parametrize("bad_kind", ["cloud", "", "OLLAMA", None])
def test_candidate_spec_rejects_bad_kind(bad_kind):
    with pytest.raises(ValueError):
        spec("x", bad_kind, 1)


@pytest.mark.parametrize("bad_tier", [-1, 5, 100])
def test_candidate_spec_rejects_tier_out_of_range(bad_tier):
    with pytest.raises(ValueError):
        spec("x", "ollama", bad_tier)


@pytest.mark.parametrize("bad_difficulty", ["easy", "", "HARD", None])
def test_route_request_rejects_bad_difficulty(bad_difficulty):
    with pytest.raises(ValueError):
        RouteRequest(difficulty=bad_difficulty)


# --- rule 1: no healthy provider --------------------------------------------------

def test_empty_registry_refuses_to_route():
    assert ModelRouter([]).route(RouteRequest()) == RouteDecision(
        "L0", "", "no healthy provider", ())


def test_all_unhealthy_candidates_refuse_to_route():
    router = ModelRouter([
        spec("dead-local", "ollama", 1, healthy=False),
        spec("dead-cloud", "openai-compatible", 2, healthy=False),
    ])
    decision = router.route(RouteRequest(difficulty="hard", tool_use_needed=True))
    assert decision == RouteDecision("L0", "", "no healthy provider", ())
    # unhealthy candidates never appear in any chain
    assert decision.fallback_chain == ()


# --- rule 2: privacy ---------------------------------------------------------------

def test_privacy_routes_to_cheapest_local_even_when_cloud_is_named_first():
    router = ModelRouter([spec("a-cloud", "openai-compatible", 2), LOCAL])
    decision = router.route(RouteRequest(privacy_required=True, difficulty="hard"))
    assert decision.level == "L1"
    assert decision.provider_name == "ollama-local"
    assert "privacy" in decision.reason
    # hard difficulty must NOT override privacy: rule 2 runs first
    assert decision.fallback_chain == ()


def test_privacy_with_no_healthy_local_refuses_at_l0():
    router = ModelRouter([CLOUD_STD, PREMIUM,
                          spec("dead-local", "ollama", 1, healthy=False)])
    decision = router.route(RouteRequest(privacy_required=True))
    assert decision.level == "L0"
    assert decision.provider_name == ""
    assert decision.fallback_chain == ()
    assert "privacy" in decision.reason
    assert "local" in decision.reason


def test_privacy_tie_break_is_alphabetical_and_chain_stays_local():
    router = ModelRouter([spec("zeta", "ollama", 1), spec("alpha", "ollama", 1),
                          CLOUD_STD])
    decision = router.route(RouteRequest(privacy_required=True))
    assert decision.provider_name == "alpha"
    # cloud names must never appear in a privacy fallback chain
    assert decision.fallback_chain == ("zeta",)


def test_privacy_with_healthy_stub_picks_it_at_l0():
    router = ModelRouter([STUB, LOCAL])
    decision = router.route(RouteRequest(privacy_required=True))
    assert decision.provider_name == "stub"
    assert decision.level == "L0"  # the stub IS deterministic code
    assert decision.fallback_chain == ("ollama-local",)


# --- rule 3: trivial ----------------------------------------------------------------

def test_trivial_uses_free_stub_at_l0():
    decision = ModelRouter(FULL_REGISTRY).route(RouteRequest(difficulty="trivial"))
    assert decision == RouteDecision("L0", "stub", "trivial: free stub",
        ("ollama-local", "cloud-std", "cloud-tool", "premium"))


def test_trivial_without_stub_picks_cheapest_local_l1():
    router = ModelRouter([LOCAL, CLOUD_STD, PREMIUM])
    decision = router.route(RouteRequest(difficulty="trivial"))
    assert decision.level == "L1"
    assert decision.provider_name == "ollama-local"
    assert "trivial" in decision.reason


def test_trivial_with_only_cloud_goes_l2():
    router = ModelRouter([spec("b", "openai-compatible", 2),
                          spec("a", "ollama-cloud", 2)])
    decision = router.route(RouteRequest(difficulty="trivial"))
    assert decision.level == "L2"
    assert decision.provider_name == "a"
    assert decision.fallback_chain == ("b",)


def test_trivial_beats_tool_use():
    router = ModelRouter(FULL_REGISTRY)
    decision = router.route(RouteRequest(difficulty="trivial", tool_use_needed=True))
    assert decision.provider_name == "stub"
    assert decision.level == "L0"


# --- rule 4: hard --------------------------------------------------------------------

def test_hard_picks_highest_tier_at_l3_with_descending_fallbacks():
    decision = ModelRouter(FULL_REGISTRY).route(RouteRequest(difficulty="hard"))
    assert decision.level == "L3"
    assert decision.provider_name == "premium"
    assert "tier 3" in decision.reason
    # remaining healthy, strongest first; tier-2 tie broken alphabetically
    assert decision.fallback_chain == ("cloud-std", "cloud-tool", "ollama-local", "stub")


def test_hard_with_single_provider_has_empty_chain():
    router = ModelRouter([LOCAL])
    decision = router.route(RouteRequest(difficulty="hard"))
    assert decision.provider_name == "ollama-local"
    assert decision.level == "L3"
    assert decision.fallback_chain == ()


def test_hard_tie_at_top_tier_breaks_alphabetically():
    router = ModelRouter([spec("zeta", "openai-compatible", 3),
                          spec("alpha", "ollama-cloud", 3)])
    decision = router.route(RouteRequest(difficulty="hard"))
    assert decision.provider_name == "alpha"
    assert decision.fallback_chain == ("zeta",)


def test_hard_beats_tool_use():
    router = ModelRouter([PREMIUM, CLOUD_TOOL, LOCAL, STUB])
    decision = router.route(RouteRequest(difficulty="hard", tool_use_needed=True))
    assert decision.level == "L3"
    assert decision.provider_name == "premium"


def test_hard_ignores_unhealthy_top_tier():
    router = ModelRouter([spec("premium", "openai-compatible", 3, healthy=False),
                          CLOUD_TOOL, LOCAL])
    decision = router.route(RouteRequest(difficulty="hard"))
    assert decision.provider_name == "cloud-tool"
    assert decision.fallback_chain == ("ollama-local",)


# --- rule 5: tool use ------------------------------------------------------------------

def test_tool_use_prefers_tool_kinds_at_l2_with_cost_ascending_chain():
    decision = ModelRouter(FULL_REGISTRY).route(RouteRequest(tool_use_needed=True))
    assert decision.level == "L2"
    assert decision.provider_name == "cloud-std"  # cheapest tool-capable
    assert "tool" in decision.reason
    # everyone else healthy, cheapest first, tier-1 local over premium cloud
    assert decision.fallback_chain == ("stub", "ollama-local", "cloud-tool", "premium")


def test_tool_use_picks_cheapest_tool_kind_alphabetically_on_tie():
    router = ModelRouter([spec("zeta", "openai-compatible", 2),
                          spec("beta", "ollama-cloud", 2),
                          spec("alpha", "ollama-cloud", 2)])
    decision = router.route(RouteRequest(tool_use_needed=True))
    assert decision.provider_name == "alpha"
    assert decision.fallback_chain == ("beta", "zeta")


def test_tool_use_without_tool_capable_kinds_falls_back_to_cheapest_non_stub():
    router = ModelRouter([STUB, LOCAL])
    decision = router.route(RouteRequest(tool_use_needed=True))
    assert decision.level == "L1"
    assert decision.provider_name == "ollama-local"
    assert "no tool-capable provider" in decision.reason
    assert decision.fallback_chain == ("stub",)


# --- rule 6: default ---------------------------------------------------------------------

def test_default_picks_cheapest_non_stub_and_reason_names_the_tier():
    decision = ModelRouter(FULL_REGISTRY).route(RouteRequest())
    assert decision.level == "L1"
    assert decision.provider_name == "ollama-local"
    assert "tier 1" in decision.reason
    assert decision.fallback_chain == ("stub", "cloud-std", "cloud-tool", "premium")


def test_default_without_local_goes_l2_and_breaks_ties_alphabetically():
    router = ModelRouter([STUB, spec("b", "openai-compatible", 2),
                          spec("a", "ollama-cloud", 2)])
    decision = router.route(RouteRequest())
    assert decision.level == "L2"
    assert decision.provider_name == "a"
    assert "tier 2" in decision.reason
    assert decision.fallback_chain == ("stub", "b")


def test_default_low_latency_target_prefers_local_over_alphabetically_first_cloud():
    # same tier, cloud wins A-Z by default; the latency carve-out flips to local
    registry = [spec("a-cloud", "openai-compatible", 2),
                spec("z-local", "ollama", 2)]
    fast = ModelRouter(registry).route(RouteRequest(latency_target_s=2.0))
    assert fast.provider_name == "z-local"
    assert fast.level == "L1"
    assert "latency" in fast.reason

    relaxed = ModelRouter(registry).route(RouteRequest(latency_target_s=30.0))
    assert relaxed.provider_name == "a-cloud"
    none_set = ModelRouter(registry).route(RouteRequest())
    assert none_set.provider_name == "a-cloud"


def test_default_low_latency_without_any_local_still_uses_cloud():
    router = ModelRouter([spec("a", "openai-compatible", 2)])
    decision = router.route(RouteRequest(latency_target_s=0.5))
    assert decision.level == "L2"
    assert decision.provider_name == "a"


def test_default_when_only_the_stub_is_healthy_uses_it_at_l0():
    router = ModelRouter([spec("dead", "ollama", 1, healthy=False), STUB])
    decision = router.route(RouteRequest())
    assert decision.level == "L0"
    assert decision.provider_name == "stub"
    assert "only the stub is healthy" in decision.reason


# --- determinism / isolation -----------------------------------------------------------

def test_same_inputs_twice_yield_identical_decisions():
    router = ModelRouter(FULL_REGISTRY)
    req = RouteRequest(task_text="summarize the run", tool_use_needed=True,
                       estimated_tokens=1200)
    first = router.route(req)
    second = router.route(req)
    assert first == second
    assert dataclasses.asdict(first) == dataclasses.asdict(second)
    for difficulty in ("trivial", "normal", "hard"):
        assert router.route(RouteRequest(difficulty=difficulty)) == \
            router.route(RouteRequest(difficulty=difficulty))


def test_constructor_stores_a_copy_of_the_candidate_list():
    mutable = [STUB, LOCAL, CLOUD_STD]
    router = ModelRouter(mutable)
    del mutable[:]  # caller mutation must not re-route
    decision = router.route(RouteRequest())
    assert decision.provider_name == "ollama-local"
    assert router.candidates == (STUB, LOCAL, CLOUD_STD)


def test_every_rule_skips_unhealthy_and_never_lists_them_in_chains():
    router = ModelRouter([
        spec("dead-stub", "stub", 0, healthy=False),
        spec("dead-premium", "openai-compatible", 3, healthy=False),
        LOCAL, CLOUD_STD,
    ])
    for req in (
        RouteRequest(difficulty="trivial"),
        RouteRequest(difficulty="hard"),
        RouteRequest(tool_use_needed=True),
        RouteRequest(privacy_required=True),
        RouteRequest(),
    ):
        decision = router.route(req)
        assert decision.provider_name in {"ollama-local", "cloud-std", ""}
        assert not set(decision.fallback_chain) & {"dead-stub", "dead-premium"}
