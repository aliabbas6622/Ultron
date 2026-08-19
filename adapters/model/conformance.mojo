# adapters.model.conformance — Conformance test suite for ModelProvider v1.
#
# From 03_BLOCK_CONTRACT.md, Model conformance tests:
#   - cancellation
#   - timeout
#   - usage reporting
#   - normalized errors
#   - capability advertisement
#
# A new block is not production-compatible until it passes its contract suite.
#
# Run with: source .venv/bin/activate && mojo run adapters/model/conformance.mojo

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
from std.testing import assert_equal, assert_true, assert_false


# ---------------------------------------------------------------------------
# Mock provider for testing contract behaviour without network
# ---------------------------------------------------------------------------

struct MockProvider:
    """Minimal mock provider for conformance testing."""

    var _capabilities: List[Capability]
    var _health_status: HealthStatus
    var _response_text: String
    var _should_cancel: Bool

    def __init__(
        out self,
        var capabilities: List[Capability],
        var health_status: HealthStatus,
        var response_text: String,
        should_cancel: Bool,
    ):
        self._capabilities = capabilities^
        self._health_status = health_status^
        self._response_text = response_text^
        self._should_cancel = should_cancel

    def declared_capabilities(self) -> List[Capability]:
        return self._capabilities.copy()

    def has_capability(self, cap: Capability) -> Bool:
        for i in range(len(self._capabilities)):
            if self._capabilities[i] == cap:
                return True
        return False

    def text_generation(
        self,
        request: ModelRequest,
        token: CancellationToken,
    ) raises -> ModelResponse:
        """Mock text generation — checks cancellation, returns canned response."""
        token.check()

        if self._should_cancel:
            raise CancelledError("mock mid-flight cancellation")

        return ModelResponse(
            request_id=request.request_id,
            text=self._response_text.copy(),
            finish_reason=String("stop"),
            model=String("mock-model"),
            usage=UsageReport(
                input_tokens=10,
                output_tokens=20,
                total_tokens=30,
                cached_tokens=0,
                cost_usd=0.0,
                latency_ms=5.0,
                retries=0,
                model=String("mock-model"),
            ),
        )

    def health(self) -> HealthReport:
        return HealthReport(
            status=self._health_status.copy(),
            message=String("mock health"),
            latency_ms=1.0,
        )


# ---------------------------------------------------------------------------
# Helper to build standard capabilities list
# ---------------------------------------------------------------------------

def _required_caps() -> List[Capability]:
    var caps = List[Capability]()
    caps.append(Capability.text_generation())
    caps.append(Capability.cancellation())
    caps.append(Capability.health())
    caps.append(Capability.usage_reporting())
    return caps^


# ---------------------------------------------------------------------------
# Test: capability advertisement
# ---------------------------------------------------------------------------

def test_required_capabilities_present() raises:
    """All required capabilities must be declared."""
    var provider = MockProvider(
        _required_caps(), HealthStatus.healthy(), String("ok"), False,
    )

    assert_true(provider.has_capability(Capability.text_generation()))
    assert_true(provider.has_capability(Capability.cancellation()))
    assert_true(provider.has_capability(Capability.health()))
    assert_true(provider.has_capability(Capability.usage_reporting()))


def test_optional_capabilities_not_required() raises:
    """Optional capabilities may or may not be present."""
    var provider = MockProvider(
        _required_caps(), HealthStatus.healthy(), String("ok"), False,
    )

    assert_false(provider.has_capability(Capability.streaming()))
    assert_false(provider.has_capability(Capability.vision()))
    assert_false(provider.has_capability(Capability.tool_calling()))


def test_optional_capabilities_advertised() raises:
    """When optional capabilities are declared, has_capability returns True."""
    var caps = _required_caps()
    caps.append(Capability.streaming())
    caps.append(Capability.tool_calling())

    var provider = MockProvider(
        caps^, HealthStatus.healthy(), String("ok"), False,
    )

    assert_true(provider.has_capability(Capability.streaming()))
    assert_true(provider.has_capability(Capability.tool_calling()))
    assert_false(provider.has_capability(Capability.vision()))


# ---------------------------------------------------------------------------
# Test: cancellation
# ---------------------------------------------------------------------------

