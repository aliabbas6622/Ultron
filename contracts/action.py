"""ActionIntent: the interchange format between ANY agent and ANY tool.

Model output never directly causes privileged effects — the proposing side
(agent, model adapter, scheduler) emits an ActionIntent; the authorizing side
(policy) evaluates it; a ToolProvider brick executes it. Because this type
lives in contracts/ with zero dependencies, any external agent can emit
intents our tools will execute, and our agents can emit intents any external
executor will run.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any


def new_action_id() -> str:
    return f"action_{uuid.uuid4().hex}"


@dataclass(frozen=True)
class ActionIntent:
    kind: str  # e.g. "file_write" — must match a ToolDescriptor.kind the executor advertised
    params: dict[str, Any] = field(default_factory=dict)
    action_id: str = field(default_factory=new_action_id)  # also the idempotency key for retries
