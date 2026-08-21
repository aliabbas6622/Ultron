"""Shared HealthStatus for every block contract (03: "health semantics")."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class HealthStatus:
    healthy: bool
    detail: str = ""