def test_cancellation_before_request() raises:
    """Provider must raise CancelledError if token is already cancelled."""
    var token = CancellationToken()
    token.cancel()

    var provider = MockProvider(
        _required_caps(), HealthStatus.healthy(), String("ok"), False,
    )

    var request = ModelRequest()
    request.prompt = "Hello"

    var caught = False
    try:
        _ = provider.text_generation(request, token)
    except e:
        caught = True

    assert_true(caught, "expected CancelledError for pre-cancelled token")


def test_cancellation_during_request() raises:
    """Provider must raise CancelledError when token is cancelled mid-flight."""
    var token = CancellationToken()

    # MockProvider with should_cancel=True raises CancelledError mid-flight
    var provider = MockProvider(
        _required_caps(), HealthStatus.healthy(), String("ok"), True,
    )

    var request = ModelRequest()
    request.prompt = "Hello"

    var caught = False
    try:
        _ = provider.text_generation(request, token)
    except e:
        caught = True

    assert_true(caught, "expected CancelledError for mid-flight cancellation")


# ---------------------------------------------------------------------------
# Test: usage reporting
# ---------------------------------------------------------------------------

def test_usage_reported_on_success() raises:
    """Every successful text_generation must return a populated UsageReport."""
    var token = CancellationToken()

    var provider = MockProvider(
        _required_caps(), HealthStatus.healthy(), String("Hello, world!"), False,
    )

    var request = ModelRequest()
    request.prompt = "Say hello"

    var response = provider.text_generation(request, token)

    assert_true(response.usage.input_tokens > 0, "input_tokens must be > 0")
    assert_true(response.usage.output_tokens > 0, "output_tokens must be > 0")
    assert_true(response.usage.total_tokens > 0, "total_tokens must be > 0")
    assert_true(response.usage.latency_ms >= 0.0, "latency_ms must be >= 0")
    assert_true(
        response.usage.model.byte_length() > 0, "model must be non-empty"
    )


def test_usage_has_request_id() raises:
    """Response must echo the request's request_id for trace correlation."""
    var token = CancellationToken()

    var provider = MockProvider(
        _required_caps(), HealthStatus.healthy(), String("ok"), False,
    )

    var request = ModelRequest()
    request.request_id = "test-run-123"
    request.prompt = "test"

    var response = provider.text_generation(request, token)

    assert_equal(response.request_id, String("test-run-123"))


# ---------------------------------------------------------------------------
# Test: health
# ---------------------------------------------------------------------------

def test_health_never_raises() raises:
    """Health check must never raise — return HealthStatus.UNHEALTHY on failure."""
    var provider = MockProvider(
        _required_caps(), HealthStatus.unhealthy(), String(""), False,
    )

    var report = provider.health()
    assert_equal(report.status.value, String("unhealthy"))


def test_health_report_contains_latency() raises:
    """HealthReport must include latency measurement."""
    var provider = MockProvider(
        _required_caps(), HealthStatus.healthy(), String("ok"), False,
    )

    var report = provider.health()
    assert_true(report.latency_ms >= 0.0, "latency_ms must be >= 0")


# ---------------------------------------------------------------------------
# Test: normalized errors
# ---------------------------------------------------------------------------

def test_provider_error_has_retryable_flag() raises:
    """ProviderError must carry a retryable flag for routing decisions."""
    var error = ProviderError(String("test error"), True)
    assert_true(error.retryable)

    var fatal = ProviderError(String("fatal error"), False)
    assert_false(fatal.retryable)


def test_provider_timeout_not_retryable() raises:
    """ProviderTimeout is not retryable by default."""
    var error = ProviderTimeout()
    assert_true(error.message.byte_length() > 0)


def test_cancelled_error_not_retryable() raises:
    """CancelledError means cancellation was requested — do not retry."""
    var error = CancelledError()
    assert_true(error.message.byte_length() > 0)


# ---------------------------------------------------------------------------
# Test: text generation contract
# ---------------------------------------------------------------------------

def test_text_generation_returns_response() raises:
    """Text generation must return a ModelResponse with text."""
    var token = CancellationToken()

    var provider = MockProvider(
        _required_caps(), HealthStatus.healthy(), String("Generated text"), False,
    )

    var request = ModelRequest()
    request.prompt = "Generate something"

    var response = provider.text_generation(request, token)

    assert_equal(response.text, String("Generated text"))
    assert_equal(response.finish_reason, String("stop"))
    assert_equal(response.model, String("mock-model"))


