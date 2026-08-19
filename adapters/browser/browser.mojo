# adapters.browser.browser — Browser v1 implementation boundary.

from std.collections import List
from adapters.browser.contract import (
    BrowserProvider,
    CancellationToken,
    CancelledError,
    HealthStatus,
    PageProjection,
)


comptime CONTRACT_ID = "browser_provider"
comptime CONTRACT_VERSION = "1.0.0"


struct BrowserError(Copyable, Movable, Writable):
    var message: String
    var retryable: Bool

    def __init__(out self, message: String, retryable: Bool = True):
        self.message = message
        self.retryable = retryable

    def write_to(self, mut writer: Some[Writer]):
        writer.write("BrowserError('", self.message, "', retryable=", self.retryable, ")")


struct BrowserUnavailableError(Copyable, Movable, Writable):
    var message: String

    def __init__(out self, message: String = "browser backend is unavailable"):
        self.message = message

    def write_to(self, mut writer: Some[Writer]):
        writer.write("BrowserUnavailableError('", self.message, "')")


struct BrowserTimeout(Copyable, Movable, Writable):
    var message: String

    def __init__(out self, message: String = "browser navigation timed out"):
        self.message = message

    def write_to(self, mut writer: Some[Writer]):
        writer.write("BrowserTimeout('", self.message, "')")


struct InvalidURLError(Copyable, Movable, Writable):
    var message: String

    def __init__(out self, message: String = "only absolute http and https URLs are supported"):
        self.message = message

    def write_to(self, mut writer: Some[Writer]):
        writer.write("InvalidURLError('", self.message, "')")


struct BrowserCapabilities(Copyable, Movable, Writable):
    var values: List[String]

    def __init__(out self):
        self.values = List[String]()
        self.values.append(String("navigation"))
        self.values.append(String("extraction"))
        self.values.append(String("timeout"))
        self.values.append(String("cancellation"))
        self.values.append(String("health"))

    def has(self, capability: String) -> Bool:
        for value in self.values:
            if value == capability:
                return True
        return False


trait BrowserBackend(Deinitable):
    def visit(
        mut self,
        url: String,
        timeout_ms: Int,
        token: CancellationToken,
    ) raises -> PageProjection:
        ...

    def health(self) -> HealthStatus:
        ...

    def close(mut self):
        pass


struct UnavailableBrowserBackend(BrowserBackend):
    var detail: String

    def __init__(out self, detail: String = "no Chromium-compatible runtime is configured"):
        self.detail = detail

    def visit(
        mut self,
        url: String,
        timeout_ms: Int,
        token: CancellationToken,
    ) raises -> PageProjection:
        raise BrowserUnavailableError(self.detail)

    def health(self) -> HealthStatus:
        return HealthStatus(False, self.detail)


struct BrowserAdapter[B: BrowserBackend & Movable](BrowserProvider):
    var backend: Self.B
    var max_text_chars: Int

    def __init__(out self, var backend: Self.B, max_text_chars: Int = 12000):
        self.backend = backend^
        self.max_text_chars = max_text_chars

    def capabilities(self) -> BrowserCapabilities:
        return BrowserCapabilities()

    def block_id(self) -> String:
        return String(CONTRACT_ID)

    def visit(
        mut self,
        url: String,
        timeout_s: Float64,
        token: CancellationToken,
    ) raises -> PageProjection:
        if not _valid_url(url):
            raise InvalidURLError()
        if timeout_s <= 0.0:
            raise BrowserTimeout("timeout must be positive")
        token.check()
        var timeout_ms = Int(timeout_s * 1000.0)
        var projection = self.backend.visit(url, timeout_ms, token)
        token.check()
        if projection.text.byte_length() > self.max_text_chars:
            projection.text = _prefix(projection.text, self.max_text_chars)
            projection.truncated = True
        return projection^

    def health(self) -> HealthStatus:
        return self.backend.health()

    def close(mut self):
        self.backend.close()


def _valid_url(url: String) -> Bool:
    var parts = url.split("://")
    if len(parts) != 2 or parts[1].byte_length() == 0:
        return False
    var scheme = String(parts[0])
    return scheme == "http" or scheme == "https"


def _prefix(value: String, limit: Int) -> String:
    var result = String("")
    var count = 0
    for character in value.codepoint_slices():
        if count >= limit:
            break
        result += String(character)
        count += 1
    return result