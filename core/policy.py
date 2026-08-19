"""PolicyEngine: the only path from ActionIntent to a permitted side effect. LLM cannot self-authorize."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto

from core.action_intent import ActionIntent


class Decision(Enum):
    ALLOW = auto()
    DENY = auto()


@dataclass(frozen=True)
class PolicyResult:
    decision: Decision
    reason: str = ""


class PolicyEngine:
    """V0.1: single rule set for file_write. ponytail: no ask/sandbox/constrain modes yet,
    add when a second ActionIntent kind needs finer-grained handling than allow/deny."""

    def __init__(self, allowed_write_dir: str) -> None:
        self._allowed_write_dir = allowed_write_dir

    def evaluate(self, intent: ActionIntent) -> PolicyResult:
        if intent.kind != "file_write":
            return PolicyResult(Decision.DENY, f"unknown action kind: {intent.kind}")

        path = intent.params.get("path", "")
        import os

        abs_target = os.path.abspath(path)
        abs_allowed = os.path.abspath(self._allowed_write_dir)
        if os.path.commonpath([abs_target, abs_allowed]) != abs_allowed:
            return PolicyResult(Decision.DENY, f"path outside allowed dir: {path}")

        return PolicyResult(Decision.ALLOW)
