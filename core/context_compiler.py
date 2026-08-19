"""Minimal context compiler for V0.1. One pass: project + fit budget. No memory/tool-schema
pruning yet, those need real memory/tool blocks first (see 06_MEMORY_CONTEXT_ROUTING.md)."""

from __future__ import annotations

from dataclasses import dataclass

from contracts.browser import PageProjection


@dataclass(frozen=True)
class CompiledContext:
    prompt: str
    estimated_tokens: int


def compile_context(task: str, page: PageProjection, max_chars: int = 4000) -> CompiledContext:
    """ponytail: char-count token estimate (len // 4), swap for a real tokenizer when
    a model adapter needs accurate budget enforcement rather than a rough ceiling."""
    text = page.text[:max_chars]
    prompt = (
        f"Task: {task}\n\n"
        f"Page URL: {page.url}\n"
        f"Page title: {page.title}\n"
        f"Page content:\n{text}\n"
    )
    return CompiledContext(prompt=prompt, estimated_tokens=len(prompt) // 4)
