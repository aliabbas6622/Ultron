# adapters.model.ollama — Ollama adapter (local OpenAI-compatible endpoint).
#
# Required capabilities: text_generation, cancellation, health, usage_reporting.
# Optional capabilities: streaming.
#
# Ollama exposes an OpenAI-compatible /v1/chat/completions endpoint.
# Uses Python FFI (httpx) for HTTP since Mojo has no built-in HTTP client.
#
# Do not couple routing to one local inference engine (tech stack principle).

from adapters.model.contract import (
    CancellationToken,
    ProviderError,
    ProviderTimeout,
    CancelledError,
)
from adapters.model.models import (
    Capability,
    HealthReport,
    HealthStatus,
    ModelRequest,
    ModelResponse,
    UsageReport,
)
from std.collections import Dict, List
from std.python import Python, PythonObject


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

struct OllamaConfig(Copyable, Movable):
    """Ollama provider configuration.

    Ollama runs locally — no API key required.
    base_url defaults to the standard Ollama OpenAI-compatible endpoint.
    """

    var base_url: String
    var model: String
    var timeout_seconds: Float64
    var max_retries: Int

    def __init__(out self):
        self.base_url = String("http://localhost:11434")
        self.model = String("llama3.2")
        self.timeout_seconds = 60.0
        self.max_retries = 1

    def __init__(
        out self,
        var base_url: String,
        var model: String,
        timeout_seconds: Float64,
        max_retries: Int,
    ):
        self.base_url = base_url^
        self.model = model^
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries


# ---------------------------------------------------------------------------
# Provider implementation
# ---------------------------------------------------------------------------

