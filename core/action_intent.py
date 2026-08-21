"""ActionIntent now lives in contracts/action.py (it is the interchange format
between any agent and any tool, so it must not belong to a runtime). Re-exported
here for existing importers; new code should import from contracts.action."""

from __future__ import annotations

from contracts.action import ActionIntent, new_action_id

__all__ = ["ActionIntent", "new_action_id"]
