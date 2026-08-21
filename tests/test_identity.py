"""Tests for core/identity.py — offline, tmp_path-isolated, deterministic.

Identity is typed state, not a personality prompt (02): what matters here is
(a) stability across restarts (same instance_id on every load), (b) tolerant
persistence (corrupt store regenerates instead of crashing), (c) the exact
mapping onto contracts.fabric.NodeIdentity.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import core.identity
from contracts.fabric import NodeIdentity
from core.identity import IdentityStore, UltronIdentity


def _store(tmp_path: Path) -> IdentityStore:
    return IdentityStore(tmp_path / "identity.json")


# --- load-or-create ---------------------------------------------------------------

def test_load_or_create_creates_and_persists(tmp_path):
    store = _store(tmp_path)
    assert not store.path.exists()
    identity = store.load_or_create()
    assert store.path.is_file()  # persisted immediately
    assert identity.instance_id.startswith("ultron_")
    assert len(identity.instance_id) > len("ultron_")  # a real uuid hex, not a stub
    assert identity.owner_id == "owner"
    assert identity.name == "ULTRON"
    assert identity.trusted_environment is True
    assert identity.capabilities == ()


def test_identity_stable_across_two_loads_and_restarts(tmp_path):
    store = _store(tmp_path)
    first = store.load_or_create()
    second = store.load_or_create()
    assert second == first  # same store, same answer
    restarted = _store(tmp_path).load_or_create()  # fresh process shape
    assert restarted.instance_id == first.instance_id  # identity survives restarts


def test_device_identity_from_platform_node(tmp_path, monkeypatch):
    monkeypatch.setattr(
        core.identity, "platform", SimpleNamespace(node=lambda: "pc-workstation")
    )
    identity = _store(tmp_path).load_or_create()
    assert identity.device_identity == "pc-workstation"
    assert identity.device_identity != ""


# --- update -----------------------------------------------------------------------

def test_update_round_trip_adds_capability_and_sets_name(tmp_path):
    store = _store(tmp_path)
    original = store.load_or_create()
    updated = store.update(capabilities=("gpu", "browser"), name="Ultron Prime")
    assert updated.capabilities == ("gpu", "browser")
    assert updated.name == "Ultron Prime"
    assert updated.instance_id == original.instance_id  # updates never re-mint identity
    assert updated != original  # frozen dataclass, replaced not mutated
    assert original.capabilities == ()  # the old instance is untouched
    reloaded = _store(tmp_path).load_or_create()  # persisted for the next process
    assert reloaded == updated
    assert reloaded.capabilities == ("gpu", "browser")
    assert reloaded.name == "Ultron Prime"


def test_update_rejects_unknown_field(tmp_path):
    store = _store(tmp_path)
    store.load_or_create()
    try:
        store.update(personality="sassy")  # identity is NOT a personality prompt
    except ValueError as exc:
        assert "personality" in str(exc)
    else:
        raise AssertionError("update() must reject unknown fields with ValueError")
    assert _store(tmp_path).load_or_create().name == "ULTRON"  # nothing was persisted


# --- corruption regenerates, never crashes ----------------------------------------

def test_corrupted_json_regenerates_new_instance_id_and_saves(tmp_path):
    store = _store(tmp_path)
    first_id = store.load_or_create().instance_id
    store.path.write_text("{ this is not json", encoding="utf-8")
    regenerated = store.load_or_create()
    assert regenerated.instance_id.startswith("ultron_")
    assert regenerated.instance_id != first_id  # fresh mint, not the old identity
    on_disk = json.loads(store.path.read_text(encoding="utf-8"))
    assert on_disk["instance_id"] == regenerated.instance_id  # regeneration persisted
    assert _store(tmp_path).load_or_create().instance_id == regenerated.instance_id


def test_valid_json_non_dict_regenerates(tmp_path):
    store = _store(tmp_path)
    store.path.write_text(json.dumps(["not", "an", "object"]), encoding="utf-8")
    identity = store.load_or_create()
    assert identity.instance_id.startswith("ultron_")


def test_blank_or_missing_instance_id_regenerates(tmp_path):
    store = _store(tmp_path)
    for payload in ({"instance_id": ""}, {"instance_id": "   "}, {"name": "ULTRON"}):
        store.path.write_text(json.dumps(payload), encoding="utf-8")
        identity = store.load_or_create()
        assert identity.instance_id.startswith("ultron_")
        assert identity.instance_id.strip() == identity.instance_id
    # blank regeneration is also persisted
    assert json.loads(store.path.read_text(encoding="utf-8"))["instance_id"].startswith("ultron_")


def test_load_tolerates_missing_optional_fields(tmp_path):
    store = _store(tmp_path)
    identity = store.load_or_create()
    store.update(capabilities=("gpu",))
    hand_edited = {"instance_id": identity.instance_id, "capabilities": ["voice"]}
    store.path.write_text(json.dumps(hand_edited), encoding="utf-8")
    loaded = store.load_or_create()
    assert loaded.instance_id == identity.instance_id  # kept: it is who this node IS
    assert loaded.capabilities == ("voice",)
    assert loaded.owner_id == "owner"  # defaults fill the gaps


# --- fabric mapping ----------------------------------------------------------------

def test_to_node_identity_field_mapping_with_trust_owner():
    identity = UltronIdentity(
        instance_id="ultron_deadbeef",
        owner_id="owner-42",
        device_identity="pc-workstation",
        capabilities=("gpu", "browser"),
    )
    node = identity.to_node_identity()
    assert isinstance(node, NodeIdentity)
    assert node.instance_id == "ultron_deadbeef"
    assert node.device_identity == "pc-workstation"
    assert node.owner_id == "owner-42"
    assert node.capabilities == ("gpu", "browser")
    assert node.trust == "owner"


# --- ULTRON_HOME resolution ---------------------------------------------------------

def test_default_path_follows_ultron_home(tmp_path, monkeypatch):
    monkeypatch.setenv("ULTRON_HOME", str(tmp_path))
    store = IdentityStore()  # no explicit path -> <ULTRON_HOME>/identity.json
    assert store.path == tmp_path / "identity.json"
    identity = store.load_or_create()
    assert (tmp_path / "identity.json").is_file()
    monkeypatch.delenv("ULTRON_HOME")
    assert IdentityStore().path.name == "identity.json"
    assert IdentityStore().path.parent.name == ".ultron"


def test_explicit_path_wins_over_ultron_home(tmp_path, monkeypatch):
    monkeypatch.setenv("ULTRON_HOME", str(tmp_path / "home"))
    explicit = tmp_path / "elsewhere" / "identity.json"
    store = IdentityStore(explicit)
    store.load_or_create()  # save() mkdirs the parent
    assert explicit.is_file()
    assert not (tmp_path / "home").exists()