struct OllamaProvider:
    """Ollama model provider — local OpenAI-compatible text generation.

    Conforms to ModelProvider v1:
        required: text_generation, cancellation, health, usage_reporting
        optional: streaming
    """

    var _config: OllamaConfig
    var _client: PythonObject
    var _connected: Bool

    def __init__(out self, var config: OllamaConfig):
        self._config = config^
        self._client = PythonObject(None)
        self._connected = False

    def __init__(out self, var model: String):
        var config = OllamaConfig()
        config.model = model^
        self._config = config^
        self._client = PythonObject(None)
        self._connected = False

    # -- capability advertisement -------------------------------------------

    def declared_capabilities(self) -> List[Capability]:
        var caps = List[Capability]()
        caps.append(Capability.text_generation())
        caps.append(Capability.cancellation())
        caps.append(Capability.health())
        caps.append(Capability.usage_reporting())
        caps.append(Capability.streaming())
        return caps^

    def has_capability(self, cap: Capability) -> Bool:
        var caps = self.declared_capabilities()
        for i in range(len(caps)):
            if caps[i] == cap:
                return True
        return False

    # -- lifecycle ----------------------------------------------------------

    def connect(mut self) raises:
        """Create httpx client pointing at local Ollama."""
        if self._connected:
            return

        var httpx = Python.import_module("httpx")

        var headers = Python.dict()
        headers["Content-Type"] = "application/json"

        self._client = httpx.Client(
            base_url=self._config.base_url,
            headers=headers,
            timeout=self._config.timeout_seconds,
        )
        self._connected = True

    def close(mut self):
        """Close httpx client."""
        if self._connected:
            try:
                self._client.close()
            except:
                pass
            self._connected = False

    # -- required: text generation ------------------------------------------

    def text_generation(
        self,
        request: ModelRequest,
        token: CancellationToken,
    ) raises -> ModelResponse:
        """Generate text via Ollama API (OpenAI-compatible endpoint).

        Checks cancellation before and during the request.
        Populates UsageReport on every call, even on partial results.
        """
        token.check()

        if not self._connected:
            raise ProviderError("provider not connected — call connect() first")

        var time = Python.import_module("time")

        # Build messages list
        var messages = Python.list()
        if request.system.byte_length() > 0:
            messages.append(
                Python.dict(role="system", content=request.system)
            )
        messages.append(
            Python.dict(role="user", content=request.prompt)
        )

        # Build request body
        var body = Python.dict()
        body["model"] = self._config.model
        body["messages"] = messages
        body["max_tokens"] = request.max_tokens
        body["temperature"] = request.temperature
        if len(request.stop) > 0:
            var stop_list = Python.list()
            for i in range(len(request.stop)):
                stop_list.append(request.stop[i])
            body["stop"] = stop_list

        # Execute with retries
        var last_error = String("")
        var retries = 0

        for attempt in range(self._config.max_retries + 1):
            token.check()

            try:
                var start_time = time.time()
                var response = self._client.post(
                    "/v1/chat/completions", json=body
                )
                var raw_ms = (time.time() - start_time) * 1000.0
                var elapsed_ms = Float64(py=raw_ms)

                token.check()

                if response.status_code == 200:
                    var data = response.json()

                    var text = String("")
                    var finish_reason = String("stop")
                    var model_used = String(self._config.model)

                    if len(data["choices"]) > 0:
                        var choice = data["choices"][0]
                        text = String(choice["message"]["content"])
                        finish_reason = String(
                            choice.get("finish_reason", "stop")
                        )

                    if "model" in data:
                        model_used = String(data["model"])

                    var usage = UsageReport()
                    if "usage" in data:
                        var usage_data = data["usage"]
                        usage = UsageReport(
                            input_tokens=Int(String(usage_data.get("prompt_tokens", 0))),
                            output_tokens=Int(String(usage_data.get("completion_tokens", 0))),
                            total_tokens=Int(String(usage_data.get("total_tokens", 0))),
                            cached_tokens=0,
                            cost_usd=0.0,
                            latency_ms=elapsed_ms,
                            retries=retries,
                            model=model_used,
                        )
                    else:
                        usage = UsageReport(
                            input_tokens=0,
                            output_tokens=0,
                            total_tokens=0,
                            cached_tokens=0,
                            cost_usd=0.0,
                            latency_ms=elapsed_ms,
                            retries=retries,
                            model=model_used,
                        )

                    return ModelResponse(
                        request_id=request.request_id,
                        text=text,
                        finish_reason=finish_reason,
                        model=model_used,
                        usage=usage^,
                    )

                elif response.status_code == 408 or response.status_code == 504:
                    raise ProviderTimeout(
                        "request timed out (HTTP "
                        + String(response.status_code)
                        + ")"
                    )

                elif response.status_code == 429:
                    last_error = "rate limited (HTTP 429)"
                    retries += 1
                    continue

                else:
                    raise ProviderError(
                        "API error: HTTP "
                        + String(response.status_code)
                        + " — "
                        + String(response.text),
                        retryable=False,
                    )

            except e:
                last_error = String(e)
                retries += 1
                continue

        raise ProviderError(
            "all retries exhausted: " + last_error, retryable=False
        )

    # -- required: health ---------------------------------------------------

    def health(self) -> HealthReport:
        """Fast health check — hits /v1/models endpoint.

        Must NOT raise. Return HealthStatus.UNHEALTHY on failure.
        """
        if not self._connected:
            return HealthReport(
                status=HealthStatus.unhealthy(),
                message="provider not connected",
                latency_ms=0.0,
            )

        try:
            var time = Python.import_module("time")
            var start_time = time.time()
            var response = self._client.get("/v1/models")
            var raw_ms = (time.time() - start_time) * 1000.0
            var elapsed_ms = Float64(py=raw_ms)

            if response.status_code == 200:
                var data = response.json()
                var model_available = False
                if "data" in data:
                    var models = data["data"]
                    for i in range(len(models)):
                        var m = models[i]
                        if String(m.get("id", "")) == self._config.model:
                            model_available = True
                            break

                if model_available:
                    return HealthReport(
                        status=HealthStatus.healthy(),
                        message="model '"
                        + self._config.model
                        + "' available",
                        latency_ms=elapsed_ms,
                    )
                else:
                    return HealthReport(
                        status=HealthStatus.degraded(),
                        message="model '"
                        + self._config.model
                        + "' not found locally",
                        latency_ms=elapsed_ms,
                    )
            else:
                return HealthReport(
                    status=HealthStatus.unhealthy(),
                    message="HTTP " + String(response.status_code),
                    latency_ms=elapsed_ms,
                )
        except e:
            return HealthReport(
                status=HealthStatus.unhealthy(),
                message=String(e),
                latency_ms=0.0,
            )
