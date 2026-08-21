"""Context compiler (06_MEMORY_CONTEXT_ROUTING.md "Context Compiler" pass list).

Passes implemented by compile_context(), mapped to the 06 list:

- select                     -> upstream of this call: one already-selected
                                observation (task + page projection); memory
                                and tool selection remain future work.
- filter                     -> max_chars truncation of the projected text.
- dedupe                     -> _dedupe_lines(): collapse runs of blank lines
                                and repeated identical lines BEFORE truncation;
                                a no-op (byte-stable) for dupe-free text.
- project                    -> render the page projection into the versioned
                                frame (core/instructions.py PAGE_FRAME_TEMPLATE),
                                never a raw dump.
- prune tools                -> FUTURE: no tool schemas enter the prompt yet.
- preserve trust/provenance  -> trust_labels: webpage content is "untrusted",
                                task text is "owner"
                                (07_SECURITY_AND_CONTAINMENT.md trust table).
- choose representation      -> plain-text frame only; TOON/compact-JSON
                                selection is deferred until benchmarks justify
                                it (04_TECH_STACK.md).
- count tokens               -> char//4 estimate (len // 4); swap for a real
                                tokenizer when a model adapter needs accurate
                                budget enforcement rather than a rough ceiling.
- fit budget                 -> max_tokens shrinks ONLY the projected text
                                portion (never task/frame); budget_fits reports
                                whether it fit, even with empty text.
- arrange cacheable/dynamic regions -> FUTURE: the frame is a static
                                cache_class bundle but regions are not arranged
                                explicitly yet.
- validate                   -> FUTURE: full-frame validation; artifact ids are
                                validated at the store boundary
                                (core/artifacts.py).

Large page texts remain external as artifact references (01 principle 4):
when the page text exceeds artifact_threshold and a store is injected, the
full text is content-addressed on disk and the prompt carries the truncated
projection plus a deterministic reference line -- the hash is content-derived,
so the prompt stays reproducible.

v2 features activate ONLY through the new keyword arguments (artifact_store,
artifact_threshold, max_tokens, dedupe). The default
compile_context(task, page, max_chars) path is byte-identical to v1, so the
frozen eval replay hashes (core/eval_harness.py freeze()) keep holding.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from contracts.browser import PageProjection
from core.artifacts import ArtifactRef, ArtifactStore
from core.instructions import PAGE_FRAME_TEMPLATE


@dataclass(frozen=True)
class CompiledContext:
    prompt: str
    estimated_tokens: int
    breakdown: dict[str, int] = field(default_factory=dict)  # per-section token estimates
    truncated: bool = False
    artifact_refs: tuple = ()  # ArtifactRef pointers replacing offloaded bulk
    trust_labels: dict = field(default_factory=dict)  # provenance: {"page": "untrusted", "task": "owner"}
    deduped: bool = False  # dedupe pass actually changed the projected text
    budget_fits: bool = True  # False only if the frame alone busts max_tokens


def _dedupe_lines(text: str) -> tuple[str, bool]:
    """Collapse runs of blank lines and runs of repeated identical lines.
    Returns (text, changed). Byte-stable for texts without such runs: lines
    are split with their endings kept and rejoined verbatim, so CRLF and
    missing trailing newlines survive untouched."""
    if not text:
        return text, False
    kept: list[str] = []
    prev_line: str | None = None
    prev_blank = False
    changed = False
    for line in text.splitlines(keepends=True):
        blank = not line.strip()
        if kept and ((blank and prev_blank) or line == prev_line):
            changed = True
            continue
        kept.append(line)
        prev_line = line
        prev_blank = blank
    return "".join(kept), changed


def compile_context(
    task: str,
    page: PageProjection,
    max_chars: int = 4000,
    *,
    artifact_store: ArtifactStore | None = None,
    artifact_threshold: int = 6000,
    max_tokens: int | None = None,
    dedupe: bool = True,
) -> CompiledContext:
    """Compile task + page projection into the framed prompt.

    Byte-compatibility: with no keyword args this produces the exact v1
    prompt (same frame sections, same order) -- frozen eval replay hashes
    depend on it. The dedupe pass is a no-op for dupe-free text, so even with
    dedupe=True (default) dupe-free inputs compile byte-identically.
    """
    full_text, deduped = _dedupe_lines(page.text) if dedupe else (page.text, False)

    projection = full_text[:max_chars]
    truncated = len(full_text) > max_chars

    artifact_refs: tuple[ArtifactRef, ...] = ()
    ref_line = ""
    if artifact_store is not None and len(page.text) > artifact_threshold:
        # offload the FULL raw text; the prompt keeps the truncated projection
        # plus a deterministic pointer (hash is content-derived)
        ref = artifact_store.store(
            page.text,
            content_type="text/plain",
            metadata={"kind": "page_text", "url": page.url, "title": page.title},
        )
        artifact_refs = (ref,)
        ref_line = f"\n[artifact {ref.artifact_id} {ref.size_bytes} bytes full text]"

    def _prompt(text_len: int) -> str:
        return PAGE_FRAME_TEMPLATE.render(
            task=task, url=page.url, title=page.title, text=projection[:text_len] + ref_line
        )

    budget_fits = True
    if max_tokens is not None and len(_prompt(len(projection))) // 4 > max_tokens:
        # shrink only the projected text portion; the task, the frame, and the
        # artifact pointer line are never cut. Binary search the longest
        # prefix that fits (prompt length is monotonic in text length).
        lo, hi = 0, len(projection)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if len(_prompt(mid)) // 4 <= max_tokens:
                lo = mid
            else:
                hi = mid - 1
        truncated = truncated or lo < len(projection)
        projection = projection[:lo]
        budget_fits = len(_prompt(lo)) // 4 <= max_tokens

    text = projection + ref_line
    prompt = PAGE_FRAME_TEMPLATE.render(task=task, url=page.url, title=page.title, text=text)
    breakdown = {k: len(v) // 4 for k, v in (("task", task), ("url", page.url), ("title", page.title), ("text", text))}
    return CompiledContext(
        prompt=prompt,
        estimated_tokens=len(prompt) // 4,
        breakdown=breakdown,
        truncated=truncated,
        artifact_refs=artifact_refs,
        trust_labels={"page": "untrusted", "task": "owner"},
        deduped=deduped,
        budget_fits=budget_fits,
    )
