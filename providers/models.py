"""Model provider bricks (Python): local Ollama, Ollama Cloud, and any
OpenAI-compatible endpoint (DeepSeek, Groq, OpenRouter, OpenAI, ...).

Depends on contracts/ only — grab-and-go like tools/. The registry in
providers/registry.py decides which brick a config entry gets; this module
knows nothing about config files.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Iterator
from dataclasses import dataclass

from contracts.errors import BlockUnavailableError
from contracts.health import HealthStatus
from contracts.model import ModelCapabilities, ModelResult, ToolCall

_REQUEST_TIMEOUT_S = 120.0


def _request_json(url: str, *, body: dict | None = None, headers: dict | None = None, timeout: float = _REQUEST_TIMEOUT_S) -> tuple[int, dict | list | None]:
    """One JSON round trip. Returns (status, parsed). HTTP errors are returned,
    not raised, so callers can distinguish auth/health semantics; network and
    parse failures raise BlockUnavailableError (normalized)."""
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8") if body is not None else None,
        headers={"Content-Type": "application/json", "Accept": "application/json", **(headers or {})},
        method="POST" if body is not None else "GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, (json.loads(raw) if raw.strip() else None)
    except urllib.error.HTTPError as exc:
        try:
            detail = exc.read().decode("utf-8", "replace")[:200]
        except Exception:  # noqa: BLE001 — body may be unusable; status is what matters
            detail = ""
        return exc.code, {"error_detail": detail}
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise BlockUnavailableError(f"request to {url} failed: {exc}") from exc
    except ValueError as exc:  # json parse
        raise BlockUnavailableError(f"unparseable response from {url}: {exc}") from exc


def _bearer(api_key: str | None) -> dict:
    return {"Authorization": f"Bearer {api_key}"} if api_key else {}


@dataclass
class OllamaLocalModel:
    """Local `ollama serve` (default http://localhost:11434), native /api endpoints."""

    block_id: str = "ollama-local"
    model: str = "llama3.2"
    base_url: str = "http://localhost:11434"
    capabilities: ModelCapabilities = None  # type: ignore[assignment]  — replaced below

    def __post_init__(self) -> None:
        if self.capabilities is None:
            self.capabilities = ModelCapabilities(prompt_cache=True, streaming=True)

    def generate(self, run, prompt: str) -> ModelResult:
        run.check_alive()
        status, data = _request_json(
            f"{self.base_url}/api/generate",
            body={"model": self.model, "prompt": prompt, "stream": False},
        )
        run.check_alive()
        if status != 200 or not isinstance(data, dict) or not str(data.get("response", "")).strip():
            raise BlockUnavailableError(f"ollama generate failed ({self.model} @ {self.base_url}, http {status}): {data}")
        return ModelResult(
            text=str(data["response"]).strip(),
            input_tokens=int(data.get("prompt_eval_count", 0)),
            output_tokens=int(data.get("eval_count", 0)),
            finish_reason=str(data.get("done_reason", "stop")),
        )

    def generate_stream(self, run, prompt: str) -> Iterator[str]:
        """Optional streaming capability (StreamingModel): newline-delimited JSON
        objects from ollama's native /api/generate with stream=true. Yields
        non-empty "response" deltas until done:true; network/HTTP/parse errors
        normalize to BlockUnavailableError."""
        run.check_alive()
        url = f"{self.base_url}/api/generate"
        req = urllib.request.Request(
            url,
            data=json.dumps({"model": self.model, "prompt": prompt, "stream": True}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT_S) as resp:
                for raw_line in resp:  # http response objects iterate line by line
                    line = raw_line.decode("utf-8", "replace").strip("\r\n")
                    if not line.strip():
                        continue
                    chunk = json.loads(line)
                    delta = str(chunk.get("response", ""))
                    if delta:
                        yield delta
                    if chunk.get("done"):
                        return
        except urllib.error.HTTPError as exc:
            raise BlockUnavailableError(
                f"ollama generate_stream failed ({self.model} @ {self.base_url}, http {exc.code})"
            ) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise BlockUnavailableError(f"stream request to {url} failed: {exc}") from exc
        except ValueError as exc:  # json parse of a stream line
            raise BlockUnavailableError(f"unparseable stream line from {url}: {exc}") from exc

    def health(self) -> HealthStatus:
        try:
            status, data = _request_json(f"{self.base_url}/api/tags", timeout=3.0)
        except BlockUnavailableError as exc:
            return HealthStatus(healthy=False, detail=f"ollama unreachable at {self.base_url}: {exc}")
        if status != 200:
            return HealthStatus(healthy=False, detail=f"ollama /api/tags http {status}")
        names = [m.get("name", "") for m in (data or {}).get("models", [])] if isinstance(data, dict) else []
        if names and not any(n.startswith(self.model) for n in names):
            return HealthStatus(healthy=False, detail=f"model {self.model!r} not pulled; have {names}")
        return HealthStatus(healthy=True, detail=f"ollama @ {self.base_url}, model={self.model}")


@dataclass
class OpenAICompatModel:
    """Any OpenAI-compatible /v1/chat/completions endpoint.

    kind "ollama-cloud": base_url https://ollama.com, Bearer api key required —
    cloud models like gemma4:31b-cloud run server-side at ollama.com.
    kind "openai-compatible": DeepSeek (https://api.deepseek.com/v1 — base_url
    includes /v1), Groq, OpenRouter, vLLM, llama.cpp server, ...
    """

    model: str
    base_url: str = "https://ollama.com"
    api_key: str | None = None
    block_id: str = "openai-compatible"
    capabilities: ModelCapabilities = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.capabilities is None:
            self.capabilities = ModelCapabilities(prompt_cache=True, tool_calling=True, streaming=True)

    def _endpoints(self) -> tuple[str, str]:
        base = self.base_url.rstrip("/")
        if base.endswith("/v1"):  # caller already gave an OpenAI-style base
            return f"{base}/chat/completions", f"{base}/models"
        return f"{base}/v1/chat/completions", f"{base}/v1/models"

    def generate(self, run, prompt: str) -> ModelResult:
        return self._generate(run, prompt)

    def generate_with_tools(self, run, prompt: str, tools: list) -> ModelResult:
        """Native OpenAI function calling: ToolDescriptors become `tools`, response
        tool_calls become contracts ToolCall proposals (still unauthorized until policy)."""
        payload_tools = [
            {
                "type": "function",
                "function": {
                    "name": d.kind,
                    "description": d.description or d.name,
                    "parameters": d.params_schema or {"type": "object", "properties": {}},
                },
            }
            for d in tools
        ]
        return self._generate(run, prompt, tools=payload_tools, parse_tool_calls=True)

    def _generate(self, run, prompt: str, tools: list | None = None, parse_tool_calls: bool = False) -> ModelResult:
        run.check_alive()
        chat_url, _ = self._endpoints()
        body: dict = {"model": self.model, "messages": [{"role": "user", "content": prompt}]}
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        status, data = _request_json(chat_url, body=body, headers=_bearer(self.api_key))
        run.check_alive()

        message: dict = {}
        if isinstance(data, dict):
            choices = data.get("choices") or []
            if choices:
                message = choices[0].get("message", {}) or {}
        text = str(message.get("content", "")).strip()

        tool_calls: tuple[ToolCall, ...] = ()
        if parse_tool_calls and message.get("tool_calls"):
            calls = []
            for call in message["tool_calls"]:
                function = call.get("function", {}) or {}
                try:
                    params = json.loads(function.get("arguments") or "{}")
                except ValueError:
                    params = {"_unparseable_arguments": str(function.get("arguments"))[:200]}
                calls.append(ToolCall(kind=str(function.get("name", "")), params=params))
            tool_calls = tuple(calls)

        if status != 200 or (not text and not tool_calls):
            raise BlockUnavailableError(
                f"{self.block_id} generate failed ({self.model}, http {status}): {data}"
            )
        usage = (data.get("usage") or {}) if isinstance(data, dict) else {}
        return ModelResult(
            text=text,
            input_tokens=int(usage.get("prompt_tokens", 0)),
            output_tokens=int(usage.get("completion_tokens", 0)),
            finish_reason="tool_calls" if tool_calls else "stop",
            cached_tokens=int(usage.get("prompt_tokens_details", {}).get("cached_tokens", 0))
            if isinstance(usage.get("prompt_tokens_details"), dict) else 0,
            tool_calls=tool_calls,
        )

    def generate_stream(self, run, prompt: str) -> Iterator[str]:
        """Optional streaming capability (StreamingModel): server-sent events from
        the chat endpoint with stream=true. Yields non-empty choices[0].delta.content
        fragments until the terminal "data: [DONE]"; HTTP/network/parse errors
        normalize to BlockUnavailableError."""
        run.check_alive()
        chat_url, _ = self._endpoints()
        req = urllib.request.Request(
            chat_url,
            data=json.dumps({
                "model": self.model,
                "messages": [{"role": "user", "content": prompt}],
                "stream": True,
            }).encode("utf-8"),
            headers={"Content-Type": "application/json", **_bearer(self.api_key)},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT_S) as resp:
                for raw_line in resp:  # http response objects iterate line by line
                    line = raw_line.decode("utf-8", "replace").strip("\r\n")
                    if not line.startswith("data:"):
                        continue
                    payload = line[len("data:"):].strip()
                    if payload == "[DONE]":
                        return
                    if not payload:
                        continue
                    chunk = json.loads(payload)
                    choices = chunk.get("choices") or [] if isinstance(chunk, dict) else []
                    if not choices:
                        continue
                    delta = (choices[0].get("delta", {}) or {}).get("content")
                    if delta:
                        yield str(delta)
        except urllib.error.HTTPError as exc:
            raise BlockUnavailableError(
                f"{self.block_id} generate_stream failed ({self.model}, http {exc.code})"
            ) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise BlockUnavailableError(f"stream request to {chat_url} failed: {exc}") from exc
        except ValueError as exc:  # json parse of an SSE data payload
            raise BlockUnavailableError(f"unparseable stream event from {chat_url}: {exc}") from exc

    def health(self) -> HealthStatus:
        _, models_url = self._endpoints()
        try:
            status, data = _request_json(models_url, headers=_bearer(self.api_key), timeout=5.0)
        except BlockUnavailableError as exc:
            return HealthStatus(healthy=False, detail=f"{self.block_id} unreachable: {exc}")
        if status in (401, 403):
            return HealthStatus(healthy=False, detail=f"{self.block_id}: invalid or missing api key (http {status})")
        if status != 200:
            return HealthStatus(healthy=False, detail=f"{self.block_id} /models http {status}")
        return HealthStatus(healthy=True, detail=f"{self.block_id} @ {self.base_url}, model={self.model}")
