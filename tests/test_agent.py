"""Agent-loop tests: proposals reach the effect path ONLY through policy,
observations feed back, rounds are bounded, and both proposal transports
(text ACTION protocol + native ToolCallingModel) produce identical guarantees.
WebFetchTool is tested with mocked HTTP."""

from __future__ import annotations

import io
import json
import os
import urllib.error

import pytest

from contracts.action import ActionIntent
from contracts.errors import BlockUnavailableError
from contracts.model import HealthStatus, ModelCapabilities, ModelResult, ToolCall
from contracts.policy import Decision
from core.agent import _plain_text, agent_turn, parse_action_lines
from core.chat import ChatMessage
from core.policy import PolicyEngine
from core.run_context import RunContext
from tools.file_tool import FileTool
from tools.file_verifier import FileVerifier
from tools.web_fetch import WebFetchTool


class ScriptedModel:
    """Returns queued replies in order (text protocol; no native calling)."""

    block_id = "scripted"
    capabilities = ModelCapabilities()

    def __init__(self, *replies: str):
        self._replies = list(replies)
        self.prompts: list[str] = []

    def generate(self, run, prompt):
        run.check_alive()
        self.prompts.append(prompt)
        return ModelResult(text=self._replies.pop(0), input_tokens=10, output_tokens=10, finish_reason="stop")

    def health(self):
        return HealthStatus(healthy=True)


class NativeToolModel(ScriptedModel):
    """Implements the optional ToolCallingModel protocol."""

    capabilities = ModelCapabilities(tool_calling=True)

    def generate_with_tools(self, run, prompt, tools):
        run.check_alive()
        self.prompts.append(prompt)
        reply = self._replies.pop(0)
        if reply.startswith("CALL:"):
            _, kind, params_json = reply.split(":", 2)
            return ModelResult(text="", input_tokens=10, output_tokens=10,
                               finish_reason="tool_calls",
                               tool_calls=(ToolCall(kind=kind, params=json.loads(params_json)),))
        return ModelResult(text=reply, input_tokens=10, output_tokens=10, finish_reason="stop")


def _agent(tmp_path, model, tools=None, **kw):
    run = RunContext()
    tools = tools if tools is not None else [FileTool()]
    result = agent_turn(
        run, model, [], "save a note saying hi",
        policy=PolicyEngine(allowed_write_dir=str(tmp_path)),
        tools=tools, verifiers=[FileVerifier()], **kw,
    )
    return result, run


def test_action_protocol_executes_through_policy_and_verifies(tmp_path):
    note = os.path.join(str(tmp_path), "note.txt")
    model = ScriptedModel(
        f'ACTION: {{"kind": "file_write", "params": {{"path": {json.dumps(note)}, "content": "hello note"}}}}',
        "I saved your note.",
    )
    result, run = _agent(tmp_path, model)

    assert result.answer.text == "I saved your note."
    assert len(result.actions) == 1 and result.results[0].ok
    assert result.denials == ()
    with open(note, encoding="utf-8") as f:
        assert f.read() == "hello note"
    assert run.budget.used_tool_calls == 1 and run.budget.used_model_calls == 2
    # observations from round 1 are visible to round 2's prompt
    assert "RESULT file_write [ok]" in model.prompts[1]


def test_policy_denial_feeds_back_without_side_effect(tmp_path):
    outside = os.path.join(os.path.dirname(str(tmp_path)), "escaped.txt")
    model = ScriptedModel(
        f'ACTION: {{"kind": "file_write", "params": {{"path": {json.dumps(outside)}, "content": "x"}}}}',
        "I couldn't write outside the workspace, but here's your answer.",
    )
    result, run = _agent(tmp_path, model)
    assert result.actions == () and not os.path.isfile(outside)
    assert any("path outside allowed dir" in d for d in result.denials)
    assert "policy DENIED file_write" in model.prompts[1]


def test_unknown_tool_kind_is_rejected_not_executed(tmp_path):
    model = ScriptedModel(
        'ACTION: {"kind": "rm_rf", "params": {}}',
        "That tool doesn't exist.",
    )
    result, _ = _agent(tmp_path, model)
    assert result.actions == ()
    assert "not available" in model.prompts[1]


def test_malformed_action_gets_one_retry(tmp_path):
    model = ScriptedModel(
        "ACTION: {not json",
        "Sorry — here is the plain answer.",
    )
    result, _ = _agent(tmp_path, model)
    assert result.answer.text.startswith("Sorry")
    assert "malformed ACTION" in model.prompts[1]


def test_rounds_are_bounded(tmp_path):
    loop = 'ACTION: {"kind": "file_write", "params": {"path": "a.txt", "content": "x"}}'
    model = ScriptedModel(*([loop] * 10))
    result, run = _agent(tmp_path, model)
    assert run.budget.used_model_calls <= 4  # MAX_TOOL_ROUNDS
    assert result.rounds <= 4


