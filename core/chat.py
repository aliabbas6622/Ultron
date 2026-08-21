"""Multi-turn chat over ANY ModelProvider v1 brick.

The v1 model contract is generate(run, prompt) only — so conversation state is
compiled into the prompt here (filter > dump, per the token-discipline rules;
a streaming/chat Messages contract is a future optional capability).

If a MemoryProvider brick is supplied, chat gets a persistent self:
- past exchanges are stored as episodic records after each turn,
- recent relevant records are retrieved and folded into the next prompt,
so ULTRON remembers you across sessions (state over history, 01).
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from contracts.memory import MemoryQuery, MemoryRecord
from contracts.model import ModelProvider
from core.run_context import RunContext

MAX_HISTORY_TURNS = 12  # per-session window that enters the prompt
MAX_MEMORY_RECORDS = 5  # cross-session records folded in
MEMORY_SNIPPET_CHARS = 280


@dataclass(frozen=True)
class ChatMessage:
    role: str  # "user" | "assistant"
    text: str


def compile_chat_prompt(history: list[ChatMessage], user_text: str, memory_lines: list[str] | None = None,
                        system: str | None = None) -> str:
    """Deterministic transcript compiler — same inputs, same prompt (replay-friendly)."""
    parts: list[str] = []
    if system:
        parts.append(f"System: {system}")
    if memory_lines:
        parts.append("Things you remember about this user (most recent first):")
        parts.extend(f"- {line}" for line in memory_lines)
    parts.append("Conversation so far:")
    for msg in history[-MAX_HISTORY_TURNS:]:
        who = "User" if msg.role == "user" else "You"
        parts.append(f"{who}: {msg.text}")
    parts.append(f"User: {user_text}")
    parts.append("You:")
    return "\n".join(parts)


def _memory_lines(memory, run: RunContext) -> list[str]:
    try:
        records = memory.retrieve(run, MemoryQuery(kind="episodic", subject="chat", limit=MAX_MEMORY_RECORDS))
    except Exception:  # noqa: BLE001 — memory is an optional block; degrade to no memory
        return []
    return [r.value[:MEMORY_SNIPPET_CHARS] for r in records if r.value]


def chat_turn(
    run: RunContext,
    model: ModelProvider,
    history: list[ChatMessage],
    user_text: str,
    *,
    system: str | None = None,
    memory=None,  # optional MemoryProvider brick
) -> ChatMessage:
    """One exchange. Mutates nothing the caller owns except run.budget; append
    the returned assistant message (and the user message) to history yourself."""
    run.check_alive()
    memory_lines = _memory_lines(memory, run) if memory is not None else []
    prompt = compile_chat_prompt(history, user_text, memory_lines=memory_lines, system=system)

    result = model.generate(run, prompt)
    run.budget.used_input_tokens += result.input_tokens
    run.budget.used_output_tokens += result.output_tokens
    run.budget.used_model_calls += 1

    if memory is not None:
        exchange = f"U: {user_text[:MEMORY_SNIPPET_CHARS]} A: {result.text[:MEMORY_SNIPPET_CHARS]}"
        try:
            memory.write(run, MemoryRecord(
                kind="episodic", subject="chat", predicate="exchange", value=exchange,
                source=f"chat:{model.block_id}", observed_at=time.time(), importance=0.4,
            ))
        except Exception:  # noqa: BLE001 — a failing memory block must not kill chat
            pass
    return ChatMessage(role="assistant", text=result.text.strip())
