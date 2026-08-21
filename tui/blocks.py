"""Demo blocks for the TUI: a real (stdlib-only) HTTP browser and a stub extractive
"model" fallback. Real model bricks (Ollama local/cloud, OpenAI-compatible
endpoints) live in providers/ behind the registry — select_model() picks the
configured default. None of these are the vendor Mojo adapters in adapters/model. ponytail: regex HTML stripping, not a real parser — good
enough for the V0.1 "one sentence" projection, swap for html.parser if a
page's structure actually matters.
"""

from __future__ import annotations

import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

from contracts.browser import HealthStatus as BrowserHealth
from contracts.browser import PageProjection
from contracts.model import HealthStatus as ModelHealth
from contracts.model import ModelCapabilities, ModelResult
from core.errors import BlockUnavailableError
from core.run_context import RunContext

_TAG_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
_HEAD_RE = re.compile(r"<head[^>]*>.*?</head>", re.IGNORECASE | re.DOTALL)
_ANY_TAG_RE = re.compile(r"<[^>]+>")
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_WS_RE = re.compile(r"\s+")


@dataclass
class HttpBrowser:
    """Real HTTP fetch + naive text projection. No JS execution, no Chromium."""

    block_id: str = "http_stub"

    def visit(self, run: RunContext, url: str, timeout_s: float) -> PageProjection:
        run.check_alive()
        if not (url.startswith("http://") or url.startswith("https://")):
            raise ValueError(f"unsupported scheme: {url}")
        try:
            with urllib.request.urlopen(url, timeout=timeout_s) as resp:
                raw = resp.read(1_000_000).decode("utf-8", errors="replace")
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise BlockUnavailableError(f"fetch failed for {url}: {exc}") from exc

        run.check_alive()
        title_match = _TITLE_RE.search(raw)
        title = _WS_RE.sub(" ", title_match.group(1)).strip() if title_match else url
        body = _TAG_RE.sub(" ", raw)
        body = _HEAD_RE.sub(" ", body)
        body = _ANY_TAG_RE.sub(" ", body)
        text = _WS_RE.sub(" ", body).strip()
        truncated = len(text) > 4000
        return PageProjection(url=url, title=title, text=text[:4000], truncated=truncated)

    def health(self) -> BrowserHealth:
        return BrowserHealth(healthy=True, detail="stdlib urllib, no JS/Chromium")


@dataclass
class StubSummarizerModel:
    """Extractive one-sentence 'summary' — first sentence of the page text. Not an LLM.
    Placeholder until adapters/model is bridged to Python (see Collab/ for status)."""

    block_id: str = "extractive_stub"
    capabilities: ModelCapabilities = ModelCapabilities()

    def generate(self, run: RunContext, prompt: str) -> ModelResult:
        run.check_alive()
        time.sleep(0.15)  # ponytail: fake latency so the TUI's "running" state is visible
        marker = "Page content:\n"
        idx = prompt.find(marker)
        text = prompt[idx + len(marker) :] if idx != -1 else prompt
        sentence_end = re.search(r"[.!?](\s|$)", text)
        sentence = text[: sentence_end.end()].strip() if sentence_end else text[:160].strip()
        if not sentence:
            sentence = "(no extractable content)"
        return ModelResult(
            text=sentence,
            input_tokens=len(prompt) // 4,
            output_tokens=len(sentence) // 4,
            finish_reason="stop",
        )

    def health(self) -> ModelHealth:
        return ModelHealth(healthy=True, detail="extractive stub, not an LLM")


def select_model(model_name: str | None = None):
    """Pick the model brick from the provider registry (see providers/registry.py):
    the configured default provider if it passes health, else the extractive stub.
    ponytail: health-checked per call, not cached — health is cheap by contract."""
    try:
        from providers.registry import ProviderRegistry

        model = ProviderRegistry.load().build(model=model_name) if model_name else ProviderRegistry.load().build()
        if model.health().healthy:
            return model
    except Exception:  # noqa: BLE001 — no config / unreachable provider -> stub
        pass
    return StubSummarizerModel()
