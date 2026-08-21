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
    """V0.1 rule set: file_write (path-gated side effect) + web_fetch (read-only,
    scheme-gated). ponytail: no ask/sandbox/constrain modes yet, add when a tool
    needs finer-grained handling than allow/deny."""

    def __init__(self, allowed_write_dir: str) -> None:
        self._allowed_write_dir = allowed_write_dir

    def evaluate(self, intent: ActionIntent) -> PolicyResult:
        if intent.kind == "file_write":
            path = intent.params.get("path", "")
            abs_allowed = os.path.abspath(self._allowed_write_dir)
            # relative paths mean "inside the allowed dir" — resolve there, so an
            # agent proposing "notes.txt" is contained, not CWD-dependent
            abs_target = os.path.abspath(path if os.path.isabs(path) else os.path.join(abs_allowed, path))
            if os.path.commonpath([abs_target, abs_allowed]) != abs_allowed:
                return PolicyResult(Decision.DENY, f"path outside allowed dir: {path}")
            return PolicyResult(Decision.ALLOW)

        if intent.kind == "web_fetch":
            url = str(intent.params.get("url", ""))
            if not (url.startswith("http://") or url.startswith("https://")):
                return PolicyResult(Decision.DENY, f"unsupported URL scheme: {url!r}")
            return PolicyResult(Decision.ALLOW)

        return PolicyResult(Decision.DENY, f"unknown action kind: {intent.kind}")
