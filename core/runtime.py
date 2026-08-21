"""V0.1 vertical slice runtime — pure composition.

user input -> browser -> page projection -> context compiler -> model
-> ActionIntent(write file) -> policy -> tool executor -> verifier -> response

Every capability is an injected brick behind a contracts/ protocol: browser,
model, policy, tool, verifier. The runtime owns no block implementations
(LEGO rule) — swap any brick at call time, including from a different vendor
or host ecosystem.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from contracts.action import ActionIntent
from contracts.browser import BrowserProvider
from contracts.errors import PolicyDeniedError, ToolExecutionError, VerificationFailedError
from contracts.model import ModelProvider
from contracts.policy import PolicyEvaluator
from contracts.tool import ToolProvider
from contracts.verification import Verifier
from core.bus import EventBus
from core.context_compiler import compile_context
from core.events import (
    ModelCompleted,
    ModelStarted,
    PolicyDenied,
    ToolCompleted,
    ToolRequested,
    VerificationFailed,
)
from core.instructions import PAGE_FRAME_TEMPLATE, TASK_VISIT_SUMMARIZE
from core.run_context import RunContext


@dataclass(frozen=True)
class SliceResult:
    summary: str
    output_path: str


def run_visit_summarize_save(
    run: RunContext,
    bus: EventBus,
    browser: BrowserProvider,
    model: ModelProvider,
    policy: PolicyEvaluator,
    tool: ToolProvider,
    verifier: Verifier,
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
    if decision.decision.name != "ALLOW":
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
            run_id=run.run_id, trace_id=run.trace_id, action_id=intent.action_id, tool_name=intent.kind,
            args=intent.params,
        )
    )
    tool_result = tool.execute(run, intent)
    run.budget.used_tool_calls += 1
    if intent.kind == "file_write":
        run.budget.used_external_writes += 1
    bus.publish(
        ToolCompleted(
            run_id=run.run_id, trace_id=run.trace_id, action_id=intent.action_id,
            ok=tool_result.ok, result=tool_result,
        )
    )
    if not tool_result.ok:
        raise ToolExecutionError(tool_result.error or f"{tool.block_id} failed without detail")

    outcome = verifier.verify(run, intent, tool_result)
    if not outcome.verified:
        run.metadata["verification_ok"] = False
        bus.publish(
            VerificationFailed(
                run_id=run.run_id, trace_id=run.trace_id, action_id=intent.action_id, reason=outcome.detail
            )
        )
        raise VerificationFailedError(outcome.detail)
    run.metadata["verification_ok"] = True

    return SliceResult(summary=summary, output_path=tool_result.outputs.get("path", output_path))
