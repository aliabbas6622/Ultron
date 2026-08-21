"""Minimal context compiler for V0.1. One pass: project + fit budget + count.
No memory/tool-schema pruning yet, those need real memory/tool blocks first
(see 06_MEMORY_CONTEXT_ROUTING.md). The prompt frame comes from a versioned
InstructionBundle (core/instructions.py), never an inline string."""

from __future__ import annotations

from dataclasses import dataclass, field

from contracts.browser import PageProjection
from core.instructions import PAGE_FRAME_TEMPLATE


@dataclass(frozen=True)
class CompiledContext:
    prompt: str
    estimated_tokens: int
    breakdown: dict[str, int] = field(default_factory=dict)  # per-section token estimates
    truncated: bool = False


def compile_context(task: str, page: PageProjection, max_chars: int = 4000) -> CompiledContext:
    """ponytail: char-count token estimate (len // 4), swap for a real tokenizer when
    a model adapter needs accurate budget enforcement rather than a rough ceiling."""
    text = page.text[:max_chars]
    prompt = PAGE_FRAME_TEMPLATE.render(task=task, url=page.url, title=page.title, text=text)
    breakdown = {k: len(v) // 4 for k, v in (("task", task), ("url", page.url), ("title", page.title), ("text", text))}
    return CompiledContext(
        prompt=prompt,
        estimated_tokens=len(prompt) // 4,
        breakdown=breakdown,
        truncated=len(page.text) > max_chars,
    )