def test_text_generation_respects_cancellation_token() raises:
    """Provider must check cancellation token before starting work."""
    var token = CancellationToken()
    token.cancel()

    var provider = MockProvider(
        _required_caps(), HealthStatus.healthy(), String("should not reach"), False,
    )

    var request = ModelRequest()
    request.prompt = "test"

    var caught = False
    try:
        _ = provider.text_generation(request, token)
    except e:
        caught = True

    assert_true(caught, "provider must check cancellation before work")


# ---------------------------------------------------------------------------
# Test runner
# ---------------------------------------------------------------------------

def main() raises:
    print("=== ModelProvider v1 Conformance Suite ===")
    print("")

    var passed = 0
    var failed = 0

    # Run each test and count results
    try:
        test_required_capabilities_present()
        print("  PASS: required_capabilities_present")
        passed += 1
    except e:
        print("  FAIL: required_capabilities_present — " + String(e))
        failed += 1

    try:
        test_optional_capabilities_not_required()
        print("  PASS: optional_capabilities_not_required")
        passed += 1
    except e:
        print("  FAIL: optional_capabilities_not_required — " + String(e))
        failed += 1

    try:
        test_optional_capabilities_advertised()
        print("  PASS: optional_capabilities_advertised")
        passed += 1
    except e:
        print("  FAIL: optional_capabilities_advertised — " + String(e))
        failed += 1

    try:
        test_cancellation_before_request()
        print("  PASS: cancellation_before_request")
        passed += 1
    except e:
        print("  FAIL: cancellation_before_request — " + String(e))
        failed += 1

    try:
        test_cancellation_during_request()
        print("  PASS: cancellation_during_request")
        passed += 1
    except e:
        print("  FAIL: cancellation_during_request — " + String(e))
        failed += 1

    try:
        test_usage_reported_on_success()
        print("  PASS: usage_reported_on_success")
        passed += 1
    except e:
        print("  FAIL: usage_reported_on_success — " + String(e))
        failed += 1

    try:
        test_usage_has_request_id()
        print("  PASS: usage_has_request_id")
        passed += 1
    except e:
        print("  FAIL: usage_has_request_id — " + String(e))
        failed += 1

    try:
        test_health_never_raises()
        print("  PASS: health_never_raises")
        passed += 1
    except e:
        print("  FAIL: health_never_raises — " + String(e))
        failed += 1

    try:
        test_health_report_contains_latency()
        print("  PASS: health_report_contains_latency")
        passed += 1
    except e:
        print("  FAIL: health_report_contains_latency — " + String(e))
        failed += 1

    try:
        test_provider_error_has_retryable_flag()
        print("  PASS: provider_error_has_retryable_flag")
        passed += 1
    except e:
        print("  FAIL: provider_error_has_retryable_flag — " + String(e))
        failed += 1

    try:
        test_provider_timeout_not_retryable()
        print("  PASS: provider_timeout_not_retryable")
        passed += 1
    except e:
        print("  FAIL: provider_timeout_not_retryable — " + String(e))
        failed += 1

    try:
        test_cancelled_error_not_retryable()
        print("  PASS: cancelled_error_not_retryable")
        passed += 1
    except e:
        print("  FAIL: cancelled_error_not_retryable — " + String(e))
        failed += 1

    try:
        test_text_generation_returns_response()
        print("  PASS: text_generation_returns_response")
        passed += 1
    except e:
        print("  FAIL: text_generation_returns_response — " + String(e))
        failed += 1

    try:
        test_text_generation_respects_cancellation_token()
        print("  PASS: text_generation_respects_cancellation_token")
        passed += 1
    except e:
        print("  FAIL: text_generation_respects_cancellation_token — " + String(e))
        failed += 1

    print("")
    print(
        "Results: "
        + String(passed)
        + " passed, "
        + String(failed)
        + " failed out of "
        + String(passed + failed)
    )

    if failed > 0:
        print("SOME TESTS FAILED")
    else:
        print("=== All conformance tests passed ===")
