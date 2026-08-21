"""Context compiler unit checks: the model sees exactly the frame sections —
projected page content only, never a raw dump, never tool schemas.
v2 additions: dedupe, trust labels, artifact offload, token budget — all
keyword-activated, with the default path staying byte-identical to v1."""

from __future__ import annotations

from contracts.browser import PageProjection
from core.artifacts import ArtifactStore, artifact_id_for
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


# --- v1 byte-stability (frozen eval replay hashes depend on this) ---------------


def test_default_path_is_byte_identical_to_the_v1_render():
    # dupe-free input, no keyword args: exactly the frame render v1 produced
    ctx = compile_context(task=TASK_VISIT_SUMMARIZE.content, page=PAGE)
    expected = PAGE_FRAME_TEMPLATE.render(
        task=TASK_VISIT_SUMMARIZE.content,
        url=PAGE.url,
        title=PAGE.title,
        text=PAGE.text[:4000],
    )
    assert ctx.prompt == expected
    assert ctx.deduped is False and ctx.artifact_refs == () and ctx.budget_fits is True
    # dedupe=True (default) changes nothing for dupe-free text
    assert ctx.prompt == compile_context(
        task=TASK_VISIT_SUMMARIZE.content, page=PAGE, dedupe=False
    ).prompt


# --- dedupe --------------------------------------------------------------------


def test_dedupe_collapses_blank_and_repeated_lines_and_flags_it():
    noisy = "head\n\n\n\n\nline A\nline A\nline A\nmiddle\n\n \ntail"
    ctx = compile_context(task=TASK_VISIT_SUMMARIZE.content, page=PageProjection(url="u", title="t", text=noisy))
    assert ctx.deduped is True
    assert ctx.prompt.endswith("Page content:\nhead\n\nline A\nmiddle\n\ntail\n")
    assert "line A\nline A" not in ctx.prompt
    assert "\n\n\n" not in ctx.prompt
    # dupe-free text is untouched and unflagged
    clean = compile_context(
        task=TASK_VISIT_SUMMARIZE.content, page=PageProjection(url="u", title="t", text="one\ntwo\nthree")
    )
    assert clean.deduped is False
    assert clean.prompt.endswith("Page content:\none\ntwo\nthree\n")


# --- trust labels ---------------------------------------------------------------


def test_trust_labels_mark_page_untrusted_and_task_owner():
    ctx = compile_context(task=TASK_VISIT_SUMMARIZE.content, page=PAGE)
    assert ctx.trust_labels == {"page": "untrusted", "task": "owner"}


# --- artifact offload -----------------------------------------------------------


def test_over_threshold_text_offloads_to_an_artifact_reference(tmp_path):
    store = ArtifactStore(dir=tmp_path)
    long_text = "unique paragraph words " * 500  # 11k chars > default 6000 threshold
    page = PageProjection(url="https://example.com", title="Example Domain", text=long_text)
    ctx = compile_context(task=TASK_VISIT_SUMMARIZE.content, page=page, artifact_store=store)

    (ref,) = ctx.artifact_refs
    assert ref.artifact_id == artifact_id_for(long_text)  # content-derived, deterministic
    expected_line = f"\n[artifact {ref.artifact_id} {ref.size_bytes} bytes full text]"
    assert ref.size_bytes == len(long_text.encode("utf-8"))
    assert ctx.prompt == PAGE_FRAME_TEMPLATE.render(
        task=TASK_VISIT_SUMMARIZE.content,
        url=page.url,
        title=page.title,
        text=long_text[:4000] + expected_line,  # truncated projection + reference line
    )
    assert ctx.truncated is True
    # the full bulk stays recoverable outside the prompt
    assert store.load(ref.artifact_id).decode("utf-8") == long_text


