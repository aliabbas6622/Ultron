"""PolicyEngine: the only path from ActionIntent to a permitted side effect. LLM cannot self-authorize.
Implements contracts.policy.PolicyEvaluator (structural) so an external agent can adopt this engine,
or ULTRON can adopt an external one — the native-evaluator guarantee (07) is what must hold."""

from __future__ import annotations

import os
from dataclasses import dataclass

from contracts.action import ActionIntent
from contracts.policy import Decision, PolicyDecision

__all__ = ["Decision", "PolicyDecision", "PolicyResult", "PolicyEngine"]


@dataclass(frozen=True)
class PolicyResult(PolicyDecision):
    """Legacy alias; policy decisions are contract types now."""


class PolicyEngine:
    """V0.1: single rule set for file_write. ponytail: no ask/sandbox/constrain modes yet,
    add when a second ActionIntent kind needs finer-grained handling than allow/deny."""

    def __init__(self, allowed_write_dir: str) -> None:
        self._allowed_write_dir = allowed_write_dir

    def evaluate(self, intent: ActionIntent) -> PolicyResult:
        if intent.kind != "file_write":
            return PolicyResult(Decision.DENY, f"unknown action kind: {intent.kind}")

        path = intent.params.get("path", "")
        abs_target = os.path.abspath(path)
        abs_allowed = os.path.abspath(self._allowed_write_dir)
        if os.path.commonpath([abs_target, abs_allowed]) != abs_allowed:
            return PolicyResult(Decision.DENY, f"path outside allowed dir: {path}")

        return PolicyResult(Decision.ALLOW)
