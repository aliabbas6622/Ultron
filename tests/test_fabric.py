"""Fabric scaffold contract tests — pure types, offline (07 containment).

The suite proves the containment invariant is load-bearing IN THE TYPES, not
in some future implementation's good behavior:

- a PairingGrant without explicit human approval cannot even be constructed;
- discovery (CapabilityAdvertisement) confers no authority — only a grant does;
- the ledger defaults to "unpaired" and revocation removes hosting rights;
- a minimal FabricHost conforms to the protocol and inherits the same rules.
"""

from __future__ import annotations

import pytest

from contracts.errors import ContractViolationError
from contracts.fabric import (
    CONTAINMENT_INVARIANT,
    CONTRACT_ID,
    CONTRACT_VERSION,
    TRUST_LEVELS,
    CapabilityAdvertisement,
    FabricHost,
    NodeIdentity,
    PairingGrant,
    PairingLedger,
    PairingRequest,
)

WORKSTATION = NodeIdentity(
    instance_id="node-a",
    device_identity="pc-workstation",
    owner_id="owner-1",
    capabilities=("gpu", "browser", "files"),
    trust="owner",
)
PHONE = NodeIdentity(
    instance_id="node-b",
    device_identity="phone-personal",
    owner_id="owner-1",
    capabilities=("voice", "camera", "gps"),
    trust="paired",
)


class FakeHost:
    """Smallest FabricHost: pairing evaluation IS PairingGrant construction —
    so an unapproved request cannot produce a grant, only a violation."""

    def __init__(self, node: NodeIdentity) -> None:
        self.node = node
        self.ledger = PairingLedger()

    def advertise(self) -> CapabilityAdvertisement:
        return CapabilityAdvertisement(
            node=self.node, resources={"gpu": True, "ram_gb": 32}, heartbeat_at=1.0
        )

    def evaluate_pairing(self, request: PairingRequest) -> PairingGrant:
        grant = PairingGrant(  # raises without a human_approval_token
            request=request, granted_at=2.0, scopes=request.requested_capabilities
        )
        self.ledger.grant(grant)
        return grant


# --- module shape -----------------------------------------------------------


def test_contract_id_and_version():
    assert CONTRACT_ID == "fabric_node"
    assert CONTRACT_VERSION == "0.1.0"


def test_containment_invariant_text_is_verbatim():
    assert "never install, activate, replicate, or migrate itself" in CONTAINMENT_INVARIANT
    assert "explicit per-device human pairing and approval" in CONTAINMENT_INVARIANT
    assert "Discovery is not admission" in CONTAINMENT_INVARIANT
    assert "Compatibility is not authority" in CONTAINMENT_INVARIANT
    assert "The model cannot change this rule" in CONTAINMENT_INVARIANT


def test_trust_levels_constant():
    assert TRUST_LEVELS == ("owner", "paired", "untrusted")


# --- PairingRequest validation ----------------------------------------------


def test_request_requires_valid_trust_on_both_nodes():
    rogue = NodeIdentity(instance_id="node-x", device_identity="unknown-box",
                         owner_id="owner-1", trust="root")
    with pytest.raises(ValueError, match="invalid trust"):
        PairingRequest(from_node=rogue, to_node=WORKSTATION)
    with pytest.raises(ValueError, match="invalid trust"):
        PairingRequest(from_node=WORKSTATION, to_node=rogue)


def test_request_rejects_self_pairing():
    with pytest.raises(ValueError, match="self-pairing is not a pairing"):
        PairingRequest(from_node=WORKSTATION, to_node=WORKSTATION)


def test_request_without_token_is_still_just_a_request():
    # a pending request carries no authority and must be constructible
    request = PairingRequest(from_node=WORKSTATION, to_node=PHONE)
    assert request.human_approval_token is None


# --- PairingGrant: the human-approval gate ----------------------------------


def test_grant_without_human_token_is_unconstructible():
    request = PairingRequest(from_node=WORKSTATION, to_node=PHONE)  # no token
    with pytest.raises(ContractViolationError, match="explicit per-device human approval"):
        PairingGrant(request=request, granted_at=1.0)


def test_grant_with_empty_human_token_is_unconstructible():
    request = PairingRequest(from_node=WORKSTATION, to_node=PHONE,
                             human_approval_token="")
    with pytest.raises(ContractViolationError, match="07 containment invariant"):
        PairingGrant(request=request, granted_at=1.0)


