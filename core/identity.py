"""ULTRON identity: typed state, not a personality prompt (02_ARCHITECTURE.md).

`UltronIdentity` is who this node IS — an `instance_id` that must survive
restarts, plus the owner, device, and profile REFERENCES (not inline profile
data) the runtime resolves elsewhere. Identity carries no authority by itself;
on the fabric it converts to `contracts.fabric.NodeIdentity` (identity is
data, not permission — only a PairingGrant authorizes anything).

State:  <ULTRON_HOME>/identity.json   (load-or-create; corrupt -> regenerate,
        never crash — regeneration changes identity, which is acceptable for
        a corrupted store at V0.x)

IdentityStore is pure composition: stdlib JSON persistence only, no bricks.
ULTRON_HOME resolution mirrors providers/registry.py (duplicated here
because core never imports providers).
"""

from __future__ import annotations

import dataclasses
import json
import os
import platform
import uuid
from pathlib import Path

from contracts.fabric import NodeIdentity

IDENTITY_FILENAME = "identity.json"


def ultron_home() -> Path:
    return Path(os.environ.get("ULTRON_HOME", os.path.join(os.path.expanduser("~"), ".ultron")))


def identity_path() -> Path:
    return ultron_home() / IDENTITY_FILENAME


def new_instance_id() -> str:
    """Stable-across-restarts ids are minted once, never re-derived: the uuid
    is persisted, and later loads always return the SAME instance_id."""
    return f"ultron_{uuid.uuid4().hex}"


@dataclasses.dataclass(frozen=True)
class UltronIdentity:
    """Typed identity state (02: `UltronIdentity` is not a personality prompt).

    `behavior_profile_ref` / `policy_profile_ref` are REFERENCES (a name, a
    path, a hash) — the runtime resolves them against profile stores; inline
    profile data would turn identity into a prompt.
    """

    instance_id: str            # stable across restarts ("ultron_" + uuid hex)
    owner_id: str = "owner"     # single-owner V0.x
    name: str = "ULTRON"
    behavior_profile_ref: str = ""   # reference, not inline data
    policy_profile_ref: str = ""
    device_identity: str = ""   # platform.node() at creation
    trusted_environment: bool = True
    capabilities: tuple[str, ...] = ()

    def to_node_identity(self) -> NodeIdentity:
        """This identity as a fabric node. Trust is "owner" because V0.x is
        single-node and the owner is physically at this device; the node
        record itself grants nothing (07 containment invariant)."""
        return NodeIdentity(
            instance_id=self.instance_id,
            device_identity=self.device_identity,
            owner_id=self.owner_id,
            capabilities=self.capabilities,
            trust="owner",
        )

    def to_dict(self) -> dict:
        data = dataclasses.asdict(self)
        data["capabilities"] = list(self.capabilities)  # tuples are not JSON
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "UltronIdentity":
        """Tolerant parse: identity must load, not crash — unknown keys are
        ignored, missing optional fields fall back to defaults."""
        capabilities = data.get("capabilities") or ()
        if not isinstance(capabilities, (list, tuple)):
            capabilities = ()
        return cls(
            instance_id=str(data["instance_id"]),
            owner_id=str(data.get("owner_id") or "owner"),
            name=str(data.get("name") or "ULTRON"),
            behavior_profile_ref=str(data.get("behavior_profile_ref") or ""),
            policy_profile_ref=str(data.get("policy_profile_ref") or ""),
            device_identity=str(data.get("device_identity") or ""),
            trusted_environment=bool(data.get("trusted_environment", True)),
            capabilities=tuple(str(c) for c in capabilities),
        )


class IdentityStore:
    """Persisted at <ULTRON_HOME>/identity.json. Load-or-create semantics:
    the first call creates a stable identity; later calls always return the
    SAME instance_id — identity must survive restarts, it is who this node
    IS. A missing/blank instance_id or a corrupted file (bad JSON, not a
    dict) regenerates a fresh identity instead of crashing."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = Path(path) if path is not None else identity_path()

    @property
    def path(self) -> Path:
        return self._path

    def load_or_create(self) -> UltronIdentity:
        if not self._path.is_file():
            return self._create()
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return self._create()  # unreadable/bad JSON -> regenerate, never crash
        if not isinstance(raw, dict):
            return self._create()  # valid JSON but not an object -> regenerate
        instance_id = raw.get("instance_id")
        if not isinstance(instance_id, str) or not instance_id.strip():
            return self._create()  # blank/missing instance_id is not an identity
        return UltronIdentity.from_dict(raw)

    def save(self, identity: UltronIdentity) -> None:
        """Atomic-ish write: temp sibling + os.replace, so a crash mid-write
        never truncates the identity (a half-written identity.json would
        force exactly the regeneration we want to avoid)."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_name(self._path.name + ".tmp")
        tmp.write_text(
            json.dumps(identity.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        os.replace(tmp, self._path)

    def update(self, **fields: object) -> UltronIdentity:
        """Immutable update: `dataclasses.replace` + immediate save. Unknown
        field names raise ValueError (typos must not silently no-op)."""
        valid = {f.name for f in dataclasses.fields(UltronIdentity)}
        unknown = sorted(set(fields) - valid)
        if unknown:
            raise ValueError(
                f"unknown identity field(s) {unknown}; expected one of {sorted(valid)}"
            )
        current = self.load_or_create()
        updated = dataclasses.replace(current, **fields)
        self.save(updated)
        return updated

    def _create(self) -> UltronIdentity:
        identity = UltronIdentity(
            instance_id=new_instance_id(),
            device_identity=platform.node(),
        )
        self.save(identity)
        return identity


__all__ = [
    "IDENTITY_FILENAME",
    "IdentityStore",
    "UltronIdentity",
    "identity_path",
    "new_instance_id",
    "ultron_home",
]
