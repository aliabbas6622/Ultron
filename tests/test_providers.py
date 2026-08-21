"""Provider system tests: registry CRUD (TOML + SecretStore), brick factory,
and the model bricks' HTTP behavior via a mocked urlopen (offline, deterministic)."""

from __future__ import annotations

import io
import json
import urllib.error

import pytest

from contracts.errors import BlockUnavailableError
from contracts.secret_store import SecretStore
from providers.models import OllamaLocalModel, OpenAICompatModel, _request_json
from providers.registry import FileSecretStore, ProviderRegistry, ultron_home


# --- registry ---------------------------------------------------------------

def test_registry_crud_round_trip(tmp_path, monkeypatch):
    monkeypatch.setenv("ULTRON_HOME", str(tmp_path))
    assert ultron_home() == tmp_path

    reg = ProviderRegistry.load()  # seeds ollama-local default on first run
    assert reg.default_provider == "ollama-local"

    reg.add("ollama-cloud", "ollama-cloud", model="gemma4:31b-cloud",
            api_key="sk-test-123", set_default=True)
    assert reg.default_provider == "ollama-cloud"
    assert reg.secrets.get("ollama-cloud") == "sk-test-123"

    # config survives reload; secrets are NOT in the config file
    reg2 = ProviderRegistry.load()
    assert "ollama-cloud" in reg2.specs and reg2.specs["ollama-cloud"].model == "gemma4:31b-cloud"
    assert "sk-test-123" not in reg2.config_path.read_text(encoding="utf-8")
    assert reg2.secrets.get("ollama-cloud") == "sk-test-123"

    reg2.edit("ollama-cloud", model="other-model")
    assert ProviderRegistry.load().specs["ollama-cloud"].model == "other-model"

    reg2.set_default("ollama-local")
    assert ProviderRegistry.load().default_provider == "ollama-local"

    reg2.remove("ollama-cloud")
    reg3 = ProviderRegistry.load()
    assert "ollama-cloud" not in reg3.specs
    assert reg3.secrets.get("ollama-cloud") is None  # secret removed with the provider


def test_registry_factory_builds_right_bricks(tmp_path, monkeypatch):
    monkeypatch.setenv("ULTRON_HOME", str(tmp_path))
    reg = ProviderRegistry.load()
    reg.add("cloud", "ollama-cloud", model="gemma4:31b-cloud", api_key="k")
    reg.add("deepseek", "openai-compatible", base_url="https://api.deepseek.com/v1",
            model="deepseek-chat", api_key="k2")

    assert isinstance(reg.build("cloud"), OpenAICompatModel)
    deepseek = reg.build("deepseek")
    assert isinstance(deepseek, OpenAICompatModel) and deepseek.api_key == "k2"
    local = reg.build("ollama-local")
    assert isinstance(local, OllamaLocalModel)

    with pytest.raises(ValueError, match="no provider configured|unknown"):
        reg.build("does-not-exist")


def test_secret_store_contract_conformance(tmp_path):
    store = FileSecretStore(tmp_path / "secrets.json")
    assert isinstance(store, SecretStore)  # runtime_checkable protocol
    store.set("a", "1")
    assert store.get("a") == "1" and store.list() == ["a"]
    store.delete("a")
    assert store.get("a") is None


# --- bricks (mocked HTTP) ----------------------------------------------------

class _Resp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status = status

    def read(self):
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _mock_urlopen(monkeypatch, responses):
    """responses: dict url-suffix -> (status, payload). Unknown URLs 404."""
    import providers.models as models

    def fake_urlopen(req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        for suffix, (status, payload) in responses.items():
            if url.endswith(suffix):
                if status != 200:
                    body = json.dumps(payload).encode("utf-8")
                    raise urllib.error.HTTPError(url, status, body, hdrs=None, fp=io.BytesIO(body))
                return _Resp(payload, status)
        body = b'{"error": "no route"}'
        raise urllib.error.HTTPError(url, 404, body, hdrs=None, fp=io.BytesIO(body))

    monkeypatch.setattr(models.urllib.request, "urlopen", fake_urlopen)


def test_openai_compat_generate_and_usage(tmp_path, monkeypatch):
    _mock_urlopen(monkeypatch, {
        "/v1/chat/completions": (200, {
            "choices": [{"message": {"content": "pong"}}],
            "usage": {"prompt_tokens": 11, "completion_tokens": 2,
                      "prompt_tokens_details": {"cached_tokens": 4}},
        }),
        "/v1/models": (200, {"data": [{"id": "gemma4:31b-cloud"}]}),
    })
    model = OpenAICompatModel(model="gemma4:31b-cloud", base_url="https://ollama.com",
                              api_key="k", block_id="ollama-cloud.test")
    from core.run_context import RunContext

    result = model.generate(RunContext(), "ping")
    assert result.text == "pong" and result.input_tokens == 11 and result.cached_tokens == 4
    assert model.health().healthy


def test_openai_compat_auth_failure_normalizes(tmp_path, monkeypatch):
    _mock_urlopen(monkeypatch, {"/v1/models": (401, {"error": "bad key"})})
    model = OpenAICompatModel(model="m", base_url="https://ollama.com", api_key="bad")
    health = model.health()
    assert not health.healthy and "api key" in health.detail


def test_openai_compat_generate_http_error_normalizes(tmp_path, monkeypatch):
    _mock_urlopen(monkeypatch, {"/v1/chat/completions": (503, {"error": "overloaded"})})
    model = OpenAICompatModel(model="m", base_url="https://ollama.com", api_key="k")
    from core.run_context import RunContext

    with pytest.raises(BlockUnavailableError, match="503"):
        model.generate(RunContext(), "ping")


def test_ollama_local_health_and_generate(tmp_path, monkeypatch):
    _mock_urlopen(monkeypatch, {
        "/api/generate": (200, {"response": "hi there", "prompt_eval_count": 3, "eval_count": 2,
                                "done_reason": "stop"}),
        "/api/tags": (200, {"models": [{"name": "llama3.2:latest"}]}),
    })
    model = OllamaLocalModel(model="llama3.2")
    assert model.health().healthy
    from core.run_context import RunContext

    result = model.generate(RunContext(), "hello")
    assert result.text == "hi there" and result.finish_reason == "stop"


def test_request_json_network_error_normalizes(monkeypatch):
    import socket

    import providers.models as models

    def boom(req, timeout=None):
        raise urllib.error.URLError(socket.timeout())

    monkeypatch.setattr(models.urllib.request, "urlopen", boom)
    with pytest.raises(BlockUnavailableError, match="failed"):
        models._request_json("https://x.example")