def test_under_or_at_threshold_text_stores_no_artifact(tmp_path):
    store = ArtifactStore(dir=tmp_path)
    page = PageProjection(url="u", title="t", text="small text")
    ctx = compile_context(task=TASK_VISIT_SUMMARIZE.content, page=page, artifact_store=store)
    assert ctx.artifact_refs == ()
    assert "[artifact" not in ctx.prompt
    assert store.list_ids() == []
    # prompt unchanged from today's behavior for this input
    assert ctx.prompt == compile_context(task=TASK_VISIT_SUMMARIZE.content, page=page).prompt
    # threshold is strict: exactly at the boundary there is still no artifact
    edge = compile_context(
        task=TASK_VISIT_SUMMARIZE.content,
        page=PageProjection(url="u", title="t", text="x" * 100),
        artifact_store=store,
        artifact_threshold=100,
    )
    assert edge.artifact_refs == () and store.list_ids() == []


def test_over_threshold_without_a_store_keeps_v1_behavior():
    long_text = "no store injected " * 500
    page = PageProjection(url="u", title="t", text=long_text)
    ctx = compile_context(task=TASK_VISIT_SUMMARIZE.content, page=page)
    assert ctx.artifact_refs == ()
    assert "[artifact" not in ctx.prompt
    assert ctx.prompt == PAGE_FRAME_TEMPLATE.render(
        task=TASK_VISIT_SUMMARIZE.content, url=page.url, title=page.title, text=long_text[:4000]
    )


def test_artifact_offload_is_deterministic(tmp_path):
    store = ArtifactStore(dir=tmp_path)
    page = PageProjection(url="https://example.com", title="Example Domain", text="stable body " * 700)
    first = compile_context(task=TASK_VISIT_SUMMARIZE.content, page=page, artifact_store=store)
    second = compile_context(task=TASK_VISIT_SUMMARIZE.content, page=page, artifact_store=store)
    assert first.prompt == second.prompt
    assert first.artifact_refs[0].artifact_id == second.artifact_refs[0].artifact_id


# --- token budget ---------------------------------------------------------------


def test_max_tokens_shrinks_only_the_projection():
    page = PageProjection(url="u", title="t", text="filler " * 2000)
    ctx = compile_context(task=TASK_VISIT_SUMMARIZE.content, page=page, max_tokens=300)
    assert ctx.budget_fits is True
    assert ctx.estimated_tokens == len(ctx.prompt) // 4 <= 300
    assert ctx.truncated is True
    # task and frame survive untouched; only the page-content body shrank
    assert ctx.prompt.startswith(f"Task: {TASK_VISIT_SUMMARIZE.content}\n")
    assert "Page URL: u\n" in ctx.prompt and "Page title: t\n" in ctx.prompt
    assert ctx.breakdown["text"] < len(page.text) // 4
    # without a budget the same input compiles much larger
    assert compile_context(task=TASK_VISIT_SUMMARIZE.content, page=page).estimated_tokens > 300


def test_impossible_budget_shrinks_to_empty_text_and_flags_it():
    ctx = compile_context(task=TASK_VISIT_SUMMARIZE.content, page=PAGE, max_tokens=1)
    assert ctx.budget_fits is False  # even empty text cannot satisfy the frame cost
    assert ctx.estimated_tokens > 1
    assert "Page content:\n\n" in ctx.prompt  # projection shrunk to nothing...
    assert ctx.prompt.startswith(f"Task: {TASK_VISIT_SUMMARIZE.content}")  # ...frame never cut


def test_fitting_budget_is_a_noop():
    plain = compile_context(task=TASK_VISIT_SUMMARIZE.content, page=PAGE)
    roomy = compile_context(task=TASK_VISIT_SUMMARIZE.content, page=PAGE, max_tokens=plain.estimated_tokens)
    assert roomy.budget_fits is True
    assert roomy.prompt == plain.prompt  # generous budget changes nothing


# --- determinism ----------------------------------------------------------------


def test_same_inputs_compile_identical_prompts_twice():
    a = compile_context(task=TASK_VISIT_SUMMARIZE.content, page=PAGE, max_chars=700)
    b = compile_context(task=TASK_VISIT_SUMMARIZE.content, page=PAGE, max_chars=700)
    assert a.prompt == b.prompt
    assert a.estimated_tokens == b.estimated_tokens and a.breakdown == b.breakdown
