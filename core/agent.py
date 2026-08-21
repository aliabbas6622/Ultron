"""The agent loop: chat that can act.

    model -> proposes actions (ToolCall / ACTION text protocol)
          -> ActionIntent -> PolicyEvaluator (allow | deny)      <- the LLM never authorizes itself
          -> optional human approval hook
          -> ToolProvider.execute -> Verifier.verify            <- "done" is not proof
          -> observation fed back to the model, bounded rounds
          -> final answer + persistent memory of the exchange

Works with ANY v1 ModelProvider: if the brick implements the optional
ToolCallingModel protocol (native function calling) it is used; otherwise the
compact tool catalog enters the prompt and proposals arrive as ACTION lines —
deterministic text protocol, same policy path either way.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Callable

from contracts.action import ActionIntent
from contracts.errors import BlockUnavailableError
from contracts.model import ModelProvider, ToolCallingModel
from contracts.policy import Decision
from contracts.tool import ToolProvider, ToolResult
from contracts.verification import Verifier
from core.chat import ChatMessage, compile_chat_prompt
from core.run_context import RunContext

MAX_TOOL_ROUNDS = 4  # 09: bounded retries / tool rounds, never unbounded
MAX_OBSERVATION_CHARS = 2000

TOOL_PROTOCOL_HEADER = """You can use tools to act. Available tools:
{catalog}
To use a tool, reply with EXACTLY one line per action and nothing else:
ACTION: {{"kind": "<tool kind>", "params": {{...}}}}
You will receive each action's result, then continue. When you have the final
answer for the user, reply with plain text and NO ACTION line. Never invent
tool kinds or params not listed above."""


@dataclass(frozen=True)
class AgentTurnResult:
    answer: ChatMessage
    actions: tuple[ActionIntent, ...] = ()
    results: tuple[ToolResult, ...] = ()
    denials: tuple[str, ...] = ()
    rounds: int = 0


def _tool_catalog(tools: list[ToolProvider]) -> str:
    lines = []
    for tool in tools:
        for d in tool.describe():
            params = ", ".join(
                f"{name}{'?' if name not in (d.params_schema or {}).get('required', []) else ''}"
                for name in (d.params_schema or {}).get("properties", {})
            ) or "no params"
            lines.append(f"- {d.kind}({params}): {d.description} [{d.risk_class}]")
    return "\n".join(lines)


def parse_action_lines(text: str) -> tuple[list[dict], str | None]:
    """Split a model reply into ACTION proposals + remaining text.
    Returns (proposals, error) where error explains a malformed ACTION line."""
    proposals: list[dict] = []
    plain: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("ACTION:"):
            payload = stripped[len("ACTION:"):].strip()
            try:
                proposal = json.loads(payload)
            except ValueError:
                return proposals, f"malformed ACTION line (not JSON): {payload[:120]}"
            if not isinstance(proposal, dict) or "kind" not in proposal:
                return proposals, f"malformed ACTION (need kind+params): {payload[:120]}"
            proposals.append(proposal)
        elif stripped:
            plain.append(stripped)
    return proposals, None


def _observation(intent: ActionIntent, result: ToolResult, verified: str) -> str:
    outputs = {k: (v if not isinstance(v, str) or len(v) <= MAX_OBSERVATION_CHARS else v[:MAX_OBSERVATION_CHARS] + "...")
               for k, v in result.outputs.items()}
    state = "ok" if result.ok else f"FAILED: {result.error}"
    return f"RESULT {intent.kind} [{state}] {json.dumps(outputs, ensure_ascii=False, default=str)} {verified}".strip()


def agent_turn(
    run: RunContext,
    model: ModelProvider,
    history: list[ChatMessage],
    user_text: str,
    *,
    policy,
    tools: list[ToolProvider],
    verifiers: list[Verifier] | None = None,
    memory=None,
    system: str | None = None,
    on_action: Callable[[ActionIntent], bool] | None = None,
) -> AgentTurnResult:
    """One agentic exchange. Side effects only ever happen through
    policy(->approval)->tool->verify; everything the model says is a proposal."""
    verifiers = verifiers or []
    by_kind = {d.kind: tool for tool in tools for d in tool.describe()}
    risk_by_kind = {d.kind: d.risk_class for tool in tools for d in tool.describe()}

    native = isinstance(model, ToolCallingModel) and getattr(model.capabilities, "tool_calling", False)
    system_parts = [system] if system else []
    if tools:
        system_parts.append(TOOL_PROTOCOL_HEADER.format(catalog=_tool_catalog(tools)))

    memory_lines: list[str] = []
    if memory is not None:
        try:
            from contracts.memory import MemoryQuery

            records = memory.retrieve(run, MemoryQuery(kind="episodic", subject="chat", limit=5))
            memory_lines = [r.value[:280] for r in records if r.value]
        except Exception:  # noqa: BLE001 — optional block; degrade to no recall
            memory_lines = []

    observations: list[str] = []
    actions: list[ActionIntent] = []
    results: list[ToolResult] = []
    denials: list[str] = []
    answer_text = ""
    rounds = 0

    for rounds in range(1, MAX_TOOL_ROUNDS + 1):
        run.check_alive()
        prompt = compile_chat_prompt(history, user_text, memory_lines=memory_lines, system="\n\n".join(system_parts))
        if observations:
            prompt += "\n\nAction results so far:\n" + "\n".join(observations)

        if native:
            try:
                result = model.generate_with_tools(run, prompt, [d for t in tools for d in t.describe()])
            except BlockUnavailableError:
                # the endpoint/model rejected native tools — fall back to the text
                # protocol for the rest of this turn (both paths are policy-gated)
                native = False
                result = model.generate(run, prompt)
        else:
            result = model.generate(run, prompt)

        # proposals can arrive natively (tool_calls) OR as ACTION text lines —
        # some models answer native-tools prompts in text; accept both, always
        text_proposals, parse_error = parse_action_lines(result.text)
        proposals = [{"kind": c.kind, "params": c.params} for c in result.tool_calls] + text_proposals
        if parse_error and not proposals:
            observations = [f"SYSTEM: your {parse_error} — retry with a valid single ACTION line or answer in plain text"]
            continue
        answer_text = _plain_text(result.text)
        run.budget.used_input_tokens += result.input_tokens
        run.budget.used_output_tokens += result.output_tokens
        run.budget.used_model_calls += 1

        if not proposals:
            break  # plain answer, done

        new_observations: list[str] = []
        for proposal in proposals:
            run.check_alive()
            kind, params = str(proposal.get("kind", "")), proposal.get("params", {}) or {}
            if kind not in by_kind:
                new_observations.append(f"SYSTEM: tool kind {kind!r} is not available")
                continue
            intent = ActionIntent(kind=kind, params=dict(params))

            decision = policy.evaluate(intent)
            if decision.decision != Decision.ALLOW:
                denials.append(f"{kind}: {decision.reason}")
                new_observations.append(f"SYSTEM: policy DENIED {kind} ({decision.reason})")
                continue
            needs_approval = risk_by_kind.get(kind, "side_effect") != "normal"
            if needs_approval and on_action is not None and not on_action(intent):
                denials.append(f"{kind}: denied by user")
                new_observations.append(f"SYSTEM: the user DENIED {kind}")
                continue

            tool_result = by_kind[kind].execute(run, intent)
            run.budget.used_tool_calls += 1
            actions.append(intent)
            results.append(tool_result)

            verifier = next((v for v in verifiers if v.supports_kind == kind), None)
            verified_note = ""
            if verifier is not None:
                outcome = verifier.verify(run, intent, tool_result)
                verified_note = f"verified={outcome.verified}"
                if not outcome.verified:
                    new_observations.append(
                        f"SYSTEM: verification FAILED for {kind}: {outcome.detail}"
                    )
                    continue
            new_observations.append(_observation(intent, tool_result, verified_note))

        observations = new_observations or ["SYSTEM: no actionable result — answer in plain text"]

    answer = ChatMessage(role="assistant", text=answer_text.strip() or "(no answer)")

    if memory is not None and answer.text:
        effects = f" tools:{','.join(a.kind for a in actions)}" if actions else ""
        try:
            from contracts.memory import MemoryRecord

            memory.write(run, MemoryRecord(
                kind="episodic", subject="chat", predicate="exchange",
                value=f"U: {user_text[:280]} A: {answer.text[:280]}{effects}",
                source=f"agent:{model.block_id}", observed_at=time.time(), importance=0.4,
            ))
        except Exception:  # noqa: BLE001 — memory must never kill the agent loop
            pass

    return AgentTurnResult(answer=answer, actions=tuple(actions), results=tuple(results),
                           denials=tuple(denials), rounds=rounds)


def _plain_text(text: str) -> str:
    return "\n".join(line for line in text.splitlines() if not line.strip().startswith("ACTION:")).strip()
