"""Minimal synchronous event bus. Components publish typed Events; nothing owns global control flow."""

from __future__ import annotations

from collections import defaultdict
from typing import Callable

from core.events import Event

Handler = Callable[[Event], None]


class EventBus:
    def __init__(self) -> None:
        self._handlers: dict[type, list[Handler]] = defaultdict(list)

    def subscribe(self, event_type: type[Event], handler: Handler) -> None:
        self._handlers[event_type].append(handler)

    def publish(self, event: Event) -> None:
        for handler in self._handlers[type(event)]:
            handler(event)
