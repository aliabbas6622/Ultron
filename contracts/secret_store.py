"""SecretStore contract (04_TECH_STACK: "Secrets through a SecretStore contract").

Providers reference secrets BY NAME in config files; the raw values live only
in a SecretStore implementation. Config files stay shareable/committable,
structured logs never see key material.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

CONTRACT_ID = "secret_store"
CONTRACT_VERSION = "1.0.0"


@runtime_checkable
class SecretStore(Protocol):
    """Required: get, set, delete, list. Implementations must persist with
    owner-only file permissions where the platform allows."""

    def get(self, name: str) -> str | None: ...

    def set(self, name: str, value: str) -> None: ...

    def delete(self, name: str) -> None: ...

    def list(self) -> list[str]: ...
