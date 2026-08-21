"""Context compiler unit checks: the model sees exactly the frame sections —
projected page content only, never a raw dump, never tool schemas."""

from __future__ import annotations

from contracts.browser import PageProjection
from core.context_compiler import compile_context
from core.instructions import PAGE_FRAME_TEMPLATE, TASK_VISIT_SUMMARIZE


PAGE = PageProjection(
    url="https://example.com", title="Example Domain", text="word " * 2000
)


def test_compiled_prompt_is_exactly_the_frame_sections():
    ctx = compile_context(task=TASK_VISIT_SUMMARIZE.content, page=PAGE)
    sections = ctx.prompt.splitlines()
    assert sections[0] == f"Task: {TASK_VISIT_SUMMARIZE.content}"
    assert sections[2] == "Page URL: https://example.com"
    assert sections[3] == "Page title: Example Domain"
    assert sections[4] == "Page content:"
    # no tool schemas, no system chatter, nothing beyond the frame
    assert "schema" not in ctx.prompt.lower() and "tool" not in ctx.prompt.lower()


def test_truncation_and_breakdown():
    ctx = compile_context(task=TASK_VISIT_SUMMARIZE.content, page=PAGE, max_chars=500)
    assert ctx.truncated is True
    assert len(ctx.prompt) < 800
    assert ctx.breakdown["text"] == 500 // 4
    assert ctx.breakdown["task"] == TASK_VISIT_SUMMARIZE.token_count
    assert ctx.estimated_tokens == len(ctx.prompt) // 4

    small = compile_context(task=TASK_VISIT_SUMMARIZE.content, page=PageProjection(url="u", title="t", text="short"))
    assert small.truncated is False
