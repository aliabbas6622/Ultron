"""V0.1 vertical slice runtime.

user input -> browser -> page projection -> context compiler -> model
-> ActionIntent(write file) -> policy -> filesystem tool -> verification -> response

Depends only on contracts (contracts.model, contracts.browser), never on adapter internals.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from contracts.browser import BrowserProvider
from contracts.model import ModelProvider
from core.action_intent import ActionIntent
from core.bus import EventBus
from core.context_compiler import compile_context
from core.errors import PolicyDeniedError, VerificationFailedError
from core.events import (
    ModelCompleted,
    ModelStarted,
    PolicyDenied,
    ToolCompleted,
    ToolRequested,
    VerificationFailed,
)
from core.file_tool import execute_file_write
from core.instructions import PAGE_FRAME_TEMPLATE, TASK_VISIT_SUMMARIZE
from core.policy import Decision, PolicyEngine
from core.run_context import RunContext
from core.verifier import verify_file_write


@dataclass(frozen=True)
class SliceResult:
    summary: str
    output_path: str


def run_visit_summarize_save(
    run: RunContext,
    bus: EventBus,
    browser: BrowserProvider,
    model: ModelProvider,
    policy: PolicyEngine,
    url: str,
    output_path: str,
    timeout_s: float = 30.0,
    on_action_intent: Callable[[ActionIntent], bool] | None = None,
) -> SliceResult:
    """on_action_intent: optional approval hook, called after policy ALLOWs an intent and
    before it executes. Returning False denies the action. Callers that don't need
    interactive approval (e.g. tests) omit it and get the existing allow-by-policy behavior."""
    run.check_alive()
    page = browser.visit(run, url, timeout_s)

    run.check_alive()
    ctx = compile_context(task=TASK_VISIT_SUMMARIZE.content, page=page)
    run.metadata.setdefault("instructions", []).extend(
        [TASK_VISIT_SUMMARIZE.record_id(), PAGE_FRAME_TEMPLATE.record_id()]
    )
    run.metadata["context_breakdown"] = dict(ctx.breakdown)

    run.check_alive()
    bus.publish(ModelStarted(run_id=run.run_id, trace_id=run.trace_id, block_id=model.block_id))
    result = model.generate(run, ctx.prompt)
    run.budget.used_input_tokens += result.input_tokens
    run.budget.used_output_tokens += result.output_tokens
    run.budget.used_model_calls += 1
    bus.publish(
        ModelCompleted(
            run_id=run.run_id,
            trace_id=run.trace_id,
            block_id=model.block_id,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            cached_tokens=result.cached_tokens,
        )
    )

    summary = result.text.strip()
    intent = ActionIntent(kind="file_write", params={"path": output_path, "content": summary})

    decision = policy.evaluate(intent)
    if decision.decision != Decision.ALLOW:
        bus.publish(
            PolicyDenied(run_id=run.run_id, trace_id=run.trace_id, action_id=intent.action_id, reason=decision.reason)
        )
        raise PolicyDeniedError(decision.reason)

    if on_action_intent is not None and not on_action_intent(intent):
        bus.publish(
            PolicyDenied(run_id=run.run_id, trace_id=run.trace_id, action_id=intent.action_id, reason="denied by user")
        )
        raise PolicyDeniedError("denied by user")

    run.check_alive()
    bus.publish(
        ToolRequested(
            run_id=run.run_id, trace_id=run.trace_id, action_id=intent.action_id, tool_name="file_write",
            args=intent.params,
        )
    )
    write_result = execute_file_write(intent)
    run.budget.used_external_writes += 1
    bus.publish(
        ToolCompleted(run_id=run.run_id, trace_id=run.trace_id, action_id=intent.action_id, ok=True, result=write_result)
    )

    try:
        verify_file_write(write_result, expected_content=summary)
    except VerificationFailedError as exc:
        run.metadata["verification_ok"] = False
        bus.publish(
            VerificationFailed(run_id=run.run_id, trace_id=run.trace_id, action_id=intent.action_id, reason=str(exc))
        )
        raise
    run.metadata["verification_ok"] = True

    return SliceResult(summary=summary, output_path=write_result.path)
