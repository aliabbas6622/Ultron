# Browser v1 conformance suite.

from adapters.browser.browser import (
    BrowserAdapter,
    BrowserCapabilities,
    BrowserUnavailableError,
    BrowserTimeout,
    BrowserBackend,
    CancellationToken,
    CancelledError,
    HealthStatus,
    InvalidURLError,
    PageProjection,
    UnavailableBrowserBackend,
)
from std.testing import assert_equal, assert_true


def main() raises:
    test_capabilities()
    test_navigation_and_projection()
    test_invalid_url()
    test_timeout_and_cancellation()
    test_unavailable_backend_degrades()
    print("browser conformance: 5 passed")


struct MockBackend(BrowserBackend):
    var delay_ms: Int

    def __init__(out self, delay_ms: Int = 0):
        self.delay_ms = delay_ms

    def visit(
        mut self,
        url: String,
        timeout_ms: Int,
        token: CancellationToken,
    ) raises -> PageProjection:
        token.check()
        if self.delay_ms > timeout_ms:
            raise BrowserTimeout()
        var page = PageProjection(url, String("Example"), String("Example page"))
        return page^

    def health(self) -> HealthStatus:
        return HealthStatus(True, String("mock backend"))


def test_capabilities() raises:
    var capabilities = BrowserCapabilities()
    assert_true(capabilities.has(String("navigation")))
    assert_true(capabilities.has(String("extraction")))
    assert_true(capabilities.has(String("cancellation")))
    assert_true(capabilities.has(String("health")))


def test_navigation_and_projection() raises:
    var adapter = BrowserAdapter(MockBackend())
    var page = adapter.visit(String("https://example.com"), 1.0, CancellationToken())
    assert_equal(page.title, String("Example"))
    assert_equal(page.text, String("Example page"))


def test_invalid_url() raises:
    var adapter = BrowserAdapter(MockBackend())
    var caught = False
    try:
        _ = adapter.visit(String("file:///secret"), 1.0, CancellationToken())
    except e:
        caught = True
    assert_true(caught)


def test_timeout_and_cancellation() raises:
    var slow = BrowserAdapter(MockBackend(1001))
    var timed_out = False
    try:
        _ = slow.visit(String("https://example.com"), 1.0, CancellationToken())
    except e:
        timed_out = True
    assert_true(timed_out)

    var token = CancellationToken()
    token.cancel()
    var adapter = BrowserAdapter(MockBackend())
    var cancelled = False
    try:
        _ = adapter.visit(String("https://example.com"), 1.0, token)
    except e:
        cancelled = True
    assert_true(cancelled)


def test_unavailable_backend_degrades() raises:
    var adapter = BrowserAdapter(UnavailableBrowserBackend())
    var status = adapter.health()
    assert_true(not status.healthy)

    var failed = False
    try:
        _ = adapter.visit(String("https://example.com"), 1.0, CancellationToken())
    except e:
        failed = True
    assert_true(failed)