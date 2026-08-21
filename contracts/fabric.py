"""Fabric node contract (scaffold) — pure types for the multi-device shape.

01_VISION.md sketches ULTRON as a logical organism spread across explicitly
paired nodes (a phone contributing camera/voice, a workstation contributing
GPU/browser/files). This module fixes the CONTRACT TYPES for that fabric and
nothing else: no networking, no discovery protocol, no auto-deploy. A future
fabric implementation must satisfy these types — and the types exist to make
one rule impossible to implement away, the containment invariant of
07_SECURITY_AND_CONTAINMENT.md (verbatim in CONTAINMENT_INVARIANT below):

- every authority-bearing operation flows through a PairingGrant;
- a PairingGrant is constructible ONLY with an explicit per-device human
  approval token — never a model output;
- a CapabilityAdvertisement is a dumb record: it carries what a node OFFERS,
  never what a node may DO. Discovery is not admission; compatibility is not
  authority;
- revocation removes hosting rights.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from contracts.errors import ContractViolationError

CONTRACT_ID = "fabric_node"
CONTRACT_VERSION = "0.1.0"

# 07, verbatim — hosts should embed this in their docs/logs.
CONTAINMENT_INVARIANT: str = (
    "ULTRON may never install, activate, replicate, or migrate itself onto a "
    "new host without explicit per-device human pairing and approval. "
    "Discovery is not admission. Compatibility is not authority. "
    "The model cannot change this rule."
)

TRUST_LEVELS = ("owner", "paired", "untrusted")


@dataclass(frozen=True)
class NodeIdentity:
    """Stable identity of one device in the fabric.

    `instance_id` is stable per node; `device_identity` is the human-facing
    role ("pc-workstation", "phone-personal"); `trust` is the node's trust
    class (TRUST_LEVELS). Identity is data, not authority: knowing a node
    (or its capabilities) grants nothing — only PairingGrant does.
    """

    instance_id: str  # stable per node
    device_identity: str  # e.g. "pc-workstation", "phone-personal"
    owner_id: str
    capabilities: tuple[str, ...] = ()  # e.g. ("gpu", "browser", "voice", "files")
    trust: str = "owner"  # owner | paired | untrusted


@dataclass(frozen=True)
class CapabilityAdvertisement:
    """What a node OFFERS. Advertising is not authorization — hosts must still
    pair before any work or capability migration happens (07).

    A dumb record by design: no authority can be derived from it. `resources`
    is free-form descriptive data such as {"gpu": True, "ram_gb": 32,
    "battery_pct": 80}; `heartbeat_at` is a liveness timestamp (0.0 = never
    seen). Seeing an advertisement MUST NOT admit the node anywhere.
    """

    node: NodeIdentity
    resources: dict[str, Any] = field(default_factory=dict)
    heartbeat_at: float = 0.0


@dataclass(frozen=True)
class PairingRequest:
    """A proposal to pair two nodes. A proposal is not a grant: it may carry
    human_approval_token=None until a human acts on it, and only then can a
    PairingGrant be constructed from it."""

    from_node: NodeIdentity
    to_node: NodeIdentity
    # REQUIRED for any grant: per-device explicit human action (07). This is
    # an artifact of a human approval flow — not a model output, ever.
    human_approval_token: str | None = None
    requested_capabilities: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.from_node.trust not in TRUST_LEVELS:
            raise ValueError(
                f"invalid trust {self.from_node.trust!r} on from_node "
                f"{self.from_node.device_identity!r}; expected one of {TRUST_LEVELS}"
            )
        if self.to_node.trust not in TRUST_LEVELS:
            raise ValueError(
                f"invalid trust {self.to_node.trust!r} on to_node "
                f"{self.to_node.device_identity!r}; expected one of {TRUST_LEVELS}"
            )
        if self.from_node.instance_id == self.to_node.instance_id:
            raise ValueError("self-pairing is not a pairing")


@dataclass(frozen=True)
class PairingGrant:
    """The ONLY authority-bearing record in the fabric: proof that a human
    approved this specific pairing on this specific device (07)."""

    request: PairingRequest
    granted_at: float
    scopes: tuple[str, ...] = ()  # least-privilege capability scopes
    revocable: bool = True  # 07: revocation removes hosting rights

    def __post_init__(self) -> None:
        token = self.request.human_approval_token
        if token is None or token == "":
            raise ContractViolationError(
                "pairing requires explicit per-device human approval "
                "(07 containment invariant)"
            )


@runtime_checkable
class FabricHost(Protocol):
    """Scaffold protocol for a future fabric implementation. A host may only
    accept work from paired+granted nodes; it MUST refuse unpaired nodes and
    never auto-admit on discovery (CONTAINMENT_INVARIANT, 07)."""

    def advertise(self) -> CapabilityAdvertisement: ...

    def evaluate_pairing(self, request: PairingRequest) -> PairingGrant:
        """Must raise ContractViolationError when the request carries no
        human_approval_token — an unapproved pairing cannot return a grant."""
        ...


class PairingLedger:
    """In-memory record of grants; the shape a persistent ledger would keep.

    Entries are keyed by the ADMITTED node (grant.request.to_node.instance_id):
    `is_paired` answers "does this node currently hold hosting rights on this
    fabric". An absent entry is the safe default — an unpaired node has no
    scopes and no rights, which is exactly the pre-pairing state 07 demands.
    """

    def __init__(self) -> None:
        self._grants: dict[str, PairingGrant] = {}

    def grant(self, grant: PairingGrant) -> None:
        """Record a grant; the admitted node gains hosting rights."""
        self._grants[grant.request.to_node.instance_id] = grant

    def is_paired(self, instance_id: str) -> bool:
        """True only if a (non-revoked) grant exists for this node."""
        return instance_id in self._grants

    def revoke(self, instance_id: str) -> None:
        """Remove hosting rights (07: revocation removes hosting rights).
        Revoking an unknown node is a no-op — the safe state is unpaired."""
        self._grants.pop(instance_id, None)

    def scopes(self, instance_id: str) -> tuple[str, ...]:
        """Least-privilege scopes of the node's current grant; () when not
        paired."""
        grant = self._grants.get(instance_id)
        return () if grant is None else grant.scopes
