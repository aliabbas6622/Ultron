"""PolicyEvaluator v1 contract.

Policy is a brick too: an external agent can plug ULTRON's native policy engine
in front of its own executors, or replace ours with theirs — the guarantee that
matters ("the model never authorizes its own privileged actions", 07) is that
SOME native evaluator sits between proposal and execution, outside any model.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from typing import Protocol, runtime_checkable

from contracts.action import ActionIntent

CONTRACT_ID = "policy_evaluator"
CONTRACT_VERSION = "1.0.0"


class Decision(Enum):
    ALLOW = auto()
    DENY = auto()
    # 07's full vocabulary; V0.1 engines emit only ALLOW/DENY, non-ALLOW always stops the action
    ASK = auto()
    CONSTRAIN = auto()
    SANDBOX = auto()


@dataclass(frozen=True)
class PolicyDecision:
    decision: Decision
    reason: str = ""


@runtime_checkable
class PolicyEvaluator(Protocol):
    """Required: evaluate. Implementations are native code, never model calls."""

    def evaluate(self, intent: ActionIntent) -> PolicyDecision: ...
