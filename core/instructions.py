"""Versioned instruction bundles (05_AGENT_INSTRUCTIONS.md "Prompt Management").

Prompt strings do not live inline in runtime/compiler code. Each bundle carries
id/version/content_hash/role/cache_class/token_count, and every run records the
bundle versions it used (RunContext.metadata["instructions"]) so a stored run can
be reproduced against the exact instruction set that produced it.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass


@dataclass(frozen=True)
class InstructionBundle:
    id: str
    version: str
    role: str  # "system" | "task" | ...
    cache_class: str  # "static" (stable across runs -> prompt-cacheable) | "per_run"
    content: str

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.content.encode("utf-8")).hexdigest()[:16]

    @property
    def token_count(self) -> int:
        # same char//4 estimate the context compiler uses; swap with the compiler
        # when a real tokenizer lands so both stay identical
        return len(self.content) // 4

    def render(self, **fields: str) -> str:
        """Format the bundle content. A bundle used with render() is a template;
        the *rendered* string must not be cached as this bundle's content."""
        return self.content.format(**fields)

    def record_id(self) -> str:
        return f"{self.id}@{self.version}+{self.content_hash}"


# --- V0.1 slice bundles -----------------------------------------------------

# The task instruction for the visit-summarize-save slice. Static, cacheable.
TASK_VISIT_SUMMARIZE = InstructionBundle(
    id="ultron.slice.visit_summarize.task",
    version="1.0.0",
    role="task",
    cache_class="static",
    content="Determine in one sentence what the page is for.",
)

# Frame that arranges the compiled context. Static template; the dynamic
# regions (task, page fields) fill the {placeholders}.
PAGE_FRAME_TEMPLATE = InstructionBundle(
    id="ultron.slice.page_frame",
    version="1.0.0",
    role="system",
    cache_class="static",
    content="Task: {task}\n\nPage URL: {url}\nPage title: {title}\nPage content:\n{text}\n",
)

REGISTRY: dict[str, InstructionBundle] = {
    b.id: b for b in (TASK_VISIT_SUMMARIZE, PAGE_FRAME_TEMPLATE)
}