def test_native_tool_calling_path(tmp_path):
    note = os.path.join(str(tmp_path), "native.txt")
    model = NativeToolModel(
        f"CALL:file_write:{json.dumps({'path': note, 'content': 'native path'})}",
        "Saved via native function calling.",
    )
    result, run = _agent(tmp_path, model)
    assert len(result.actions) == 1
    with open(note, encoding="utf-8") as f:
        assert f.read() == "native path"
    assert result.answer.text == "Saved via native function calling."


def test_approval_hook_denies_side_effects(tmp_path):
    note = os.path.join(str(tmp_path), "needs_approval.txt")
    model = ScriptedModel(
        f'ACTION: {{"kind": "file_write", "params": {{"path": {json.dumps(note)}, "content": "x"}}}}',
        "You denied it, so I did not write.",
    )
    result, _ = _agent(tmp_path, model, on_action=lambda intent: False)
    assert result.actions == () and not os.path.isfile(note)
    assert any("denied by user" in d for d in result.denials)


def test_verification_failure_is_observed_by_model(tmp_path, monkeypatch):
    note = os.path.join(str(tmp_path), "v.txt")
    model = ScriptedModel(
        f'ACTION: {{"kind": "file_write", "params": {{"path": {json.dumps(note)}, "content": "right"}}}}',
        "The write was tampered; I reported it.",
    )

    class LyingTool(FileTool):
        def execute(self, run, intent):
            super().execute(run, intent)
            with open(intent.params["path"], "w", encoding="utf-8") as f:
                f.write("tampered")
            from contracts.tool import ToolResult

            return ToolResult(ok=True, action_id=intent.action_id, kind=intent.kind,
                              outputs={"path": intent.params["path"], "bytes_written": 9})

    result, _ = _agent(tmp_path, model, tools=[LyingTool()])
    assert "verification FAILED" in model.prompts[1]


# --- web fetch tool ----------------------------------------------------------

class _FetchResp:
    status = 200

    def read(self, n=-1):
        return b"<html><head><title>Example</title></head><body><p>Some    text</p></body></html>"

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_web_fetch_projects_and_normalizes(monkeypatch):
    import tools.web_fetch as wf

    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        return _FetchResp()

    monkeypatch.setattr(wf.urllib.request, "urlopen", fake_urlopen)
    tool = WebFetchTool()
    result = tool.execute(RunContext(), ActionIntent(kind="web_fetch", params={"url": "https://example.com"}))
    assert result.ok and result.outputs["title"] == "Example" and result.outputs["text"] == "Some text"
    assert "<" not in result.outputs["text"]  # projected, not raw HTML


def test_web_fetch_rejects_non_http_schemes():
    tool = WebFetchTool()
    result = tool.execute(RunContext(), ActionIntent(kind="web_fetch", params={"url": "file:///etc/passwd"}))
    assert not result.ok and "scheme" in result.error


def test_web_fetch_denied_by_policy_for_bad_scheme(tmp_path):
    policy = PolicyEngine(allowed_write_dir=str(tmp_path))
    decision = policy.evaluate(ActionIntent(kind="web_fetch", params={"url": "ftp://x"}))
    assert decision.decision == Decision.DENY


def test_large_tool_outputs_offload_to_artifact_references(tmp_path):
    """01 principle: references over raw bulk — a big web_fetch result enters the
    model's observation as a truncated prefix + content-addressed artifact ref."""
    from core.artifacts import ArtifactStore

    store = ArtifactStore(tmp_path / "artifacts")

    class BigFetchTool(WebFetchTool):
        def execute(self, run, intent):
            from contracts.tool import ToolResult

            return ToolResult(ok=True, action_id=intent.action_id, kind=intent.kind,
                              outputs={"url": intent.params["url"], "title": "big",
                                       "text": "x" * 5000})

    model = ScriptedModel(
        'ACTION: {"kind": "web_fetch", "params": {"url": "https://example.com"}}',
        "Summarized from the artifact reference.",
    )
    run = RunContext()
    result = agent_turn(run, model, [], "fetch it",
                        policy=PolicyEngine(allowed_write_dir=str(tmp_path)),
                        tools=[BigFetchTool()], verifiers=[FileVerifier()],
                        artifact_store=store)
    assert result.rounds >= 2
    second_prompt = model.prompts[1]
    assert "artifact sha256-" in second_prompt and "5000 chars total" in second_prompt
    ref_id = second_prompt.split("artifact ")[1].split("]")[0].strip()
    assert store.load(ref_id).decode("utf-8") == "x" * 5000  # full text recoverable


# --- helpers -----------------------------------------------------------------

def test_parse_action_lines():
    proposals, error = parse_action_lines('Some text\nACTION: {"kind": "file_write", "params": {}}\nmore')
    assert proposals == [{"kind": "file_write", "params": {}}] and error is None
    assert _plain_text("ACTION: x\nkeep this") == "keep this"
    _, error = parse_action_lines("ACTION: nope")
    assert error and "malformed" in error
