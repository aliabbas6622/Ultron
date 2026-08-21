"""ID generation for runs, traces, and actions. Single source so formats never drift."""

from __future__ import annotations

import uuid


def new_run_id() -> str:
    return f"run_{uuid.uuid4().hex}"


def new_trace_id() -> str:
    return f"trace_{uuid.uuid4().hex}"


def new_action_id() -> str:
    return f"action_{uuid.uuid4().hex}"


def new_memory_id() -> str:
    return f"mem_{uuid.uuid4().hex}"
