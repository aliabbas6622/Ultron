# adapters.model.contract — ModelProvider v1 contract (abstract trait).
#
# Every implementation MUST satisfy:
#   text_generation, cancellation, health, usage_reporting
#
# Implementations advertise optional capabilities via declared_capabilities().
# Routers query declared_capabilities() — never assume capability presence.
#
# Failure semantics:
#   - ProviderError        → non-fatal, caller may retry within budget
#   - ProviderTimeout      → request exceeded deadline, caller decides retry
#   - CancelledError       → cancellation was requested, caller should not retry
#
# Health semantics:
#   - health() must be fast and side-effect-free.
#   - A failing health() returns HealthStatus.UNHEALTHY, never raises.
#
# State ownership:
#   - The provider owns connection state, auth tokens, retry state.
#   - The caller owns request lifecycle and cancellation tokens.
#
# Cold swap support:
#   - Stop using old impl → configure replacement → rebind.
#   - System remains behaviourally valid.
#
# Hot swap: NOT declared in v1.

from adapters.model.models import (
    Capability,
    HealthReport,
    HealthStatus,
    ModelRequest,
    ModelResponse,
    UsageReport,
)
from std.collections import Dict, List


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

struct ProviderError(Copyable, Movable, Writable):
    """Non-fatal provider failure — caller may retry within budget."""

    var message: String
    var retryable: Bool

    def __init__(out self, message: String):
        self.message = message
        self.retryable = True

    def __init__(out self, message: String, retryable: Bool):
        self.message = message
        self.retryable = retryable

    def write_to(self, mut writer: Some[Writer]):
        writer.write(
            "ProviderError('",
            self.message,
            "', retryable=",
            self.retryable,
            ")",
        )


struct ProviderTimeout(Copyable, Movable, Writable):
    """Request exceeded deadline. Not retryable by default."""

    var message: String

    def __init__(out self):
        self.message = String("request timed out")

    def __init__(out self, message: String):
        self.message = message

    def write_to(self, mut writer: Some[Writer]):
        writer.write("ProviderTimeout('", self.message, "')")


struct CancelledError(Copyable, Movable, Writable):
    """Cancellation was requested. Caller should not retry."""

    var message: String

    def __init__(out self):
        self.message = String("request cancelled")

    def __init__(out self, message: String):
        self.message = message

    def write_to(self, mut writer: Some[Writer]):
        writer.write("CancelledError('", self.message, "')")


# ---------------------------------------------------------------------------
# Cancellation token
# ---------------------------------------------------------------------------
# Cancellation is an architectural primitive, not a later UI feature.
# Providers MUST check is_cancelled before / during long work and raise
# CancelledError when set.

struct CancellationToken:
    """Cooperative cancellation primitive.

    Usage:
        token = CancellationToken()
        # ... in a task runner ...
        token.cancel()
        # ... inside provider ...
        if token.is_cancelled:
            raise CancelledError()
    """

    var _cancelled: Bool

    def __init__(out self):
        self._cancelled = False

    def is_cancelled(self) -> Bool:
        return self._cancelled

    def cancel(mut self):
        """Request cancellation."""
        self._cancelled = True

    def check(self) raises:
        """Raise CancelledError if cancellation was requested."""
        if self._cancelled:
            raise CancelledError()


# ---------------------------------------------------------------------------
# Abstract trait: ModelProvider
# ---------------------------------------------------------------------------

trait ModelProvider:
    """ModelProvider v1 — abstract trait.

    Implementors MUST:
        1. Implement all four required methods.
        2. Check token.is_cancelled at cancellation-sensitive points.
        3. Return UsageReport from text_generation (even on partial results).
        4. Never raise from health() — return HealthStatus.UNHEALTHY instead.

    Implementors MAY:
        - Override declared_capabilities() to add optional capabilities.
    """

    # -- capability advertisement -------------------------------------------

    def declared_capabilities(self) -> List[Capability]:
        """Return the set of capabilities this provider supports.

        Default: the four required capabilities only.
        """
        var caps = List[Capability]()
        caps.append(Capability.text_generation())
        caps.append(Capability.cancellation())
        caps.append(Capability.health())
        caps.append(Capability.usage_reporting())
        return caps^

    def has_capability(self, cap: Capability) -> Bool:
        """Check whether a capability is declared."""
        var caps = self.declared_capabilities()
        for i in range(len(caps)):
            if caps[i] == cap:
                return True
        return False

    # -- required: text generation ------------------------------------------

    def text_generation(
        self,
        request: ModelRequest,
        token: CancellationToken,
    ) raises -> ModelResponse:
        """Generate text from a prompt.

        Must:
            - Respect token.is_cancelled.
            - Return a ModelResponse with a populated UsageReport.
            - Raise CancelledError if cancellation was requested mid-flight.
            - Raise ProviderError / ProviderTimeout on failure.
        """
        ...

    # -- required: health ---------------------------------------------------

    def health(self) -> HealthReport:
        """Fast, side-effect-free health check.

        Must NOT raise. Return HealthStatus.UNHEALTHY on failure.
        """
        ...

    # -- lifecycle ----------------------------------------------------------

    def connect(mut self) raises:
        """Bind to backend (open connection pool, validate auth, etc.).

        Called once after construction. Default: no-op.
        """
        pass

    def close(mut self):
        """Release resources. Called during cold swap teardown or shutdown.

        Default: no-op.
        """
        pass
