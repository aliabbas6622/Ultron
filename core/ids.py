"""ID generation for runs, traces, and actions. Single source so formats never drift.
action ids moved to contracts/action.py so intents are self-contained bricks."""

from __future__ import annotations

import uuid

from contracts.action import new_action_id


def new_run_id() -> str:
    return f"run_{uuid.uuid4().hex}"


def new_trace_id() -> str:
    return f"trace_{uuid.uuid4().hex}"


__all__ = ["new_run_id", "new_trace_id", "new_action_id"]
