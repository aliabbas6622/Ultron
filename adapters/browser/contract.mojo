# adapters.browser.contract — Mojo mirror of contracts/browser.py.

from std.collections import List


comptime CONTRACT_ID = "browser_provider"
comptime CONTRACT_VERSION = "1.0.0"


struct CancellationToken:
    var _cancelled: Bool

    def __init__(out self):
        self._cancelled = False

    def cancel(mut self):
        self._cancelled = True

    def is_cancelled(self) -> Bool:
        return self._cancelled

    def check(self) raises:
        if self._cancelled:
            raise CancelledError()


struct CancelledError(Copyable, Movable, Writable):
    var message: String

    def __init__(out self, message: String = "browser navigation cancelled"):
        self.message = message

    def write_to(self, mut writer: Some[Writer]):
        writer.write("CancelledError('", self.message, "')")


struct PageProjection(Copyable, Movable, Writable):
    """Projected page content; raw DOM/HTML is never part of this contract."""

    var url: String
    var title: String
    var text: String
    var truncated: Bool

    def __init__(out self, url: String, title: String, text: String):
        self.url = url
        self.title = title
        self.text = text
        self.truncated = False

    def write_to(self, mut writer: Some[Writer]):
        writer.write(
            "PageProjection(url='", self.url,
            "', title='", self.title,
            "', text_length=", self.text.byte_length(),
            ", truncated=", self.truncated, ")",
        )


struct HealthStatus(Copyable, Movable, Writable):
    var healthy: Bool
    var detail: String

    def __init__(out self, healthy: Bool = True, detail: String = ""):
        self.healthy = healthy
        self.detail = detail

    def write_to(self, mut writer: Some[Writer]):
        writer.write(
            "HealthStatus(healthy=", self.healthy,
            ", detail='", self.detail, "')",
        )


trait BrowserProvider:
    """BrowserProvider v1: navigation, projection, timeout, cancellation, health."""

    def block_id(self) -> String:
        return String(CONTRACT_ID)

    def visit(
        mut self,
        url: String,
        timeout_s: Float64,
        token: CancellationToken,
    ) raises -> PageProjection:
        ...

    def health(self) -> HealthStatus:
        ...