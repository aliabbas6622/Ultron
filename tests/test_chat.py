"""Chat tests: prompt compilation determinism, multi-turn over any v1 brick,
persistent memory across 'sessions' (the Hermes-style remember-you behavior)."""

from __future__ import annotations

import os

from contracts.memory import MemoryQuery
from contracts.model import HealthStatus, ModelCapabilities, ModelResult
from core.chat import ChatMessage, chat_turn, compile_chat_prompt
from core.memory_sqlite import SqliteMemoryStore
from core.run_context import RunContext


class EchoModel:
    """Deterministic brick: replies with the last user line it sees."""
    block_id = "echo"
    capabilities = ModelCapabilities()

    def generate(self, run, prompt):
        run.check_alive()
        return ModelResult(text=f"echo:{prompt.splitlines()[-2]}", input_tokens=5, output_tokens=5,
                           finish_reason="stop")

    def health(self):
        return HealthStatus(healthy=True)


def test_compile_chat_prompt_is_deterministic():
    history = [ChatMessage("user", "hi"), ChatMessage("assistant", "hello")]
    a = compile_chat_prompt(history, "next", memory_lines=["likes tea"], system="be brief")
    b = compile_chat_prompt(history, "next", memory_lines=["likes tea"], system="be brief")
    assert a == b
    assert "Things you remember" in a and "likes tea" in a and "User: next" in a
    assert "System: be brief" in a.splitlines()[0]


def test_chat_turn_appends_and_counts_budget():
    run = RunContext()
    history = [ChatMessage("user", "hello")]
    answer = chat_turn(run, EchoModel(), history, "what time is it")
    assert answer.role == "assistant"
    assert answer.text.startswith("echo:")
    assert run.budget.used_model_calls == 1 and run.budget.used_input_tokens == 5


def test_chat_persists_and_recalls_across_sessions(tmp_path):
    store = SqliteMemoryStore(os.path.join(str(tmp_path), "memory.db"))
    run = RunContext()

    first = chat_turn(run, EchoModel(), [], "my favorite color is blue", memory=store)
    assert first.role == "assistant"

    # brand-new "session": same store, empty history — the exchange is recalled
    recalled = store.retrieve(run, MemoryQuery(kind="episodic", subject="chat", limit=5))
    assert any("favorite color" in r.value for r in recalled)

    answer = chat_turn(run, EchoModel(), [], "what is my favorite color?", memory=store)
    assert answer.text.startswith("echo:")
    store.close()


def test_failing_memory_does_not_kill_chat(tmp_path, monkeypatch):
    class ExplodingMemory:
        def retrieve(self, run, query):
            raise RuntimeError("memory exploded")

        def write(self, run, record):
            raise RuntimeError("memory exploded")

    run = RunContext()
    answer = chat_turn(run, EchoModel(), [], "still works?", memory=ExplodingMemory())
    assert answer.text.startswith("echo:")
