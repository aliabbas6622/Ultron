"""Brick independence primitive.

Every block contract in this package types its calls against CallContext, not
against any runtime's context class — so a block can be implemented or driven
by ANY host (ULTRON core, another agent framework, a plain script). The host
just needs an object with check_alive(); ULTRON's core.RunContext satisfies
this structurally without either side importing the other.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class CallContext(Protocol):
    """What a block may demand from its caller: cooperative cancellation,
    deadline, and budget checks. Blocks call check_alive() at their internal
    boundaries; the host decides what "alive" means."""

    def check_alive(self) -> None: ...
