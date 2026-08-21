"""Streaming capability conformance (StreamingModel, optional ModelProvider v1
capability): delta ordering, terminal conditions, normalized failures, and
capability advertisement — fully offline via a mocked urlopen, like
test_providers.py. Cancellation before the request is part of the minimum
model contract (09_CODING_STANDARDS.md, Streaming and Cancellation)."""

from __future__ import annotations

import io
import json
import urllib.error

import pytest

from contracts.errors import BlockUnavailableError, CancelledError
from contracts.model import StreamingModel
from providers.models import OllamaLocalModel, OpenAICompatModel


class _StreamResp:
    """Fake urlopen response: context manager + line iterator (byte lines)."""

    def __init__(self, lines: list[bytes]):
        self._lines = lines

    def __iter__(self):
        return iter(self._lines)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _mock_stream_urlopen(monkeypatch, lines: list[bytes]):
    import providers.models as models

    def fake_urlopen(req, timeout=None):
        return _StreamResp(lines)

    # urlopen is reached as urllib.request.urlopen inside providers.models;
    # patching the attribute on the real urllib.request module is fine here.
    monkeypatch.setattr(models.urllib.request, "urlopen", fake_urlopen)


def _mock_stream_failure(monkeypatch, exc: Exception):
    import providers.models as models

    def fake_urlopen(req, timeout=None):
        raise exc

    monkeypatch.setattr(models.urllib.request, "urlopen", fake_urlopen)


def _ollama_lines(*events: dict) -> list[bytes]:
    return [json.dumps(e).encode("utf-8") for e in events]


def _sse_lines(*events: str) -> list[bytes]:
    return [f"data: {e}\r\n".encode("utf-8") for e in events]


# --- ollama native NDJSON streaming -------------------------------------------

def test_ollama_stream_deltas_in_order_and_done_terminates(monkeypatch):
    # a line after done:true must never surface
    _mock_stream_urlopen(monkeypatch, _ollama_lines(
        {"response": "Hello", "done": False},
        {"response": " ", "done": False},
        {"response": "world", "done": True},
        {"response": "AFTER-DONE", "done": True},
    ))
    model = OllamaLocalModel(model="llama3.2")
    from core.run_context import RunContext

    deltas = list(model.generate_stream(RunContext(), "hi"))
    assert deltas == ["Hello", " ", "world"]
    assert "".join(deltas) == "Hello world"


def test_ollama_stream_http_error_normalizes(monkeypatch):
    _mock_stream_failure(monkeypatch, urllib.error.HTTPError(
        "http://localhost:11434/api/generate", 500, b"boom", hdrs=None, fp=io.BytesIO(b"boom")))
    model = OllamaLocalModel(model="llama3.2")
    from core.run_context import RunContext

    with pytest.raises(BlockUnavailableError, match="500"):
        list(model.generate_stream(RunContext(), "hi"))


def test_ollama_stream_urlerror_normalizes(monkeypatch):
    _mock_stream_failure(monkeypatch, urllib.error.URLError("connection refused"))
    model = OllamaLocalModel(model="llama3.2")
    from core.run_context import RunContext

    with pytest.raises(BlockUnavailableError, match="failed"):
        list(model.generate_stream(RunContext(), "hi"))


# --- OpenAI-compatible SSE streaming -------------------------------------------

def test_openai_sse_stream_accumulates_and_done_terminates(monkeypatch):
    _mock_stream_urlopen(monkeypatch, _sse_lines(
        '{"choices": [{"delta": {"role": "assistant"}}]}',        # no content -> skipped
        '{"choices": [{"delta": {"content": "Hel"}}]}',
        '{"choices": [{"delta": {"content": ""}}]}',              # empty content -> skipped
        '{"choices": [{"delta": {"content": "lo"}}]}',
        "[DONE]",
        '{"choices": [{"delta": {"content": "AFTER-DONE"}}]}',   # after terminal -> never yielded
    ))
    model = OpenAICompatModel(model="m", base_url="https://ollama.com", api_key="k")
    from core.run_context import RunContext

    deltas = list(model.generate_stream(RunContext(), "hi"))
    assert deltas == ["Hel", "lo"]
    assert "".join(deltas) == "Hello"


def test_openai_stream_base_url_with_v1(monkeypatch):
    """Streaming must use the same endpoint derivation as generate (no double /v1)."""
    import providers.models as models

    seen = {}

    def fake_urlopen(req, timeout=None):
        seen["url"] = req.full_url
        return _StreamResp(_sse_lines('[DONE]'))

    monkeypatch.setattr(models.urllib.request, "urlopen", fake_urlopen)
    model = OpenAICompatModel(model="m", base_url="https://api.deepseek.com/v1", api_key="k")
    from core.run_context import RunContext

    assert list(model.generate_stream(RunContext(), "hi")) == []
    assert seen["url"] == "https://api.deepseek.com/v1/chat/completions"


def test_openai_stream_http_error_normalizes(monkeypatch):
    _mock_stream_failure(monkeypatch, urllib.error.HTTPError(
        "https://ollama.com/v1/chat/completions", 503, b"overloaded", hdrs=None, fp=io.BytesIO(b"overloaded")))
    model = OpenAICompatModel(model="m", base_url="https://ollama.com", api_key="k")
    from core.run_context import RunContext

    with pytest.raises(BlockUnavailableError, match="503"):
        list(model.generate_stream(RunContext(), "hi"))


def test_openai_stream_urlerror_normalizes(monkeypatch):
    _mock_stream_failure(monkeypatch, urllib.error.URLError("dns failure"))
    model = OpenAICompatModel(model="m", base_url="https://ollama.com", api_key="k")
    from core.run_context import RunContext

    with pytest.raises(BlockUnavailableError, match="failed"):
        list(model.generate_stream(RunContext(), "hi"))


# --- cancellation (minimum contract) + capability advertisement -----------------

def test_stream_checks_alive_before_request(monkeypatch):
    """A cancelled run must fail fast with CancelledError before any HTTP happens."""
    import providers.models as models

    def boom(req, timeout=None):
        raise AssertionError("urlopen must not be reached on a cancelled run")

    monkeypatch.setattr(models.urllib.request, "urlopen", boom)
    from core.run_context import RunContext

    run = RunContext()
    run.cancel()
    for model in (OllamaLocalModel(model="llama3.2"),
                  OpenAICompatModel(model="m", base_url="https://ollama.com")):
        with pytest.raises(CancelledError):
            list(model.generate_stream(run, "hi"))


def test_bricks_advertise_streaming_capability():
    from contracts.model import ModelProvider

    ollama = OllamaLocalModel(model="llama3.2")
    compat = OpenAICompatModel(model="m", base_url="https://ollama.com", api_key="k")
    for model in (ollama, compat):
        assert isinstance(model, ModelProvider)
        assert isinstance(model, StreamingModel)  # runtime_checkable capability check
        assert model.capabilities.streaming
