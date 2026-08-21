"""Adaptive model routing (doc 06, "Routing Levels").

ULTRON picks the CHEAPEST LEVEL THAT CAN RELIABLY COMPLETE THE TASK:

    L0 deterministic code      -> free stub / refusal-to-route
    L1 tiny/local model        -> privacy, trivial without a stub, tight latency
    L2 standard model          -> tool-capable cloud, default cloud tier
    L3 reasoning model         -> hard tasks
    L4 specialist orchestration-> NOT emitted here (this router picks one
                                  provider per decision; supervisors compose L4)

Rule -> doc-06 rationale map (evaluated in this exact order):

1. no healthy candidate            -> L0, nobody routed (provider health is a
                                      doc-06 router input; never route to dead)
2. privacy_required                -> local-only pool {"stub","ollama"}: data
                                      must not leave the machine; cheapest
                                      local wins (L0 if that is the stub, else
                                      L1). No local healthy -> L0 refusal.
                                      Fallback chain stays local-only.
3. difficulty "trivial"            -> cheapest level suffices: free stub (L0)
                                      when healthy, else cheapest (L1/L2).
4. difficulty "hard"               -> strongest reliable level: highest
                                      cost_tier healthy (L3), remaining
                                      healthy as fallbacks, strongest first.
5. tool_use_needed                 -> only cloud kinds advertise tool calling
                                      ({"ollama-cloud","openai-compatible"}),
                                      cheapest of them (L2); everything else
                                      by cost ascending behind it.
6. default ("normal")              -> cheapest healthy that is NOT the free
                                      stub (L1/L2 by tier); a latency target
                                      under 5 s prefers local (no network hop).

Determinism: every choice is min/max over the sort key (cost_tier, name) —
lower tier first, then alphabetical name — so identical inputs always yield
identical decisions. No RNG, no clock, no network: pure function of
(candidates, request). The router knows only CandidateSpec facts; real
capability negotiation happens bind-time per doc 03, this just ranks specs.
"""

from __future__ import annotations

from dataclasses import dataclass

KINDS = ("stub", "ollama", "ollama-cloud", "openai-compatible")
LOCAL_KINDS = frozenset({"stub", "ollama"})            # data stays on-machine
TOOL_KINDS = frozenset({"ollama-cloud", "openai-compatible"})
DIFFICULTIES = frozenset({"trivial", "normal", "hard"})
MIN_COST_TIER = 0
MAX_COST_TIER = 4
LOW_LATENCY_S = 5.0  # below this, prefer local (no network round trip)


@dataclass(frozen=True)
class CandidateSpec:
    """What the router may know about a provider: facts only, no handles.

    cost_tier: 0=free stub, 1=local, 2=standard cloud, 3=premium cloud
    (4 reserved). `local` mirrors kind in a sane registry; routing filters by
    kind, which doc 06 treats as the privacy boundary.
    """

    name: str
    kind: str
    cost_tier: int
    local: bool
    healthy: bool
    model: str = ""

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValueError(f"kind {self.kind!r} not in {KINDS}")
        if not (MIN_COST_TIER <= self.cost_tier <= MAX_COST_TIER):
            raise ValueError(
                f"cost_tier {self.cost_tier!r} outside "
                f"{MIN_COST_TIER}..{MAX_COST_TIER}"
            )


@dataclass(frozen=True)
class RouteRequest:
    """Doc-06 "Router Inputs" subset that is knowable before dispatch."""

    task_text: str = ""
    privacy_required: bool = False
    tool_use_needed: bool = False
    difficulty: str = "normal"  # "trivial" | "normal" | "hard"
    latency_target_s: float | None = None
    estimated_tokens: int | None = None

    def __post_init__(self) -> None:
        if self.difficulty not in DIFFICULTIES:
            raise ValueError(
                f"difficulty {self.difficulty!r} not in {sorted(DIFFICULTIES)}"
            )


@dataclass(frozen=True)
class RouteDecision:
    level: str  # "L0".."L4"
    provider_name: str
    reason: str
    fallback_chain: tuple[str, ...] = ()


def _by_cost(spec: CandidateSpec) -> tuple[int, str]:
    """Deterministic sort key: lower cost_tier first, then name A-Z."""
    return (spec.cost_tier, spec.name)


def _by_cost_desc(spec: CandidateSpec) -> tuple[int, str]:
    """Highest cost_tier first, ties still broken A-Z (deterministic)."""
    return (-spec.cost_tier, spec.name)


def _level_of(spec: CandidateSpec) -> str:
    """Doc-06 level of a chosen candidate: stub=L0, local=L1, cloud=L2."""
    if spec.kind == "stub":
        return "L0"
    if spec.kind == "ollama":
        return "L1"
    return "L2"


