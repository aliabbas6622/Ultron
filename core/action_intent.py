"""Model output never directly causes side effects. It proposes an ActionIntent instead."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from core.ids import new_action_id


@dataclass(frozen=True)
class ActionIntent:
    kind: str  # e.g. "file_write"
    params: dict[str, Any] = field(default_factory=dict)
    action_id: str = field(default_factory=new_action_id)