def test_grant_with_human_token_succeeds():
    request = PairingRequest(from_node=WORKSTATION, to_node=PHONE,
                             human_approval_token="approve:node-b:2026-08-22",
                             requested_capabilities=("voice",))
    grant = PairingGrant(request=request, granted_at=1.0, scopes=("voice",))
    assert grant.request is request
    assert grant.granted_at == 1.0
    assert grant.scopes == ("voice",)
    assert grant.revocable is True  # 07: hosting rights are revocable by default


# --- PairingLedger -----------------------------------------------------------


def test_ledger_defaults_to_unpaired():
    ledger = PairingLedger()
    assert ledger.is_paired(PHONE.instance_id) is False
    assert ledger.scopes(PHONE.instance_id) == ()


def test_ledger_grant_tracks_the_admitted_node():
    ledger = PairingLedger()
    ledger.grant(PairingGrant(
        request=PairingRequest(from_node=WORKSTATION, to_node=PHONE,
                               human_approval_token="approve:node-b:1"),
        granted_at=1.0,
        scopes=("voice", "camera"),
    ))
    assert ledger.is_paired(PHONE.instance_id) is True  # to_node is admitted
    assert ledger.is_paired(WORKSTATION.instance_id) is False
    assert ledger.scopes(PHONE.instance_id) == ("voice", "camera")


def test_ledger_revoke_removes_hosting_rights():
    ledger = PairingLedger()
    ledger.grant(PairingGrant(
        request=PairingRequest(from_node=WORKSTATION, to_node=PHONE,
                               human_approval_token="approve:node-b:1"),
        granted_at=1.0,
        scopes=("voice",),
    ))
    ledger.revoke(PHONE.instance_id)
    assert ledger.is_paired(PHONE.instance_id) is False
    assert ledger.scopes(PHONE.instance_id) == ()
    ledger.revoke(PHONE.instance_id)  # revoking an unpaired node is a safe no-op


# --- discovery is not admission ----------------------------------------------


def test_advertisement_is_a_dumb_record_with_no_authority():
    # a node can advertise loudly on the wire; the ledger does not care
    ad = CapabilityAdvertisement(node=PHONE,
                                 resources={"battery_pct": 80, "voice": True},
                                 heartbeat_at=99.0)
    ledger = PairingLedger()
    assert ad.node is PHONE  # the record just describes
    assert ledger.is_paired(ad.node.instance_id) is False  # ...and admits nobody
    assert ledger.scopes(ad.node.instance_id) == ()
    # only an explicit human-approved grant changes that
    ledger.grant(PairingGrant(
        request=PairingRequest(from_node=WORKSTATION, to_node=ad.node,
                               human_approval_token="approve:node-b:1"),
        granted_at=100.0,
        scopes=("voice",),
    ))
    assert ledger.is_paired(ad.node.instance_id) is True


# --- FabricHost protocol ------------------------------------------------------


def test_fake_host_conforms_to_fabric_host_protocol():
    host = FakeHost(WORKSTATION)
    assert isinstance(host, FabricHost)
    assert not isinstance(object(), FabricHost)


def test_fake_host_advertise_returns_dumb_record():
    host = FakeHost(WORKSTATION)
    ad = host.advertise()
    assert isinstance(ad, CapabilityAdvertisement)
    assert ad.node is WORKSTATION
    assert ad.resources == {"gpu": True, "ram_gb": 32}


def test_fake_host_refuses_pairing_without_human_approval():
    host = FakeHost(WORKSTATION)
    request = PairingRequest(from_node=PHONE, to_node=WORKSTATION)  # no token
    with pytest.raises(ContractViolationError):
        host.evaluate_pairing(request)
    assert host.ledger.is_paired(WORKSTATION.instance_id) is False


def test_fake_host_grants_pairing_with_human_approval():
    host = FakeHost(WORKSTATION)
    request = PairingRequest(from_node=PHONE, to_node=WORKSTATION,
                             human_approval_token="approve:node-a:1",
                             requested_capabilities=("files",))
    grant = host.evaluate_pairing(request)
    assert isinstance(grant, PairingGrant)
    assert grant.scopes == ("files",)  # least-privilege scopes carry through
    assert host.ledger.is_paired(WORKSTATION.instance_id) is True
    assert host.ledger.scopes(WORKSTATION.instance_id) == ("files",)