class ModelRouter:
    """Ranks injected CandidateSpecs into a RouteDecision. Pure and offline."""

    def __init__(self, candidates: list[CandidateSpec]) -> None:
        # Store a copy: later mutation of the caller's list must not re-route.
        self._candidates: tuple[CandidateSpec, ...] = tuple(candidates)

    @property
    def candidates(self) -> tuple[CandidateSpec, ...]:
        return self._candidates

    def route(self, req: RouteRequest) -> RouteDecision:
        """Apply rules 1-6 in order; first match wins (docstring table above).

        fallback_chain always lists the remaining healthy names of the rule's
        own pool, best-first by that rule's preference, so a supervisor can
        walk it on failure without re-deriving the constraint (privacy keeps
        the chain local-only; hard keeps it strongest-first).
        """
        healthy = [c for c in self._candidates if c.healthy]

        # Rule 1: dead registry -> refuse to route (doc 06: provider health).
        if not healthy:
            return RouteDecision("L0", "", "no healthy provider", ())

        # Rule 2: privacy bounds the pool to local kinds before anything else.
        if req.privacy_required:
            return self._route_privacy(healthy)

        # Rule 3: trivial -> cheapest level that can answer at all.
        if req.difficulty == "trivial":
            return self._route_trivial(healthy)

        # Rule 4: hard -> strongest healthy tier (reasoning models live high).
        if req.difficulty == "hard":
            return self._route_hard(healthy)

        # Rule 5: tool use needs a kind that advertises tool calling.
        if req.tool_use_needed:
            return self._route_tools(healthy, req)

        # Rule 6: default economy, with the latency carve-out for local.
        return self._route_default(healthy, req)

    # --- rules ------------------------------------------------------------------

    def _route_privacy(self, healthy: list[CandidateSpec]) -> RouteDecision:
        locals_ = [c for c in healthy if c.kind in LOCAL_KINDS]
        if not locals_:
            return RouteDecision(
                "L0", "", "privacy required but no healthy local provider", ()
            )
        ranked = sorted(locals_, key=_by_cost)
        chosen = ranked[0]
        chain = tuple(c.name for c in ranked[1:])  # never list cloud fallbacks
        return RouteDecision(
            _level_of(chosen), chosen.name,
            f"privacy: cheapest healthy local tier {chosen.cost_tier}", chain,
        )

    def _route_trivial(self, healthy: list[CandidateSpec]) -> RouteDecision:
        ranked = sorted(healthy, key=_by_cost)
        chosen = ranked[0]
        if chosen.kind == "stub":
            reason = "trivial: free stub"
        else:
            reason = (
                f"trivial: cheapest healthy tier {chosen.cost_tier} (no stub)"
            )
        return RouteDecision(
            _level_of(chosen), chosen.name, reason,
            tuple(c.name for c in ranked[1:]),
        )

    def _route_hard(self, healthy: list[CandidateSpec]) -> RouteDecision:
        ranked = sorted(healthy, key=_by_cost_desc)
        chosen = ranked[0]
        return RouteDecision(
            "L3", chosen.name,
            f"hard: highest healthy tier {chosen.cost_tier}",
            tuple(c.name for c in ranked[1:]),
        )

    def _route_tools(
        self, healthy: list[CandidateSpec], req: RouteRequest
    ) -> RouteDecision:
        preferred = [c for c in healthy if c.kind in TOOL_KINDS]
        if preferred:
            ranked = sorted(healthy, key=_by_cost)
            chosen = sorted(preferred, key=_by_cost)[0]
            return RouteDecision(
                "L2", chosen.name,
                f"tool use: cheapest tool-capable tier {chosen.cost_tier}",
                tuple(c.name for c in ranked if c.name != chosen.name),
            )
        # Nothing advertises tool calling: cheapest non-stub, like rule 6.
        return self._route_default(healthy, req, note="no tool-capable provider; ")

    def _route_default(
        self, healthy: list[CandidateSpec], req: RouteRequest, note: str = ""
    ) -> RouteDecision:
        pool = [c for c in healthy if c.kind != "stub"]
        chosen: CandidateSpec | None = None
        latency_note = ""
        if (
            req.latency_target_s is not None
            and req.latency_target_s < LOW_LATENCY_S
        ):
            locals_ = [c for c in pool if c.kind in LOCAL_KINDS]
            if locals_:
                chosen = min(locals_, key=_by_cost)
                latency_note = (
                    f"latency target {req.latency_target_s}s < {LOW_LATENCY_S:g}s: "
                    "local preferred; "
                )
        if chosen is None:
            chosen = min(pool, key=_by_cost) if pool else min(healthy, key=_by_cost)
        reason = (
            f"{note}{latency_note}cheapest healthy tier {chosen.cost_tier}"
            if chosen.kind != "stub"
            else f"{note}only the stub is healthy"
        )
        return RouteDecision(
            _level_of(chosen), chosen.name, reason,
            tuple(c.name for c in sorted(healthy, key=_by_cost)
                  if c.name != chosen.name),
        )


__all__ = [
    "CandidateSpec",
    "RouteDecision",
    "RouteRequest",
    "ModelRouter",
    "KINDS",
    "LOCAL_KINDS",
    "TOOL_KINDS",
    "DIFFICULTIES",
]
