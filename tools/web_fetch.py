"""WebFetchTool brick: ToolProvider v1, kind="web_fetch" (read-only).

Fetches an http(s) URL and returns projected text — same projection discipline
as the browser block (strip tags, collapse whitespace, truncate): the model
never receives raw HTML. Read-only, so there is nothing to verify; policy still
evaluates every web_fetch intent (constitution: the model never authorizes
itself, even for reads).
"""

from __future__ import annotations

import re
import urllib.error
import urllib.request
from dataclasses import dataclass

from contracts.action import ActionIntent
from contracts.health import HealthStatus
from contracts.tool import ToolDescriptor, ToolResult

WEB_FETCH_PARAMS_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "url": {"type": "string", "description": "absolute http(s) URL to fetch"},
        "max_chars": {"type": "integer", "description": "projection budget (default 4000)"},
    },
    "required": ["url"],
}

_TAG_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
_HEAD_RE = re.compile(r"<head[^>]*>.*?</head>", re.IGNORECASE | re.DOTALL)
_ANY_TAG_RE = re.compile(r"<[^>]+>")
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_WS_RE = re.compile(r"\s+")

MAX_FETCH_BYTES = 1_000_000


@dataclass
class WebFetchTool:
    block_id: str = "tool.web_fetch"
    default_max_chars: int = 4000

    def describe(self) -> list[ToolDescriptor]:
        return [
            ToolDescriptor(
                kind="web_fetch",
                name="web_fetch",
                risk_class="normal",
                description="Fetch a web page and return its readable text (read-only).",
                params_schema=WEB_FETCH_PARAMS_SCHEMA,
            )
        ]

    def execute(self, run, intent: ActionIntent) -> ToolResult:
        run.check_alive()
        if intent.kind != "web_fetch":
            from contracts.errors import ContractViolationError

            raise ContractViolationError(f"{self.block_id}: cannot execute intent kind {intent.kind!r}")
        url = str(intent.params.get("url", ""))
        max_chars = int(intent.params.get("max_chars", self.default_max_chars))
        if not (url.startswith("http://") or url.startswith("https://")):
            return ToolResult(ok=False, action_id=intent.action_id, kind=intent.kind,
                              outputs={}, error=f"unsupported URL scheme: {url!r}")

        try:
            req = urllib.request.Request(url, headers={"User-Agent": "ULTRON/0.1 web_fetch"})
            with urllib.request.urlopen(req, timeout=30.0) as resp:
                raw = resp.read(MAX_FETCH_BYTES).decode("utf-8", errors="replace")
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            return ToolResult(ok=False, action_id=intent.action_id, kind=intent.kind,
                              outputs={}, error=f"fetch failed for {url}: {exc}")

        run.check_alive()
        title_match = _TITLE_RE.search(raw)
        title = _WS_RE.sub(" ", title_match.group(1)).strip() if title_match else url
        body = _ANY_TAG_RE.sub(" ", _HEAD_RE.sub(" ", _TAG_RE.sub(" ", raw)))
        text = _WS_RE.sub(" ", body).strip()[:max_chars]
        return ToolResult(
            ok=True, action_id=intent.action_id, kind=intent.kind,
            outputs={"url": url, "title": title, "text": text, "truncated": len(body) > max_chars},
        )

    def health(self) -> HealthStatus:
        return HealthStatus(healthy=True, detail=f"{self.block_id}: stdlib urllib, read-only")
