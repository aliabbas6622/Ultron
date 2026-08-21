"""Typed runtime events. No giant synchronous loop owns behavior; components emit these."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Event:
    run_id: str
    trace_id: str
    at: float = field(default_factory=time.time)


@dataclass(frozen=True)
class UserInput(Event):
    text: str = ""


@dataclass(frozen=True)
class ModelStarted(Event):
    block_id: str = ""


@dataclass(frozen=True)
class ModelDelta(Event):
    text: str = ""


@dataclass(frozen=True)
class ModelCompleted(Event):
    block_id: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0


@dataclass(frozen=True)
class ToolRequested(Event):
    action_id: str = ""
    tool_name: str = ""
    args: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ToolCompleted(Event):
    action_id: str = ""
    ok: bool = False
    result: Any = None


@dataclass(frozen=True)
class PolicyDenied(Event):
    action_id: str = ""
    reason: str = ""


@dataclass(frozen=True)
class VerificationFailed(Event):
    action_id: str = ""
    reason: str = ""


@dataclass(frozen=True)
class ComponentDegraded(Event):
    block_id: str = ""
    reason: str = ""


@dataclass(frozen=True)
class ComponentRecovered(Event):
    block_id: str = ""


@dataclass(frozen=True)
class RunCancelled(Event):
    reason: str = ""
