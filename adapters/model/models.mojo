# adapters.model.models — Typed data structures for the ModelProvider v1 contract.
#
# All fields use concrete types. No arbitrary dict/map payloads.
# Typed internal structures over prose (coding standard #8).

from std.collections import Dict, List


# ---------------------------------------------------------------------------
# Capability advertisement
# ---------------------------------------------------------------------------
# Routers must query declared_capabilities rather than assume them.

struct Capability(Copyable, Movable, Writable, Hashable, Equatable):
    """Capabilities a ModelProvider may advertise."""

    var value: String

    def __init__(out self, value: String):
        self.value = value

    # Required
    @staticmethod
    def text_generation() -> Self:
        return Self(value="text_generation")

    @staticmethod
    def cancellation() -> Self:
        return Self(value="cancellation")

    @staticmethod
    def health() -> Self:
        return Self(value="health")

    @staticmethod
    def usage_reporting() -> Self:
        return Self(value="usage_reporting")

    # Optional
    @staticmethod
    def streaming() -> Self:
        return Self(value="streaming")

    @staticmethod
    def vision() -> Self:
        return Self(value="vision")

    @staticmethod
    def audio() -> Self:
        return Self(value="audio")

    @staticmethod
    def tool_calling() -> Self:
        return Self(value="tool_calling")

    @staticmethod
    def embeddings() -> Self:
        return Self(value="embeddings")

    @staticmethod
    def prompt_cache() -> Self:
        return Self(value="prompt_cache")

    @staticmethod
    def structured_output() -> Self:
        return Self(value="structured_output")

    def __eq__(self, other: Self) -> Bool:
        return self.value == other.value

    def __ne__(self, other: Self) -> Bool:
        return self.value != other.value

    def __hash__(self) -> Int:
        return Int(hash(self.value))

    def write_to(self, mut writer: Some[Writer]):
        writer.write("Capability(", self.value, ")")


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

struct HealthStatus(Copyable, Movable, Writable, Equatable):
    """Provider health status."""

    var value: String

    def __init__(out self, value: String):
        self.value = value

    @staticmethod
    def healthy() -> Self:
        return Self(value="healthy")

    @staticmethod
    def degraded() -> Self:
        return Self(value="degraded")

    @staticmethod
    def unhealthy() -> Self:
        return Self(value="unhealthy")

    def __eq__(self, other: Self) -> Bool:
        return self.value == other.value

    def __ne__(self, other: Self) -> Bool:
        return self.value != other.value

    def write_to(self, mut writer: Some[Writer]):
        writer.write("HealthStatus(", self.value, ")")

    def is_healthy(self) -> Bool:
        return self.value == "healthy"

    def is_unhealthy(self) -> Bool:
        return self.value == "unhealthy"


struct HealthReport(Copyable, Movable, Writable):
    """Snapshot of provider health at a point in time."""

    var status: HealthStatus
    var message: String
    var latency_ms: Float64

    def __init__(out self):
        self.status = HealthStatus.healthy()
        self.message = String("")
        self.latency_ms = 0.0

    def __init__(
        out self,
        var status: HealthStatus,
        message: String,
        latency_ms: Float64,
    ):
        self.status = status^
        self.message = message
        self.latency_ms = latency_ms

    def write_to(self, mut writer: Some[Writer]):
        writer.write(
            "HealthReport(status=",
            self.status,
            ", message='",
            self.message,
            "', latency_ms=",
            self.latency_ms,
            ")",
        )


# ---------------------------------------------------------------------------
# Request / Response
# ---------------------------------------------------------------------------

struct ModelRequest(Copyable, Movable, Writable):
    """A single text-generation request.

    Cancellation is architectural — the runtime passes a CancellationToken
    to the provider. The request carries metadata (run_id, trace_id, budget)
    for observability.
    """

    var request_id: String
    var prompt: String
    var system: String
    var max_tokens: Int
    var temperature: Float64
    var stop: List[String]

    def __init__(out self):
        self.request_id = String("")
        self.prompt = String("")
        self.system = String("")
        self.max_tokens = 4096
        self.temperature = 0.7
        self.stop = List[String]()

    def __init__(
        out self,
        prompt: String,
        system: String,
        max_tokens: Int,
        temperature: Float64,
    ):
        self.request_id = String("")
        self.prompt = prompt
        self.system = system
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.stop = List[String]()

    def write_to(self, mut writer: Some[Writer]):
        writer.write(
            "ModelRequest(id='",
            self.request_id,
            "', prompt='",
            self.prompt,
            "', max_tokens=",
            self.max_tokens,
            ")",
        )


struct UsageReport(Copyable, Movable, Writable):
    """Token / cost accounting for one request — core observability primitive.

    Measure tokens, latency, cost, retries, and side effects (principle #14).
    """

    var input_tokens: Int
    var output_tokens: Int
    var total_tokens: Int
    var cached_tokens: Int
    var cost_usd: Float64
    var latency_ms: Float64
    var retries: Int
    var model: String

    def __init__(out self):
        self.input_tokens = 0
        self.output_tokens = 0
        self.total_tokens = 0
        self.cached_tokens = 0
        self.cost_usd = 0.0
        self.latency_ms = 0.0
        self.retries = 0
        self.model = String("")

    def __init__(
        out self,
        input_tokens: Int,
        output_tokens: Int,
        total_tokens: Int,
        cached_tokens: Int,
        cost_usd: Float64,
        latency_ms: Float64,
        retries: Int,
        model: String,
    ):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.total_tokens = total_tokens
        self.cached_tokens = cached_tokens
        self.cost_usd = cost_usd
        self.latency_ms = latency_ms
        self.retries = retries
        self.model = model

    def write_to(self, mut writer: Some[Writer]):
        writer.write(
            "UsageReport(input=",
            self.input_tokens,
            ", output=",
            self.output_tokens,
            ", total=",
            self.total_tokens,
            ", cached=",
            self.cached_tokens,
            ", cost_usd=",
            self.cost_usd,
            ", latency_ms=",
            self.latency_ms,
            ", retries=",
            self.retries,
            ", model='",
            self.model,
            "')",
        )


struct ModelResponse(Copyable, Movable, Writable):
    """Result returned by a ModelProvider after text generation."""

    var request_id: String
    var text: String
    var finish_reason: String
    var model: String
    var usage: UsageReport

    def __init__(out self):
        self.request_id = String("")
        self.text = String("")
        self.finish_reason = String("stop")
        self.model = String("")
        self.usage = UsageReport()

    def __init__(
        out self,
        request_id: String,
        text: String,
        finish_reason: String,
        model: String,
        var usage: UsageReport,
    ):
        self.request_id = request_id
        self.text = text
        self.finish_reason = finish_reason
        self.model = model
        self.usage = usage^

    def write_to(self, mut writer: Some[Writer]):
        writer.write(
            "ModelResponse(id='",
            self.request_id,
            "', finish='",
            self.finish_reason,
            "', model='",
            self.model,
            "', text='",
            self.text,
            "')",
        )
